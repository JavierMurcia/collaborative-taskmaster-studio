from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

import pytest

from infrastructure.vertex import VertexModelGateway, VertexReadiness, VertexSettings
from studio.domain.errors import DomainError
from studio.ports.model_gateway import ModelRequest

SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {"question": {"type": "string"}},
    "required": ["question"],
}


@dataclass
class FakeUsage:
    prompt_token_count: int = 12
    candidates_token_count: int = 8
    total_token_count: int = 20


@dataclass
class FakeResponse:
    text: str
    response_id: str = "response-123"
    model_version: str = "gemini-3.5-flash-001"
    usage_metadata: FakeUsage | None = None


class FakeModels:
    def __init__(self, response: object | Exception) -> None:
        self.response = response
        self.calls: list[dict[str, Any]] = []

    def generate_content(
        self,
        *,
        model: str,
        contents: str,
        config: dict[str, Any],
    ) -> object:
        self.calls.append({"model": model, "contents": contents, "config": config})
        if isinstance(self.response, Exception):
            raise self.response
        return self.response


class FakeClient:
    def __init__(self, response: object | Exception) -> None:
        self.models = FakeModels(response)


def settings() -> VertexSettings:
    return VertexSettings(
        enabled=True,
        use_vertex_ai=True,
        project="sentinel-taskmaster-dev",
        location="global",
        model="gemini-3.5-flash",
        api_version="v1",
    )


def readiness(status: str = "ready") -> VertexReadiness:
    if status == "ready":
        return VertexReadiness(
            status="ready",
            configured=True,
            adc_available=True,
            project="sentinel-taskmaster-dev",
            location="global",
            model="gemini-3.5-flash",
            api_version="v1",
            credentials_source="application_default",
            message="ready",
        )
    return VertexReadiness(
        status="disabled",
        configured=False,
        adc_available=False,
        project=None,
        location="global",
        model="gemini-3.5-flash",
        api_version="v1",
        credentials_source="none",
        message="disabled",
    )


def request(schema: dict[str, Any] | None = None) -> ModelRequest:
    return ModelRequest(
        purpose="interview_question",
        system_instruction="Ask one concise question.",
        prompt="The workflow still needs a deadline.",
        response_schema=schema or SCHEMA,
        max_output_tokens=120,
        temperature=0.1,
    )


def timer(values: tuple[float, ...]) -> Any:
    remaining: Iterator[float] = iter(values)
    return lambda: next(remaining)


def test_gateway_generates_and_validates_structured_output() -> None:
    client = FakeClient(
        FakeResponse('{"question":"¿Cuál es la fecha límite?"}', usage_metadata=FakeUsage())
    )
    gateway = VertexModelGateway(
        settings(),
        readiness(),
        client_factory=lambda _: client,
        timer=timer((10.0, 10.125)),
    )

    result = gateway.generate_structured(request())

    assert result.payload == {"question": "¿Cuál es la fecha límite?"}
    assert result.metadata.provider == "vertex_ai"
    assert result.metadata.model == "gemini-3.5-flash"
    assert result.metadata.latency_ms == 125.0
    assert result.metadata.usage.total_tokens == 20
    assert result.metadata.response_id == "response-123"
    assert len(client.models.calls) == 1
    call = client.models.calls[0]
    assert call["model"] == "gemini-3.5-flash"
    assert call["contents"] == "The workflow still needs a deadline."
    assert call["config"] == {
        "candidate_count": 1,
        "max_output_tokens": 120,
        "response_mime_type": "application/json",
        "response_schema": SCHEMA,
        "system_instruction": "Ask one concise question.",
        "temperature": 0.1,
        "thinking_config": {"thinking_level": "MINIMAL"},
    }


def test_non_gemini_3_model_does_not_receive_thinking_level() -> None:
    client = FakeClient(FakeResponse('{"question":"Question"}'))
    legacy = settings().model_copy(update={"model": "gemini-2.5-flash"})
    gateway = VertexModelGateway(
        legacy,
        readiness(),
        client_factory=lambda _: client,
        timer=timer((0.0, 0.1)),
    )

    gateway.generate_structured(request())

    assert "thinking_config" not in client.models.calls[0]["config"]


def test_gemini_3_7_uses_only_supported_generation_controls() -> None:
    client = FakeClient(FakeResponse('{"question":"Question"}'))
    modern = settings().model_copy(update={"model": "gemini-3.7-flash"})
    gateway = VertexModelGateway(
        modern,
        readiness(),
        client_factory=lambda _: client,
        timer=timer((0.0, 0.1)),
    )

    gateway.generate_structured(request())

    config = client.models.calls[0]["config"]
    assert config["thinking_config"] == {"thinking_level": "LOW"}
    assert "temperature" not in config
    assert "candidate_count" not in config


def test_gateway_is_lazy_and_reuses_its_client() -> None:
    client = FakeClient(FakeResponse('{"question":"Question"}'))
    creations = 0

    def factory(_: VertexSettings) -> FakeClient:
        nonlocal creations
        creations += 1
        return client

    gateway = VertexModelGateway(
        settings(), readiness(), client_factory=factory, timer=timer((0.0, 0.1, 1.0, 1.1))
    )

    gateway.generate_structured(request())
    gateway.generate_structured(request())

    assert creations == 1
    assert len(client.models.calls) == 2


def test_gateway_rejects_invalid_schema_before_creating_client() -> None:
    created = False

    def forbidden_factory(_: VertexSettings) -> FakeClient:
        nonlocal created
        created = True
        raise AssertionError("The client must not be created")

    gateway = VertexModelGateway(settings(), readiness(), client_factory=forbidden_factory)

    with pytest.raises(DomainError) as captured:
        gateway.generate_structured(request({"type": "array"}))

    assert captured.value.code == "MODEL_SCHEMA_INVALID"
    assert created is False


def test_gateway_rejects_token_budget_before_creating_client() -> None:
    created = False

    def forbidden_factory(_: VertexSettings) -> FakeClient:
        nonlocal created
        created = True
        raise AssertionError("The client must not be created")

    limited = settings().model_copy(update={"max_model_output_tokens": 100})
    gateway = VertexModelGateway(
        limited,
        readiness(),
        client_factory=forbidden_factory,
    )

    with pytest.raises(DomainError) as captured:
        gateway.generate_structured(request())

    assert captured.value.code == "MODEL_TOKEN_LIMIT_EXCEEDED"
    assert captured.value.context == {
        "purpose": "interview_question",
        "requested_tokens": 120,
        "max_output_tokens": 100,
    }
    assert created is False


def test_gateway_refuses_calls_unless_vertex_is_ready() -> None:
    gateway = VertexModelGateway(settings(), readiness("disabled"))

    with pytest.raises(DomainError) as captured:
        gateway.generate_structured(request())

    assert captured.value.code == "MODEL_GATEWAY_UNAVAILABLE"


@pytest.mark.parametrize(
    ("response", "code"),
    [
        (FakeResponse("not json"), "MODEL_OUTPUT_INVALID"),
        (FakeResponse('{"unexpected":true}'), "MODEL_OUTPUT_INVALID"),
        (FakeResponse(""), "MODEL_EMPTY_RESPONSE"),
    ],
)
def test_gateway_rejects_untrusted_model_output(response: FakeResponse, code: str) -> None:
    client = FakeClient(response)
    gateway = VertexModelGateway(
        settings(), readiness(), client_factory=lambda _: client, timer=timer((0.0, 0.1))
    )

    with pytest.raises(DomainError) as captured:
        gateway.generate_structured(request())

    assert captured.value.code == code
    assert captured.value.context["provider"] == "vertex_ai"
    assert captured.value.context["model"] == "gemini-3.5-flash"
    assert captured.value.context["location"] == "global"
    assert captured.value.context["latency_ms"] == 100.0
    assert "prompt" not in captured.value.context


def test_gateway_normalizes_provider_errors_without_leaking_details() -> None:
    client = FakeClient(RuntimeError("secret token and prompt"))
    gateway = VertexModelGateway(
        settings(), readiness(), client_factory=lambda _: client, timer=timer((0.0, 0.125))
    )

    with pytest.raises(DomainError) as captured:
        gateway.generate_structured(request())

    assert captured.value.code == "MODEL_UNAVAILABLE"
    assert "secret" not in captured.value.message
    assert "prompt" not in captured.value.context
    assert captured.value.context == {
        "provider": "vertex_ai",
        "purpose": "interview_question",
        "model": "gemini-3.5-flash",
        "model_version": None,
        "location": "global",
        "response_id": None,
        "latency_ms": 125.0,
    }
    assert len(client.models.calls) == 1


def test_gateway_distinguishes_provider_timeout() -> None:
    client = FakeClient(TimeoutError("provider detail"))
    gateway = VertexModelGateway(
        settings(), readiness(), client_factory=lambda _: client, timer=timer((0.0, 0.25))
    )

    with pytest.raises(DomainError) as captured:
        gateway.generate_structured(request())

    assert captured.value.code == "MODEL_TIMEOUT"
    assert captured.value.context["model"] == "gemini-3.5-flash"
    assert captured.value.context["latency_ms"] == 250.0
