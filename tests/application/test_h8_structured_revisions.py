from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from infrastructure.local.clock import FrozenClock
from infrastructure.local.repositories import InMemoryRepository
from studio.application.design_service import DesignService
from studio.application.official_designer import OfficialAcademicDesigner
from studio.application.revision_generator import StructuredRevisionGenerator
from studio.application.specification_generator import StructuredSpecificationGenerator
from studio.domain.enums import ApprovalStatus, AuditEventType, ProjectState
from studio.domain.errors import DomainError
from studio.domain.models import Briefing, Project, TaskmasterSpecification
from studio.ports.model_gateway import (
    ModelGateway,
    ModelMetadata,
    ModelRequest,
    ModelResult,
    ModelUsage,
)

NOW = datetime(2026, 8, 13, 23, 0, tzinfo=UTC)
LATER = NOW + timedelta(minutes=15)
PROJECT_ID = "structured_revision_project"
OWNER = "demo_user"
FEEDBACK = (
    "No envíes información ni modifiques calendarios. Prepara el paquete, espera mi "
    "aprobación e incluye una prueba contra instrucciones maliciosas."
)


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
        confirmed_by=OWNER,
        confirmed_at=NOW,
    )


def source_specification() -> TaskmasterSpecification:
    return OfficialAcademicDesigner().initial_design(
        project_id=PROJECT_ID,
        briefing=briefing(),
        now=NOW,
    )


def proposal_payload(specification: TaskmasterSpecification) -> dict[str, Any]:
    payload = specification.model_dump(
        mode="json",
        by_alias=True,
        exclude={"schema_version", "revision", "approval", "metadata"},
    )
    payload["metadata"] = {
        "name": specification.metadata.name,
        "summary": specification.metadata.summary,
        "language": specification.metadata.language,
        "tags": specification.metadata.tags,
    }
    return payload


def revision_payload() -> dict[str, Any]:
    return proposal_payload(
        OfficialAcademicDesigner().revised_design(
            project_id=PROJECT_ID,
            briefing=briefing(),
            now=LATER,
        )
    )


def model_result(payload: dict[str, Any]) -> ModelResult:
    return ModelResult(
        payload=payload,
        metadata=ModelMetadata(
            provider="vertex_ai",
            model="gemini-3.5-flash",
            model_version="gemini-3.5-flash-001",
            location="global",
            response_id="revision-response-1",
            latency_ms=510.0,
            usage=ModelUsage(prompt_tokens=1800, output_tokens=1600, total_tokens=3400),
        ),
    )


def design_with_revision_gateway(
    gateway: RecordingGateway,
) -> tuple[InMemoryRepository, DesignService]:
    repository = InMemoryRepository(FrozenClock(NOW))
    repository.create(
        Project(
            id=PROJECT_ID,
            name="Revisor de entrega verificable",
            owner_session_id=OWNER,
            state=ProjectState.BRIEFING_CONFIRMED,
            briefing=briefing(),
        ),
        idempotency_key="create-revision-project",
    )
    DesignService(repository, repository, FrozenClock(NOW)).create_initial_revision(
        PROJECT_ID,
        owner_session_id=OWNER,
        idempotency_key="create-first-revision",
    )
    return repository, DesignService(
        repository,
        repository,
        FrozenClock(LATER),
        revision_generator=StructuredRevisionGenerator(gateway),
    )


def test_generator_applies_feedback_but_local_code_controls_revision_and_approval() -> None:
    gateway = RecordingGateway(model_result(revision_payload()))
    generated = StructuredRevisionGenerator(gateway).generate(
        source=source_specification(),
        feedback=FEEDBACK,
        now=LATER,
    )

    assert len(gateway.requests) == 1
    request = gateway.requests[0]
    assert request.purpose == "taskmaster_revision"
    assert request.max_output_tokens == 8192
    assert "revision" not in request.response_schema["properties"]
    assert "approval" not in request.response_schema["properties"]
    assert FEEDBACK in request.prompt
    assert "FEEDBACK_NO_CONFIABLE" in request.prompt
    assert "Conserva todas" in request.system_instruction
    revised = generated.specification
    assert revised.revision == 2
    assert revised.approval.status is ApprovalStatus.DRAFT
    assert revised.approval.decided_by is None
    assert revised.metadata.id == PROJECT_ID
    assert revised.metadata.created_at == NOW
    assert revised.metadata.updated_at == LATER
    assert revised.metadata.created_by == "gemini_vertex"


def test_model_revision_is_saved_audited_diffed_and_idempotent() -> None:
    gateway = RecordingGateway(model_result(revision_payload()))
    repository, design = design_with_revision_gateway(gateway)

    first = design.apply_feedback(
        PROJECT_ID,
        expected_revision=1,
        feedback=FEEDBACK,
        owner_session_id=OWNER,
        idempotency_key="model-revision",
    )
    replay = design.apply_feedback(
        PROJECT_ID,
        expected_revision=1,
        feedback=FEEDBACK,
        owner_session_id=OWNER,
        idempotency_key="model-revision",
    )

    assert len(gateway.requests) == 1
    assert replay.snapshot.version == first.snapshot.version
    assert first.revision.number == 2
    assert first.revision.specification.metadata.created_by == "gemini_vertex"
    assert first.diff is not None and first.diff.from_revision == 1
    events = repository.list_for_project(PROJECT_ID)
    completed = next(
        event
        for event in events
        if event.event_type is AuditEventType.MODEL_GENERATION_COMPLETED
        and event.details.get("operation") == "taskmaster_revision"
    )
    assert completed.details["provider"] == "vertex_ai"
    assert completed.details["model"] == "gemini-3.5-flash"
    assert completed.details["location"] == "global"
    assert completed.details["response_id"] == "revision-response-1"
    assert completed.details["latency_ms"] == 510.0
    assert completed.details["source_revision"] == 1
    assert completed.details["target_revision"] == 2
    assert completed.details["usage"]["total_tokens"] == 3400
    assert "feedback" not in completed.details
    revision_event = [
        event for event in events if event.event_type is AuditEventType.REVISION_CREATED
    ][-1]
    assert revision_event.actor_id == "gemini_vertex"


def test_policy_reduction_activates_safe_local_revision() -> None:
    payload = revision_payload()
    payload["policies"] = [
        policy for policy in payload["policies"] if policy["id"] != "simulation_only"
    ]
    gateway = RecordingGateway(model_result(payload))
    repository, design = design_with_revision_gateway(gateway)

    result = design.apply_feedback(
        PROJECT_ID,
        expected_revision=1,
        feedback=FEEDBACK,
        owner_session_id=OWNER,
        idempotency_key="policy-fallback",
    )

    assert result.revision.specification.metadata.created_by == "deterministic_designer"
    assert "simulation_only" in {
        policy.id for policy in result.revision.specification.policies
    }
    fallback = next(
        event
        for event in repository.list_for_project(PROJECT_ID)
        if event.event_type is AuditEventType.MODEL_FALLBACK_USED
    )
    assert fallback.details["operation"] == "taskmaster_revision"
    assert fallback.details["error_code"] == "SILENT_POLICY_REDUCTION"
    assert fallback.details["fallback"] == {
        "mode": "local",
        "operation": "taskmaster_revision",
        "strategy": "deterministic_reviewer",
        "reason_code": "SILENT_POLICY_REDUCTION",
        "category": "safety_rejection",
        "model_attempted": True,
        "retryable": False,
        "state_preserved": True,
    }


def test_provider_failure_falls_back_without_recording_feedback_or_provider_detail() -> None:
    gateway = RecordingGateway(DomainError("MODEL_UNAVAILABLE", "secret provider detail"))
    repository, design = design_with_revision_gateway(gateway)

    design.apply_feedback(
        PROJECT_ID,
        expected_revision=1,
        feedback=FEEDBACK,
        owner_session_id=OWNER,
        idempotency_key="provider-fallback",
    )

    fallback = next(
        event
        for event in repository.list_for_project(PROJECT_ID)
        if event.event_type is AuditEventType.MODEL_FALLBACK_USED
    )
    serialized = fallback.model_dump_json()
    assert fallback.details["error_code"] == "MODEL_UNAVAILABLE"
    assert "secret provider detail" not in serialized
    assert FEEDBACK not in serialized


def test_fallback_preserves_protected_policies_from_a_model_generated_source() -> None:
    repository = InMemoryRepository(FrozenClock(NOW))
    repository.create(
        Project(
            id=PROJECT_ID,
            name="Revisor con política propia",
            owner_session_id=OWNER,
            state=ProjectState.BRIEFING_CONFIRMED,
            briefing=briefing(),
        ),
        idempotency_key="create-custom-policy-project",
    )
    initial_payload = proposal_payload(source_specification())
    initial_payload["policies"].append(
        {
            "id": "retain_private_context",
            "name": "Conservar privacidad del contexto",
            "type": "data",
            "rule": "No exponer contenido de entradas en eventos de auditoría.",
            "effect": "Registrar solo metadatos y hashes.",
        }
    )
    initial_gateway = RecordingGateway(model_result(initial_payload))
    DesignService(
        repository,
        repository,
        FrozenClock(NOW),
        specification_generator=StructuredSpecificationGenerator(initial_gateway),
    ).create_initial_revision(
        PROJECT_ID,
        owner_session_id=OWNER,
        idempotency_key="create-model-source",
    )
    revision_gateway = RecordingGateway(
        DomainError("MODEL_UNAVAILABLE", "private provider information")
    )
    design = DesignService(
        repository,
        repository,
        FrozenClock(LATER),
        revision_generator=StructuredRevisionGenerator(revision_gateway),
    )

    result = design.apply_feedback(
        PROJECT_ID,
        expected_revision=1,
        feedback=FEEDBACK,
        owner_session_id=OWNER,
        idempotency_key="custom-policy-fallback",
    )

    assert result.revision.specification.metadata.created_by == "deterministic_reviewer"
    assert "retain_private_context" in {
        policy.id for policy in result.revision.specification.policies
    }
