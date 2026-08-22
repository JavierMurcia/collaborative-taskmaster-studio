from __future__ import annotations

import json
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

import pytest

from infrastructure.vertex import VertexModelGateway, VertexReadiness, VertexSettings
from studio.application.briefing_generator import StructuredBriefingGenerator
from studio.application.interview_catalog import QUESTION_BY_ID
from studio.application.interview_question_generator import StructuredInterviewQuestionGenerator
from studio.application.revision_generator import StructuredRevisionGenerator
from studio.application.specification_generator import StructuredSpecificationGenerator
from studio.domain.enums import ApprovalStatus
from studio.domain.errors import DomainError
from studio.domain.models import Briefing, TaskmasterSpecification

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
RECORDINGS = FIXTURES / "model_responses" / "recordings.json"
NOW = datetime(2026, 8, 13, 23, 30, tzinfo=UTC)


@dataclass(frozen=True)
class RecordedUsage:
    prompt_token_count: int
    candidates_token_count: int
    total_token_count: int


@dataclass(frozen=True)
class RecordedResponse:
    text: str
    response_id: str
    model_version: str
    usage_metadata: RecordedUsage


class RecordedModels:
    def __init__(self, response: RecordedResponse) -> None:
        self._response = response
        self.calls: list[dict[str, Any]] = []

    def generate_content(
        self,
        *,
        model: str,
        contents: str,
        config: dict[str, Any],
    ) -> object:
        self.calls.append({"model": model, "contents": contents, "config": config})
        return self._response


class RecordedClient:
    def __init__(self, response: RecordedResponse) -> None:
        self.models = RecordedModels(response)


def _catalog() -> dict[str, Any]:
    return cast(dict[str, Any], json.loads(RECORDINGS.read_text(encoding="utf-8")))


def _recording(recording_id: str) -> dict[str, Any]:
    matches = [item for item in _catalog()["recordings"] if item["id"] == recording_id]
    assert len(matches) == 1
    return cast(dict[str, Any], matches[0])


def _payload(recording: dict[str, Any]) -> dict[str, Any]:
    inline = recording.get("payload")
    if isinstance(inline, dict):
        return cast(dict[str, Any], inline)
    relative = recording["payload_fixture"]
    path = (RECORDINGS.parent / relative).resolve()
    assert path.is_relative_to(FIXTURES.resolve())
    source = cast(dict[str, Any], json.loads(path.read_text(encoding="utf-8")))
    assert recording.get("payload_projection") == "specification_proposal"
    proposal = {
        key: value
        for key, value in source.items()
        if key not in {"schema_version", "revision", "approval", "metadata"}
    }
    metadata = source["metadata"]
    proposal["metadata"] = {
        key: metadata[key] for key in ("name", "summary", "language", "tags")
    }
    return proposal


def _timer(latency_ms: float) -> Any:
    values: Iterator[float] = iter((10.0, 10.0 + latency_ms / 1_000))
    return lambda: next(values)


def _gateway(recording_id: str) -> tuple[VertexModelGateway, RecordedClient]:
    recording = _recording(recording_id)
    metadata = recording["metadata"]
    response = RecordedResponse(
        text=json.dumps(_payload(recording), ensure_ascii=False, sort_keys=True),
        response_id=metadata["response_id"],
        model_version=metadata["model_version"],
        usage_metadata=RecordedUsage(
            prompt_token_count=metadata["prompt_tokens"],
            candidates_token_count=metadata["output_tokens"],
            total_token_count=metadata["total_tokens"],
        ),
    )
    client = RecordedClient(response)
    settings = VertexSettings(
        enabled=True,
        use_vertex_ai=True,
        project="recorded-contract-project",
        location="global",
        model="gemini-3.5-flash",
    )
    readiness = VertexReadiness(
        status="ready",
        configured=True,
        adc_available=True,
        project=settings.project,
        location=settings.location,
        model=settings.model,
        api_version="v1",
        credentials_source="application_default",
        message="recorded test double",
    )
    return (
        VertexModelGateway(
            settings,
            readiness,
            client_factory=lambda _: client,
            timer=_timer(metadata["latency_ms"]),
        ),
        client,
    )


def _briefing() -> Briefing:
    return Briefing(
        problem="La entrega semanal se organiza manualmente.",
        goal="Preparar un paquete verificable antes del viernes.",
        deadline="Viernes 18:00",
        available_hours=6,
        input_format="Lista de requisitos",
        outputs=["Plan semanal", "Paquete requisito-evidencia"],
        external_actions="requires_clarification",
        approval_owner="Estudiante",
        success_criteria=["Cada requisito tiene evidencia"],
        confirmed=True,
        confirmed_by="demo_user",
        confirmed_at=NOW,
    )


def _source_specification() -> TaskmasterSpecification:
    payload = json.loads((FIXTURES / "academic_delivery_specification.json").read_text("utf-8"))
    specification = TaskmasterSpecification.model_validate(payload)
    return specification.model_copy(
        update={
            "metadata": specification.metadata.model_copy(
                update={"id": specification.metadata.source_project_id}
            )
        }
    )


def test_recording_catalog_is_versioned_unique_and_sanitized() -> None:
    catalog = _catalog()
    serialized = RECORDINGS.read_text(encoding="utf-8")
    ids = [item["id"] for item in catalog["recordings"]]

    assert catalog["schema_version"] == "1.0.0"
    assert catalog["sanitized"] is True
    assert catalog["source"] == "synthetic_vertex_recording"
    assert len(ids) == len(set(ids)) == 5
    for forbidden in (
        "AIza",
        "Bearer ",
        "BEGIN PRIVATE KEY",
        "GOOGLE_API_KEY",
        "GEMINI_API_KEY",
        "credentials",
    ):
        assert forbidden not in serialized


def test_recorded_question_passes_gateway_and_question_contract() -> None:
    gateway, client = _gateway("interview-question-valid-v1")
    generated = StructuredInterviewQuestionGenerator(gateway).generate(
        _briefing().model_copy(update={"deadline": None, "available_hours": None}),
        QUESTION_BY_ID["ask_deadline_and_hours"],
    )

    assert generated.question_id == "ask_deadline_and_hours"
    assert generated.target_fields == ["deadline", "available_hours"]
    assert generated.model_metadata.response_id == "recorded-question-001"
    assert generated.model_metadata.latency_ms == pytest.approx(118.0)
    assert len(client.models.calls) == 1


def test_recorded_briefing_values_pass_the_specific_extraction_contract() -> None:
    gateway, client = _gateway("briefing-deadline-valid-v1")
    generated = StructuredBriefingGenerator(gateway).generate(
        _briefing().model_copy(update={"deadline": None, "available_hours": None}),
        QUESTION_BY_ID["ask_deadline_and_hours"],
        "Debe estar el viernes a las 18:00 y tengo seis horas.",
    )

    assert generated.values == {"deadline": "Viernes 18:00", "available_hours": 6}
    assert generated.model_metadata.usage.total_tokens == 199
    assert len(client.models.calls) == 1


def test_recorded_specification_passes_all_local_contracts_as_draft() -> None:
    source = _source_specification()
    project_id = source.metadata.source_project_id
    gateway, client = _gateway("specification-valid-v1")
    generated = StructuredSpecificationGenerator(gateway).generate(
        project_id=project_id,
        project_name=source.metadata.name,
        briefing=_briefing(),
        now=NOW,
    )

    assert generated.specification.metadata.id == project_id
    assert generated.specification.revision == 1
    assert generated.specification.approval.status is ApprovalStatus.DRAFT
    assert generated.specification.metadata.created_by == "gemini_vertex"
    assert generated.model_metadata.response_id == "recorded-specification-001"
    assert len(client.models.calls) == 1


def test_recorded_revision_passes_contract_and_cannot_self_approve() -> None:
    source = _source_specification()
    gateway, client = _gateway("revision-valid-v1")
    generated = StructuredRevisionGenerator(gateway).generate(
        source=source,
        feedback="Conserva controles y mejora la explicación del flujo.",
        now=NOW,
    )

    assert generated.specification.revision == source.revision + 1
    assert generated.specification.approval.status is ApprovalStatus.DRAFT
    assert generated.specification.approval.decided_by is None
    assert generated.model_metadata.response_id == "recorded-revision-001"
    assert len(client.models.calls) == 1


def test_recorded_contract_drift_is_rejected_before_application_use() -> None:
    gateway, client = _gateway("interview-question-extra-field-invalid-v1")

    with pytest.raises(DomainError) as captured:
        StructuredInterviewQuestionGenerator(gateway).generate(
            _briefing(),
            QUESTION_BY_ID["ask_deadline_and_hours"],
        )

    assert captured.value.code == "MODEL_OUTPUT_INVALID"
    assert captured.value.context["response_id"] == "recorded-invalid-001"
    assert captured.value.context["latency_ms"] == pytest.approx(121.0)
    assert len(client.models.calls) == 1
