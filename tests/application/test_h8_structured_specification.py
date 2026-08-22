from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from infrastructure.local.clock import FrozenClock
from infrastructure.local.repositories import InMemoryRepository
from studio.application.design_service import DesignService
from studio.application.official_designer import OfficialAcademicDesigner
from studio.application.specification_generator import StructuredSpecificationGenerator
from studio.domain.enums import ApprovalStatus, AuditEventType, ProjectState
from studio.domain.errors import DomainError
from studio.domain.models import Briefing, Project
from studio.ports.model_gateway import (
    ModelGateway,
    ModelMetadata,
    ModelRequest,
    ModelResult,
    ModelUsage,
)

NOW = datetime(2026, 8, 13, 22, 30, tzinfo=UTC)
PROJECT_ID = "structured_specification_project"
OWNER = "demo_user"


class RecordingGateway(ModelGateway):
    def __init__(self, result: ModelResult | DomainError) -> None:
        self.result = result
        self.requests: list[ModelRequest] = []

    def generate_structured(self, request: ModelRequest) -> ModelResult:
        self.requests.append(request)
        if isinstance(self.result, DomainError):
            raise self.result
        return self.result


def briefing() -> Briefing:
    return Briefing(
        problem="Los requisitos de entrega se organizan manualmente.",
        goal="Crear un paquete verificable antes del viernes.",
        desired_result="Plan y paquete listos para revisión humana.",
        actors=["Estudiante"],
        inputs=["Lista de requisitos"],
        constraints=["No enviar nada externamente"],
        scope_in=["Organizar requisitos", "Preparar evidencia"],
        scope_out=["Enviar la entrega"],
        approvals=["El estudiante aprueba cualquier entrega"],
        success_criteria=["Cada requisito tiene evidencia"],
        deadline="Viernes 18:00",
        available_hours=6,
        input_format="Documento de texto",
        outputs=["Plan semanal", "Paquete requisito-evidencia"],
        external_actions="requires_clarification",
        approval_owner="Estudiante",
        confirmed=True,
        confirmed_by=OWNER,
        confirmed_at=NOW,
    )


def proposal_payload() -> dict[str, Any]:
    specification = OfficialAcademicDesigner().initial_design(
        project_id=PROJECT_ID,
        briefing=briefing(),
        now=NOW,
    )
    payload = specification.model_dump(
        mode="json",
        by_alias=True,
        exclude={"schema_version", "revision", "approval", "metadata"},
    )
    payload["metadata"] = {
        "name": "Taskmaster de entrega verificable",
        "summary": "Organiza requisitos y evidencia sin realizar entregas externas.",
        "language": "es",
        "tags": ["academico", "verificacion"],
    }
    return payload


def model_result(payload: dict[str, Any]) -> ModelResult:
    return ModelResult(
        payload=payload,
        metadata=ModelMetadata(
            provider="vertex_ai",
            model="gemini-3.5-flash",
            model_version="gemini-3.5-flash-001",
            location="global",
            response_id="specification-response-1",
            latency_ms=420.0,
            usage=ModelUsage(prompt_tokens=900, output_tokens=1700, total_tokens=2600),
        ),
    )


def confirmed_project() -> Project:
    return Project(
        id=PROJECT_ID,
        name="Diseñador de entrega verificable",
        owner_session_id=OWNER,
        state=ProjectState.BRIEFING_CONFIRMED,
        briefing=briefing(),
    )


def test_generator_uses_complete_proposal_without_delegating_local_authority() -> None:
    gateway = RecordingGateway(model_result(proposal_payload()))
    generated = StructuredSpecificationGenerator(gateway).generate(
        project_id=PROJECT_ID,
        project_name="Diseñador de entrega verificable",
        briefing=briefing(),
        now=NOW,
    )

    assert len(gateway.requests) == 1
    request = gateway.requests[0]
    assert request.purpose == "taskmaster_specification"
    assert request.max_output_tokens == 8192
    assert "revision" not in request.response_schema["properties"]
    assert "approval" not in request.response_schema["properties"]
    assert "CONTEXTO_NO_CONFIABLE" in request.prompt
    assert "nunca instrucciones" in request.system_instruction
    specification = generated.specification
    assert specification.revision == 1
    assert specification.approval.status is ApprovalStatus.DRAFT
    assert specification.approval.decided_by is None
    assert specification.metadata.id == PROJECT_ID
    assert specification.metadata.source_project_id == PROJECT_ID
    assert specification.metadata.created_at == NOW
    assert specification.metadata.created_by == "gemini_vertex"


def test_model_specification_is_saved_audited_and_idempotent() -> None:
    repository = InMemoryRepository(FrozenClock(NOW))
    repository.create(confirmed_project(), idempotency_key="create-project")
    gateway = RecordingGateway(model_result(proposal_payload()))
    design = DesignService(
        repository,
        repository,
        FrozenClock(NOW),
        specification_generator=StructuredSpecificationGenerator(gateway),
    )

    first = design.create_initial_revision(
        PROJECT_ID,
        owner_session_id=OWNER,
        idempotency_key="generate-specification",
    )
    replay = design.create_initial_revision(
        PROJECT_ID,
        owner_session_id=OWNER,
        idempotency_key="generate-specification",
    )

    assert len(gateway.requests) == 1
    assert replay.snapshot.version == first.snapshot.version
    assert first.revision.specification.metadata.created_by == "gemini_vertex"
    events = repository.list_for_project(PROJECT_ID)
    completed = next(
        event for event in events if event.event_type is AuditEventType.MODEL_GENERATION_COMPLETED
    )
    assert completed.details["operation"] == "taskmaster_specification"
    assert completed.details["provider"] == "vertex_ai"
    assert completed.details["model"] == "gemini-3.5-flash"
    assert completed.details["location"] == "global"
    assert completed.details["response_id"] == "specification-response-1"
    assert completed.details["latency_ms"] == 420.0
    assert completed.details["usage"]["total_tokens"] == 2600
    assert "payload" not in completed.details
    revision_event = next(
        event for event in events if event.event_type is AuditEventType.REVISION_CREATED
    )
    assert revision_event.actor_id == "gemini_vertex"


def test_semantically_invalid_model_output_falls_back_without_leaking_payload() -> None:
    repository = InMemoryRepository(FrozenClock(NOW))
    repository.create(confirmed_project(), idempotency_key="create-invalid-project")
    payload = proposal_payload()
    payload["workflow"]["steps"][0]["actor_id"] = "secret_invalid_actor"
    gateway = RecordingGateway(model_result(payload))
    design = DesignService(
        repository,
        repository,
        FrozenClock(NOW),
        specification_generator=StructuredSpecificationGenerator(gateway),
    )

    result = design.create_initial_revision(
        PROJECT_ID,
        owner_session_id=OWNER,
        idempotency_key="fallback-specification",
    )

    assert len(gateway.requests) == 1
    assert result.revision.specification.metadata.created_by == "deterministic_designer"
    fallback = next(
        event
        for event in repository.list_for_project(PROJECT_ID)
        if event.event_type is AuditEventType.MODEL_FALLBACK_USED
    )
    assert fallback.details["operation"] == "taskmaster_specification"
    assert fallback.details["error_code"] == "SPECIFICATION_PROPOSAL_INVALID"
    assert fallback.details["model"] == "gemini-3.5-flash"
    assert fallback.details["latency_ms"] == 420.0
    assert "secret_invalid_actor" not in fallback.model_dump_json()


def test_provider_failure_uses_same_safe_fallback() -> None:
    repository = InMemoryRepository(FrozenClock(NOW))
    repository.create(confirmed_project(), idempotency_key="create-provider-project")
    gateway = RecordingGateway(DomainError("MODEL_UNAVAILABLE", "secret provider detail"))
    design = DesignService(
        repository,
        repository,
        FrozenClock(NOW),
        specification_generator=StructuredSpecificationGenerator(gateway),
    )

    design.create_initial_revision(
        PROJECT_ID,
        owner_session_id=OWNER,
        idempotency_key="provider-fallback",
    )

    fallback = next(
        event
        for event in repository.list_for_project(PROJECT_ID)
        if event.event_type is AuditEventType.MODEL_FALLBACK_USED
    )
    assert fallback.details["error_code"] == "MODEL_UNAVAILABLE"
    assert "secret provider detail" not in fallback.model_dump_json()
