"""Google Gen AI SDK adapter with a fail-closed structured output boundary."""

from __future__ import annotations

import base64
import json
import re
from collections.abc import Callable
from time import perf_counter
from typing import Any, Protocol, cast

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError, ValidationError

from infrastructure.vertex.config import VertexReadiness, VertexSettings
from studio.domain.errors import DomainError
from studio.ports.model_gateway import (
    ModelMetadata,
    ModelRequest,
    ModelResult,
    ModelUsage,
)


class ModelsClient(Protocol):
    def generate_content(
        self,
        *,
        model: str,
        contents: str,
        config: dict[str, Any],
    ) -> object: ...


class GenAIClient(Protocol):
    @property
    def models(self) -> ModelsClient: ...


ClientFactory = Callable[[VertexSettings], GenAIClient]
Timer = Callable[[], float]


class VertexModelGateway:
    """Single, auditable boundary for structured Gemini requests."""

    def __init__(
        self,
        settings: VertexSettings,
        readiness: VertexReadiness,
        *,
        client_factory: ClientFactory | None = None,
        timer: Timer = perf_counter,
    ) -> None:
        self._settings = settings
        self._readiness = readiness
        self._client_factory = client_factory or _official_client
        self._timer = timer
        self._client: GenAIClient | None = None

    def generate_structured(self, request: ModelRequest) -> ModelResult:
        self._ensure_ready()
        validator = _validator(request.response_schema)
        if request.max_output_tokens > self._settings.max_model_output_tokens:
            raise DomainError(
                "MODEL_TOKEN_LIMIT_EXCEEDED",
                "La solicitud supera el límite local de tokens de salida.",
                context={
                    "purpose": request.purpose,
                    "requested_tokens": request.max_output_tokens,
                    "max_output_tokens": self._settings.max_model_output_tokens,
                },
            )
        client = self._client_instance()
        config: dict[str, Any] = {
            "max_output_tokens": request.max_output_tokens,
            "response_mime_type": "application/json",
            "response_schema": request.response_schema,
            "system_instruction": request.system_instruction,
        }
        if _uses_modern_generation_config(self._settings.model):
            config["thinking_config"] = {"thinking_level": "LOW"}
        else:
            config["candidate_count"] = 1
            config["temperature"] = request.temperature
        if self._settings.model.startswith("gemini-3") and not _uses_modern_generation_config(
            self._settings.model
        ):
            config["thinking_config"] = {"thinking_level": "MINIMAL"}
        started = self._timer()
        contents: object = request.prompt
        if request.media:
            try:
                from google.genai import types
            except ModuleNotFoundError as error:
                raise DomainError(
                    "MODEL_SDK_UNAVAILABLE",
                    'Instala el extra ".[vertex]" para utilizar imágenes con Vertex AI.',
                ) from error
            contents = [
                request.prompt,
                *(
                    types.Part.from_bytes(
                        data=base64.b64decode(item.data_base64, validate=True),
                        mime_type=item.mime_type,
                    )
                    for item in request.media
                ),
            ]
        try:
            response = client.models.generate_content(
                model=self._settings.model,
                contents=contents,
                config=config,
            )
        except TimeoutError as error:
            latency_ms = self._elapsed_ms(started)
            raise DomainError(
                "MODEL_TIMEOUT",
                "Vertex AI no respondió dentro del tiempo disponible.",
                context=self._attempt_details(request, latency_ms),
            ) from error
        except Exception as error:
            latency_ms = self._elapsed_ms(started)
            raise DomainError(
                "MODEL_UNAVAILABLE",
                "Vertex AI no pudo completar la solicitud estructurada.",
                context=self._attempt_details(request, latency_ms),
            ) from error
        latency_ms = self._elapsed_ms(started)
        try:
            payload = _payload(response)
        except DomainError as error:
            raise DomainError(
                error.code,
                error.message,
                context={**error.context, **self._attempt_details(request, latency_ms, response)},
            ) from error
        try:
            validator.validate(payload)
        except ValidationError as error:
            path = "/" + "/".join(str(item) for item in error.absolute_path)
            raise DomainError(
                "MODEL_OUTPUT_INVALID",
                "Vertex AI devolvió una respuesta que no cumple el contrato.",
                context={
                    "path": path,
                    **self._attempt_details(request, latency_ms, response),
                },
            ) from error
        return ModelResult(
            payload=payload,
            metadata=_metadata(response, self._settings, latency_ms),
        )

    def tool_client(self) -> GenAIClient:
        """Return the same authenticated client for explicitly governed Gemini tools."""

        self._ensure_ready()
        return self._client_instance()

    def _elapsed_ms(self, started: float) -> float:
        return max(0.0, (self._timer() - started) * 1_000)

    def _attempt_details(
        self,
        request: ModelRequest,
        latency_ms: float,
        response: object | None = None,
    ) -> dict[str, Any]:
        return {
            "provider": "vertex_ai",
            "purpose": request.purpose,
            "model": self._settings.model,
            "model_version": _string_attribute(response, "model_version"),
            "location": self._settings.location,
            "response_id": _string_attribute(response, "response_id"),
            "latency_ms": latency_ms,
        }

    def _ensure_ready(self) -> None:
        if not self._settings.enabled or self._readiness.status != "ready":
            raise DomainError(
                "MODEL_GATEWAY_UNAVAILABLE",
                "El gateway de Vertex AI no está habilitado y preparado.",
                context={"status": self._readiness.status},
            )

    def _client_instance(self) -> GenAIClient:
        if self._client is None:
            self._client = self._client_factory(self._settings)
        return self._client


def _uses_modern_generation_config(model: str) -> bool:
    """Gemini 3.6+ rejects sampling and candidate-count parameters."""

    match = re.match(r"^gemini-(\d+)\.(\d+)", model)
    if match is None:
        return False
    major, minor = (int(value) for value in match.groups())
    return major > 3 or (major == 3 and minor >= 6)


def _official_client(settings: VertexSettings) -> GenAIClient:
    try:
        import truststore
        from google import genai
        from google.genai import types
    except ModuleNotFoundError as error:
        raise DomainError(
            "MODEL_SDK_UNAVAILABLE",
            'Instala el extra ".[vertex]" para utilizar Vertex AI.',
        ) from error
    # Respect the operating system trust store. This is required on managed Windows
    # environments and keeps TLS verification enabled.
    truststore.inject_into_ssl()
    return cast(
        GenAIClient,
        genai.Client(
            vertexai=True,
            project=settings.project,
            location=settings.location,
            http_options=types.HttpOptions(api_version=settings.api_version),
        ),
    )


def _validator(schema: dict[str, Any]) -> Draft202012Validator:
    try:
        Draft202012Validator.check_schema(schema)
    except SchemaError as error:
        raise DomainError(
            "MODEL_SCHEMA_INVALID",
            "El esquema de salida solicitado no es JSON Schema válido.",
        ) from error
    if schema.get("type") != "object":
        raise DomainError(
            "MODEL_SCHEMA_INVALID",
            "El esquema de salida debe declarar un objeto en el nivel superior.",
        )
    return Draft202012Validator(schema)


def _payload(response: object) -> dict[str, Any]:
    parsed_response = getattr(response, "parsed", None)
    if isinstance(parsed_response, dict):
        return cast(dict[str, Any], parsed_response)
    model_dump = getattr(parsed_response, "model_dump", None)
    if callable(model_dump):
        dumped = model_dump(mode="json")
        if isinstance(dumped, dict):
            return cast(dict[str, Any], dumped)
    text = getattr(response, "text", None)
    if not isinstance(text, str) or not text.strip():
        raise DomainError("MODEL_EMPTY_RESPONSE", "Vertex AI no devolvió contenido utilizable.")
    normalized = text.strip()
    fenced = re.fullmatch(r"```(?:json)?\s*(\{.*\})\s*```", normalized, flags=re.DOTALL)
    if fenced is not None:
        normalized = fenced.group(1)
    try:
        parsed = json.loads(normalized)
    except json.JSONDecodeError as error:
        raise DomainError(
            "MODEL_OUTPUT_INVALID",
            "Vertex AI devolvió contenido que no es JSON válido.",
        ) from error
    if not isinstance(parsed, dict):
        raise DomainError(
            "MODEL_OUTPUT_INVALID",
            "Vertex AI devolvió una raíz JSON que no es un objeto.",
        )
    return cast(dict[str, Any], parsed)


def _metadata(response: object, settings: VertexSettings, latency_ms: float) -> ModelMetadata:
    usage = getattr(response, "usage_metadata", None)
    return ModelMetadata(
        provider="vertex_ai",
        model=settings.model,
        model_version=_string_attribute(response, "model_version"),
        location=settings.location,
        response_id=_string_attribute(response, "response_id"),
        latency_ms=latency_ms,
        usage=ModelUsage(
            prompt_tokens=_integer_attribute(usage, "prompt_token_count"),
            output_tokens=_integer_attribute(usage, "candidates_token_count"),
            total_tokens=_integer_attribute(usage, "total_token_count"),
        ),
    )


def _string_attribute(value: object, name: str) -> str | None:
    candidate = getattr(value, name, None)
    return candidate if isinstance(candidate, str) and candidate else None


def _integer_attribute(value: object, name: str) -> int | None:
    candidate = getattr(value, name, None)
    return candidate if isinstance(candidate, int) and candidate >= 0 else None
