from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest

from infrastructure.local.clock import FrozenClock
from infrastructure.local.repositories import InMemoryRepository, JsonLocalRepository
from studio.application.approval_service import ApprovalService, require_approved_revision
from studio.application.design_service import DesignService, ensure_protected_policies_preserved
from studio.domain.enums import ApprovalStatus, AuditEventType, ProjectState
from studio.domain.errors import DomainError, RevisionImmutableError
from studio.domain.models import Briefing, Project
from studio.domain.validation import validate_specification

PROJECT_ID = "academic_delivery_project"
OWNER = "demo_user"
NOW = datetime.fromisoformat("2026-08-13T16:25:00-05:00")
APPROVAL_TIME = datetime.fromisoformat("2026-08-13T17:00:00-05:00")
OFFICIAL_FEEDBACK = (
    "No quiero que el agente envíe nada ni modifique calendarios. Solo debe preparar el "
    "paquete y esperar mi aprobación. También quiero una prueba que compruebe que una "
    "instrucción dentro de los requisitos no pueda saltarse esta regla."
)


def confirmed_project() -> Project:
    return Project(
        id=PROJECT_ID,
        name="Coordinador de entrega académica",
        owner_session_id=OWNER,
        state=ProjectState.BRIEFING_CONFIRMED,
        briefing=Briefing(
            problem=(
                "Los requisitos semanales se organizan manualmente y pueden quedar sin "
                "actividad o evidencia."
            ),
            goal=(
                "Crear un plan semanal dentro de seis horas y preparar un paquete que "
                "relacione cada requisito con su evidencia."
            ),
            deadline="Viernes 18:00",
            available_hours=6,
            input_format="Lista escrita por el estudiante",
            outputs=["Plan semanal", "Paquete requisito-evidencia"],
            external_actions="requires_clarification",
            approval_owner="Estudiante",
            success_criteria=[
                "Cada requisito tiene una actividad",
                "Cada requisito tiene evidencia",
                "El estudiante aprueba el paquete",
            ],
            confirmed=True,
            confirmed_by=OWNER,
            confirmed_at=NOW,
        ),
    )


def setup_design(
    repository: InMemoryRepository,
) -> tuple[DesignService, ApprovalService]:
    repository.create(confirmed_project(), idempotency_key="create-confirmed")
    clock = FrozenClock(NOW)
    return (
        DesignService(repository, repository, clock),
        ApprovalService(repository, repository, clock),
    )


def create_two_revisions(design: DesignService) -> None:
    design.create_initial_revision(
        PROJECT_ID,
        owner_session_id=OWNER,
        idempotency_key="design-one",
    )
    design.apply_feedback(
        PROJECT_ID,
        expected_revision=1,
        feedback=OFFICIAL_FEEDBACK,
        owner_session_id=OWNER,
        idempotency_key="feedback-official",
    )


def test_initial_design_is_valid_draft_with_official_broad_actions() -> None:
    repository = InMemoryRepository(FrozenClock(NOW))
    design, _ = setup_design(repository)
    result = design.create_initial_revision(
        PROJECT_ID,
        owner_session_id=OWNER,
        idempotency_key="design-one",
    )
    specification = result.revision.specification
    validation = validate_specification(
        specification.model_dump(mode="json", by_alias=True),
        active_project_id=PROJECT_ID,
    )
    assert validation.valid
    assert not validation.capabilities.can_generate
    assert result.snapshot.project.state is ProjectState.DESIGN_IN_REVIEW
    assert result.snapshot.project.active_revision == 1
    assert specification.approval.status is ApprovalStatus.DRAFT
    assert {tool.id for tool in specification.tools} == {
        "save_weekly_plan",
        "create_calendar_blocks",
        "verify_evidence_coverage",
        "prepare_review_package",
        "send_review_package",
    }


def test_initial_design_uses_the_available_time_from_the_briefing() -> None:
    repository = InMemoryRepository(FrozenClock(NOW))
    project = confirmed_project()
    project.briefing.available_hours = 1
    repository.create(project, idempotency_key="create-one-hour-project")
    design = DesignService(repository, repository, FrozenClock(NOW))

    result = design.create_initial_revision(
        PROJECT_ID,
        owner_session_id=OWNER,
        idempotency_key="design-one-hour",
    )
    planning_step = next(
        step
        for step in result.revision.specification.workflow.steps
        if step.id == "build_weekly_plan"
    )

    assert planning_step.name == "Crear plan de 1 hora"
    assert planning_step.description == "Distribuir actividades sin superar 1 hora."


def test_local_designer_adapts_to_a_non_academic_legal_workflow() -> None:
    repository = InMemoryRepository(FrozenClock(NOW))
    project_id = "legal_documents_project"
    project = Project(
        id=project_id,
        name="Documentos legales",
        owner_session_id=OWNER,
        state=ProjectState.BRIEFING_CONFIRMED,
        briefing=Briefing(
            problem="Clientes de distintas profesiones necesitan documentos legales coherentes.",
            goal="Preparar documentos legales personalizados para revisión.",
            desired_result="Borrador legal verificable",
            deadline="Viernes",
            available_hours=4,
            input_format="Formulario con datos confirmados del cliente",
            outputs=["Borrador legal"],
            external_actions="none",
            approval_owner="Abogado responsable",
            success_criteria=["Usar únicamente datos confirmados"],
            confirmed=True,
            confirmed_by=OWNER,
            confirmed_at=NOW,
        ),
    )
    repository.create(project, idempotency_key="create-legal-design")
    result = DesignService(repository, repository, FrozenClock(NOW)).create_initial_revision(
        project_id,
        owner_session_id=OWNER,
        idempotency_key="design-legal",
    )

    specification = result.revision.specification
    assert [step.id for step in specification.workflow.steps] == [
        "validate_inputs",
        "prepare_result",
        "verify_result",
        "human_review",
        "completed",
        "stopped_safely",
    ]
    assert {tool.id for tool in specification.tools} == {
        "prepare_work_result",
        "verify_work_result",
    }
    serialized = specification.model_dump_json().casefold()
    assert "plan semanal" not in serialized
    assert "calendario" not in serialized
    assert "estudiante" not in serialized


def test_official_feedback_creates_revision_two_and_preserves_revision_one() -> None:
    repository = InMemoryRepository(FrozenClock(NOW))
    design, _ = setup_design(repository)
    first = design.create_initial_revision(
        PROJECT_ID,
        owner_session_id=OWNER,
        idempotency_key="design-one",
    )
    result = design.apply_feedback(
        PROJECT_ID,
        expected_revision=1,
        feedback=OFFICIAL_FEEDBACK,
        owner_session_id=OWNER,
        idempotency_key="feedback-official",
    )
    assert [revision.number for revision in result.snapshot.revisions] == [1, 2]
    assert first.revision.model_dump() == result.snapshot.revisions[0].model_dump()
    assert result.snapshot.project.active_revision == 2
    second = result.revision.specification
    assert second.approval.status is ApprovalStatus.DRAFT
    assert {tool.id for tool in second.tools} == {
        "save_weekly_plan",
        "verify_evidence_coverage",
        "prepare_review_package",
    }
    assert "deny_external_delivery" in {policy.id for policy in second.policies}
    assert "malicious_requirement" in {scenario.id for scenario in second.test_scenarios}
    assert "completed_after_approval" in second.workflow.terminal_states


def test_structural_diff_matches_the_official_feedback() -> None:
    repository = InMemoryRepository(FrozenClock(NOW))
    design, _ = setup_design(repository)
    create_two_revisions(design)
    diff = design.get_diff(
        PROJECT_ID,
        from_revision=1,
        to_revision=2,
        owner_session_id=OWNER,
    )
    removed_tools = {item.identifier for item in diff.removed if item.category == "tool"}
    added_policies = {item.identifier for item in diff.added if item.category == "policy"}
    added_scenarios = {item.identifier for item in diff.added if item.category == "test_scenario"}
    removed_scenarios = {
        item.identifier for item in diff.removed if item.category == "test_scenario"
    }
    assert removed_tools == {"create_calendar_blocks", "send_review_package"}
    assert added_policies == {"deny_external_delivery"}
    assert added_scenarios == {"malicious_requirement"}
    assert removed_scenarios == {"generic_security_check"}
    assert any(
        item.category == "policy" and item.identifier == "require_final_approval"
        for item in diff.modified
    )


def test_protected_policy_cannot_be_removed_silently() -> None:
    repository = InMemoryRepository(FrozenClock(NOW))
    design, _ = setup_design(repository)
    create_two_revisions(design)
    snapshot = repository.get(PROJECT_ID)
    first, second = snapshot.revisions
    weakened = second.specification.model_copy(
        update={
            "policies": [
                policy for policy in second.specification.policies if policy.id != "simulation_only"
            ]
        },
        deep=True,
    )
    with pytest.raises(DomainError) as captured:
        ensure_protected_policies_preserved(first.specification, weakened)
    assert captured.value.code == "SILENT_POLICY_REDUCTION"


def test_design_overview_exposes_steps_tools_risks_and_verification() -> None:
    repository = InMemoryRepository(FrozenClock(NOW))
    design, _ = setup_design(repository)
    create_two_revisions(design)
    overview = design.get_overview(
        PROJECT_ID,
        revision=2,
        owner_session_id=OWNER,
    )
    assert overview.approval_status == "draft"
    assert any(step.name == "Esperar aprobación humana" for step in overview.steps)
    assert all(tool.mode == "simulated" for tool in overview.tools)
    assert "Prohibir entrega externa" in overview.policies
    assert overview.verification_criteria


def test_agent_or_model_cannot_approve() -> None:
    repository = InMemoryRepository(FrozenClock(NOW))
    design, approval = setup_design(repository)
    create_two_revisions(design)
    with pytest.raises(DomainError) as captured:
        approval.decide(
            PROJECT_ID,
            revision=2,
            decision="approved",
            actor_id="gemini_designer",
            actor_type="agent",
            note="Model selected its own output.",
            approval_id="invalid_model_approval",
            owner_session_id=OWNER,
            idempotency_key="model-approval",
        )
    assert captured.value.code == "HUMAN_APPROVAL_REQUIRED"
    assert repository.get(PROJECT_ID).approvals == ()


def test_generation_is_blocked_without_approval() -> None:
    repository = InMemoryRepository(FrozenClock(NOW))
    design, _ = setup_design(repository)
    create_two_revisions(design)
    with pytest.raises(DomainError) as captured:
        require_approved_revision(repository.get(PROJECT_ID))
    assert captured.value.code == "GENERATION_REQUIRES_APPROVAL"


def test_feedback_replay_is_idempotent_and_different_feedback_is_rejected() -> None:
    repository = InMemoryRepository(FrozenClock(NOW))
    design, _ = setup_design(repository)
    design.create_initial_revision(
        PROJECT_ID,
        owner_session_id=OWNER,
        idempotency_key="design-one",
    )
    first = design.apply_feedback(
        PROJECT_ID,
        expected_revision=1,
        feedback=OFFICIAL_FEEDBACK,
        owner_session_id=OWNER,
        idempotency_key="feedback-official",
    )
    replay = design.apply_feedback(
        PROJECT_ID,
        expected_revision=1,
        feedback=OFFICIAL_FEEDBACK,
        owner_session_id=OWNER,
        idempotency_key="feedback-official",
    )
    assert replay.snapshot.version == first.snapshot.version
    assert len(replay.snapshot.revisions) == 2
    with pytest.raises(DomainError) as captured:
        design.apply_feedback(
            PROJECT_ID,
            expected_revision=1,
            feedback="Un feedback diferente.",
            owner_session_id=OWNER,
            idempotency_key="feedback-different",
        )
    assert captured.value.code == "DESIGN_ALREADY_REVISED"


def test_human_approval_unlocks_generation_and_freezes_revision() -> None:
    repository = InMemoryRepository(FrozenClock(NOW))
    design, _ = setup_design(repository)
    create_two_revisions(design)
    approval = ApprovalService(repository, repository, FrozenClock(APPROVAL_TIME))
    approved = approval.decide(
        PROJECT_ID,
        revision=2,
        decision="approved",
        actor_id=OWNER,
        actor_type="human",
        note=(
            "Aprobado después de eliminar calendarios y envíos y añadir la prueba de "
            "prompt injection."
        ),
        approval_id="academic_delivery_revision_2_approval",
        owner_session_id=OWNER,
        idempotency_key="approve-revision-two",
    )
    assert approved.project.state is ProjectState.DESIGN_APPROVED
    assert require_approved_revision(approved).approval.status is ApprovalStatus.APPROVED
    frozen = approved.revisions[1]
    with pytest.raises(RevisionImmutableError):
        frozen.replace_specification(frozen.specification)
    assert approved.approvals[0].approval.decided_at == APPROVAL_TIME
    assert (
        repository.list_for_project(PROJECT_ID)[-1].event_type is AuditEventType.REVISION_APPROVED
    )
    replay = approval.decide(
        PROJECT_ID,
        revision=2,
        decision="approved",
        actor_id=OWNER,
        actor_type="human",
        note=(
            "Aprobado después de eliminar calendarios y envíos y añadir la prueba de "
            "prompt injection."
        ),
        approval_id="academic_delivery_revision_2_approval",
        owner_session_id=OWNER,
        idempotency_key="approve-revision-two",
    )
    assert replay.version == approved.version
    assert len(replay.approvals) == 1


def test_rejection_does_not_unlock_generation() -> None:
    repository = InMemoryRepository(FrozenClock(NOW))
    design, approval = setup_design(repository)
    create_two_revisions(design)
    rejected = approval.decide(
        PROJECT_ID,
        revision=2,
        decision="rejected",
        actor_id=OWNER,
        actor_type="human",
        note="Necesita cambios.",
        approval_id="academic_delivery_revision_2_rejection",
        owner_session_id=OWNER,
        idempotency_key="reject-revision-two",
    )
    assert rejected.project.state is ProjectState.DESIGN_IN_REVIEW
    with pytest.raises(DomainError):
        require_approved_revision(rejected)


def test_json_repository_restores_both_revisions_and_approval(tmp_path: Path) -> None:
    data = tmp_path / "studio-data"
    repository = JsonLocalRepository(data, FrozenClock(NOW))
    design, approval = setup_design(repository)
    create_two_revisions(design)
    approval.decide(
        PROJECT_ID,
        revision=2,
        decision="approved",
        actor_id=OWNER,
        actor_type="human",
        note="Aprobado.",
        approval_id="academic_delivery_revision_2_approval",
        owner_session_id=OWNER,
        idempotency_key="approve-revision-two",
    )
    restarted = JsonLocalRepository(data, FrozenClock(APPROVAL_TIME))
    restored = restarted.get(PROJECT_ID)
    assert [revision.number for revision in restored.revisions] == [1, 2]
    assert restored.revisions[1].specification.approval.status is ApprovalStatus.APPROVED
    assert len(restored.approvals) == 1
