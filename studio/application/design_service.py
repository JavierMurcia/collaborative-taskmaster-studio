"""Design, feedback, revision comparison, and policy-preservation use cases."""

from __future__ import annotations

import hashlib
import re
from collections.abc import Iterable
from datetime import datetime
from typing import Any

from studio.application.design_models import (
    DesignOverview,
    DesignResult,
    DiffItem,
    StepOverview,
    StructuralDiff,
    ToolOverview,
)
from studio.application.fallback_policy import (
    decide_local_fallback,
    fallback_event_details,
)
from studio.application.interview_service import require_confirmed_briefing
from studio.application.official_designer import OfficialAcademicDesigner
from studio.application.project_service import ProjectService
from studio.application.revision_generator import (
    GeneratedRevision,
    StructuredRevisionGenerator,
)
from studio.application.specification_generator import (
    GeneratedSpecification,
    StructuredSpecificationGenerator,
)
from studio.domain.enums import ApprovalStatus, AuditEventType, ProjectState
from studio.domain.errors import DomainError, RepositoryConflictError
from studio.domain.models import (
    AuditEvent,
    Briefing,
    Project,
    ProjectSnapshot,
    Revision,
    TaskmasterSpecification,
)
from studio.domain.transitions import transition_project
from studio.domain.validation import validate_specification
from studio.ports.clock import Clock
from studio.ports.model_gateway import model_metadata_details
from studio.ports.repositories import EventRepository, ProjectRepository


class DesignService:
    def __init__(
        self,
        projects: ProjectRepository,
        events: EventRepository,
        clock: Clock,
        designer: OfficialAcademicDesigner | None = None,
        specification_generator: StructuredSpecificationGenerator | None = None,
        revision_generator: StructuredRevisionGenerator | None = None,
    ) -> None:
        self._projects = projects
        self._events = events
        self._clock = clock
        self._designer = designer or OfficialAcademicDesigner()
        self._specification_generator = specification_generator
        self._revision_generator = revision_generator

    def create_initial_revision(
        self,
        project_id: str,
        *,
        owner_session_id: str,
        idempotency_key: str,
    ) -> DesignResult:
        snapshot = self._authorized(project_id, owner_session_id)
        if snapshot.project.active_revision == 1:
            return DesignResult(snapshot=snapshot, revision=_revision(snapshot, 1))
        if snapshot.project.active_revision is not None:
            raise RepositoryConflictError(
                "El proyecto ya tiene una revisión activa.",
                active_revision=snapshot.project.active_revision,
            )
        briefing = require_confirmed_briefing(snapshot.project)
        specification, model_event = self._initial_specification(snapshot.project, briefing)
        changed = transition_project(snapshot.project, ProjectState.DESIGN_IN_REVIEW)
        with_state = self._projects.save(
            changed,
            expected_version=snapshot.version,
            idempotency_key=f"{idempotency_key}:state",
        )
        revision = Revision(
            project_id=project_id,
            number=1,
            specification=specification,
            created_at=self._clock.now(),
        )
        saved = self._projects.add_revision(
            project_id,
            revision,
            expected_version=with_state.version,
            idempotency_key=f"{idempotency_key}:revision",
        )
        if model_event is not None:
            event_type, summary, details = model_event
            self._event(
                saved.project,
                event_type,
                "gemini_vertex",
                summary,
                key=f"{idempotency_key}:model_event",
                details=details,
                revision=1,
            )
        source = (
            "vertex_ai"
            if model_event and model_event[0] is AuditEventType.MODEL_GENERATION_COMPLETED
            else "deterministic"
        )
        self._event(
            saved.project,
            AuditEventType.DESIGN_REQUESTED,
            owner_session_id,
            "Diseño solicitado desde un briefing confirmado.",
            key=f"{idempotency_key}:requested_event",
            details={"source": "confirmed_briefing", "designer": source},
        )
        self._event(
            saved.project,
            AuditEventType.REVISION_CREATED,
            "gemini_vertex" if source == "vertex_ai" else "deterministic_designer",
            "Revisión 1 creada en estado draft; requiere decisión humana.",
            key=f"{idempotency_key}:revision_event",
            details={"revision": 1, "approval_status": "draft"},
            revision=1,
        )
        return DesignResult(snapshot=saved, revision=_revision(saved, 1))

    def _initial_specification(
        self,
        project: Project,
        briefing: Briefing,
    ) -> tuple[
        TaskmasterSpecification,
        tuple[AuditEventType, str, dict[str, Any]] | None,
    ]:
        now = self._clock.now()
        if self._specification_generator is not None:
            try:
                generated: GeneratedSpecification = self._specification_generator.generate(
                    project_id=project.id,
                    project_name=project.name,
                    briefing=briefing,
                    now=now,
                )
                _validate_design(generated.specification, project.id)
            except DomainError as error:
                specification = self._designer.initial_design(
                    project_id=project.id,
                    briefing=briefing,
                    now=now,
                )
                _validate_design(specification, project.id)
                decision = decide_local_fallback(
                    "taskmaster_specification",
                    "deterministic_designer",
                    error.code,
                    model_attempted=True,
                )
                return specification, (
                    AuditEventType.MODEL_FALLBACK_USED,
                    "Se utilizó el diseñador local seguro para crear la especificación.",
                    fallback_event_details(decision, error=error),
                )
            metadata = generated.model_metadata
            return generated.specification, (
                AuditEventType.MODEL_GENERATION_COMPLETED,
                "Gemini generó una especificación Taskmaster estructurada.",
                {
                    "operation": "taskmaster_specification",
                    **model_metadata_details(metadata),
                },
            )
        specification = self._designer.initial_design(
            project_id=project.id,
            briefing=briefing,
            now=now,
        )
        _validate_design(specification, project.id)
        return specification, None

    def apply_feedback(
        self,
        project_id: str,
        *,
        expected_revision: int,
        feedback: str,
        owner_session_id: str,
        idempotency_key: str,
    ) -> DesignResult:
        snapshot = self._authorized(project_id, owner_session_id)
        clean_feedback = feedback.strip()
        if not clean_feedback:
            raise DomainError("EMPTY_FEEDBACK", "El feedback no puede estar vacío.")
        if snapshot.project.state is not ProjectState.DESIGN_IN_REVIEW:
            raise DomainError(
                "DESIGN_NOT_EDITABLE",
                "Solo un diseño en revisión puede recibir feedback.",
                context={"state": snapshot.project.state.value},
            )
        if snapshot.project.active_revision == 2:
            first = _revision(snapshot, 1)
            second = _revision(snapshot, 2)
            recorded_feedback = next(
                (
                    event
                    for event in reversed(self._events.list_for_project(project_id))
                    if event.event_type is AuditEventType.FEEDBACK_RECORDED and event.revision == 1
                ),
                None,
            )
            digest = hashlib.sha256(clean_feedback.encode("utf-8")).hexdigest()
            if (
                recorded_feedback is None
                or recorded_feedback.details.get("feedback_sha256") != digest
            ):
                raise DomainError(
                    "DESIGN_ALREADY_REVISED",
                    "La revisión 1 ya fue adaptada con un feedback diferente.",
                    context={"active_revision": 2},
                )
            return DesignResult(
                snapshot=snapshot,
                revision=second,
                diff=compute_structural_diff(first, second),
            )
        if snapshot.project.active_revision != expected_revision or expected_revision != 1:
            raise RepositoryConflictError(
                "El feedback apunta a una revisión que dejó de estar activa.",
                expected_revision=expected_revision,
                active_revision=snapshot.project.active_revision,
            )
        first = _revision(snapshot, 1)
        specification, model_event = self._revised_specification(
            snapshot.project,
            first.specification,
            clean_feedback,
        )
        second = Revision(
            project_id=project_id,
            number=2,
            specification=specification,
            created_at=self._clock.now(),
        )
        saved = self._projects.add_revision(
            project_id,
            second,
            expected_version=snapshot.version,
            idempotency_key=f"{idempotency_key}:revision",
        )
        feedback_digest = hashlib.sha256(clean_feedback.encode("utf-8")).hexdigest()
        if model_event is not None:
            event_type, summary, details = model_event
            self._event(
                saved.project,
                event_type,
                "gemini_vertex",
                summary,
                key=f"{idempotency_key}:model_event",
                details=details,
                revision=2,
            )
        self._event(
            saved.project,
            AuditEventType.FEEDBACK_RECORDED,
            owner_session_id,
            "Feedback humano registrado para adaptar la revisión 1.",
            key=f"{idempotency_key}:feedback_event",
            details={
                "source_revision": 1,
                "feedback_sha256": feedback_digest,
                "feedback_length": len(clean_feedback),
            },
            revision=1,
        )
        self._event(
            saved.project,
            AuditEventType.REVISION_CREATED,
            (
                "gemini_vertex"
                if model_event and model_event[0] is AuditEventType.MODEL_GENERATION_COMPLETED
                else "deterministic_designer"
            ),
            "Revisión 2 creada; las revisiones anteriores permanecen disponibles.",
            key=f"{idempotency_key}:revision_event",
            details={"revision": 2, "source_revision": 1, "approval_status": "draft"},
            revision=2,
        )
        stored_second = _revision(saved, 2)
        return DesignResult(
            snapshot=saved,
            revision=stored_second,
            diff=compute_structural_diff(first, stored_second),
        )

    def _revised_specification(
        self,
        project: Project,
        source: TaskmasterSpecification,
        feedback: str,
    ) -> tuple[
        TaskmasterSpecification,
        tuple[AuditEventType, str, dict[str, Any]] | None,
    ]:
        now = self._clock.now()
        if self._revision_generator is not None:
            try:
                generated: GeneratedRevision = self._revision_generator.generate(
                    source=source,
                    feedback=feedback,
                    now=now,
                )
                ensure_protected_policies_preserved(source, generated.specification)
                _validate_design(generated.specification, project.id)
            except DomainError as error:
                specification = self._safe_local_revision(project, source, now)
                decision = decide_local_fallback(
                    "taskmaster_revision",
                    "deterministic_reviewer",
                    error.code,
                    model_attempted=True,
                )
                return specification, (
                    AuditEventType.MODEL_FALLBACK_USED,
                    "Se utilizó el revisor local seguro para adaptar la especificación.",
                    fallback_event_details(decision, error=error),
                )
            metadata = generated.model_metadata
            return generated.specification, (
                AuditEventType.MODEL_GENERATION_COMPLETED,
                "Gemini generó una revisión Taskmaster estructurada.",
                {
                    "operation": "taskmaster_revision",
                    "source_revision": source.revision,
                    "target_revision": source.revision + 1,
                    **model_metadata_details(metadata),
                },
            )
        specification = self._designer.revised_design(
            project_id=project.id,
            briefing=project.briefing,
            now=now,
        )
        ensure_protected_policies_preserved(source, specification)
        _validate_design(specification, project.id)
        return specification, None

    def _safe_local_revision(
        self,
        project: Project,
        source: TaskmasterSpecification,
        now: datetime,
    ) -> TaskmasterSpecification:
        candidate = self._designer.revised_design(
            project_id=project.id,
            briefing=project.briefing,
            now=now,
        )
        try:
            ensure_protected_policies_preserved(source, candidate)
            _validate_design(candidate, project.id)
            return candidate
        except DomainError:
            conservative = source.model_copy(
                update={
                    "revision": source.revision + 1,
                    "metadata": source.metadata.model_copy(
                        update={"updated_at": now, "created_by": "deterministic_reviewer"}
                    ),
                    "approval": source.approval.model_copy(
                        update={
                            "status": ApprovalStatus.DRAFT,
                            "decided_by": None,
                            "decided_at": None,
                            "note": "",
                        }
                    ),
                },
                deep=True,
            )
            ensure_protected_policies_preserved(source, conservative)
            _validate_design(conservative, project.id)
            return conservative

    def get_diff(
        self,
        project_id: str,
        *,
        from_revision: int,
        to_revision: int,
        owner_session_id: str,
    ) -> StructuralDiff:
        snapshot = self._authorized(project_id, owner_session_id)
        return compute_structural_diff(
            _revision(snapshot, from_revision),
            _revision(snapshot, to_revision),
        )

    def get_overview(
        self,
        project_id: str,
        *,
        revision: int,
        owner_session_id: str,
    ) -> DesignOverview:
        stored = _revision(self._authorized(project_id, owner_session_id), revision)
        specification = stored.specification
        return DesignOverview(
            revision=revision,
            goal=specification.mission.goal,
            steps=[
                StepOverview(
                    id=step.id,
                    name=step.name,
                    description=step.description,
                    risk=step.risk.value,
                    tool_ids=list(step.tool_ids),
                    approval_required=step.approval_policy_id is not None,
                )
                for step in specification.workflow.steps
            ],
            tools=[
                ToolOverview(
                    id=tool.id,
                    name=tool.name,
                    mode=tool.mode,
                    risk=tool.risk.value,
                    description=tool.description,
                )
                for tool in specification.tools
            ],
            policies=[policy.name for policy in specification.policies],
            verification_criteria=[
                criterion.description for criterion in specification.verification.criteria
            ],
            approval_status=specification.approval.status.value,
        )

    def _authorized(self, project_id: str, owner_session_id: str) -> ProjectSnapshot:
        snapshot = self._projects.get(project_id, owner_session_id=owner_session_id)
        ProjectService.ensure_owner(snapshot, owner_session_id)
        return snapshot

    def _event(
        self,
        project: Project,
        event_type: AuditEventType,
        actor_id: str,
        summary: str,
        *,
        key: str,
        details: dict[str, Any],
        revision: int | None = None,
    ) -> None:
        self._events.append(
            AuditEvent(
                id=_event_id(event_type.value, key),
                project_id=project.id,
                event_type=event_type,
                actor_id=actor_id,
                summary=summary,
                revision=revision,
                occurred_at=self._clock.now(),
                details=details,
            ),
            idempotency_key=key,
        )


def compute_structural_diff(before: Revision, after: Revision) -> StructuralDiff:
    if before.project_id != after.project_id:
        raise DomainError(
            "DIFF_PROJECT_MISMATCH", "Las revisiones pertenecen a proyectos distintos."
        )
    added: list[DiffItem] = []
    removed: list[DiffItem] = []
    modified: list[DiffItem] = []
    left = before.specification
    right = after.specification
    _compare_scalars("scope_in", left.mission.scope_in, right.mission.scope_in, added, removed)
    _compare_scalars("scope_out", left.mission.scope_out, right.mission.scope_out, added, removed)
    _compare_scalars(
        "terminal_state",
        left.workflow.terminal_states,
        right.workflow.terminal_states,
        added,
        removed,
    )
    _compare_models(
        "workflow_step", left.workflow.steps, right.workflow.steps, added, removed, modified
    )
    _compare_models("tool", left.tools, right.tools, added, removed, modified)
    _compare_models("policy", left.policies, right.policies, added, removed, modified)
    _compare_models(
        "test_scenario", left.test_scenarios, right.test_scenarios, added, removed, modified
    )

    def sort_key(item: DiffItem) -> tuple[str, str]:
        return item.category, item.identifier

    return StructuralDiff(
        from_revision=before.number,
        to_revision=after.number,
        added=sorted(added, key=sort_key),
        removed=sorted(removed, key=sort_key),
        modified=sorted(modified, key=sort_key),
    )


def ensure_protected_policies_preserved(
    before: TaskmasterSpecification,
    after: TaskmasterSpecification,
) -> None:
    protected = {
        policy.id: policy
        for policy in before.policies
        if policy.type in {"deny", "require_approval", "data"}
    }
    current = {policy.id: policy for policy in after.policies}
    missing = sorted(set(protected) - set(current))
    weakened = sorted(
        identifier
        for identifier, policy in protected.items()
        if identifier in current and current[identifier].type != policy.type
    )
    if missing or weakened:
        raise DomainError(
            "SILENT_POLICY_REDUCTION",
            "El feedback no puede retirar o debilitar políticas protegidas silenciosamente.",
            context={"missing": missing, "weakened": weakened},
        )


def _validate_design(specification: TaskmasterSpecification, project_id: str) -> None:
    result = validate_specification(
        specification.model_dump(mode="json", by_alias=True),
        active_project_id=project_id,
    )
    if not result.valid:
        raise DomainError(
            "DESIGN_CONTRACT_INVALID",
            "El diseño determinista no cumple el contrato canónico.",
            context={"issues": [issue.as_dict() for issue in result.errors]},
        )


def _revision(snapshot: ProjectSnapshot, number: int) -> Revision:
    match = next((revision for revision in snapshot.revisions if revision.number == number), None)
    if match is None:
        raise DomainError(
            "REVISION_NOT_FOUND",
            f"No existe la revisión {number}.",
            context={"revision": number},
        )
    return match


def _compare_scalars(
    category: Any,
    before: Iterable[str],
    after: Iterable[str],
    added: list[DiffItem],
    removed: list[DiffItem],
) -> None:
    left = set(before)
    right = set(after)
    added.extend(
        DiffItem(category=category, identifier=value, after=value) for value in right - left
    )
    removed.extend(
        DiffItem(category=category, identifier=value, before=value) for value in left - right
    )


def _compare_models(
    category: Any,
    before: Iterable[Any],
    after: Iterable[Any],
    added: list[DiffItem],
    removed: list[DiffItem],
    modified: list[DiffItem],
) -> None:
    left = {item.id: item.model_dump(mode="json", by_alias=True) for item in before}
    right = {item.id: item.model_dump(mode="json", by_alias=True) for item in after}
    added.extend(
        DiffItem(category=category, identifier=identifier, after=right[identifier])
        for identifier in right.keys() - left.keys()
    )
    removed.extend(
        DiffItem(category=category, identifier=identifier, before=left[identifier])
        for identifier in left.keys() - right.keys()
    )
    modified.extend(
        DiffItem(
            category=category,
            identifier=identifier,
            before=left[identifier],
            after=right[identifier],
        )
        for identifier in left.keys() & right.keys()
        if left[identifier] != right[identifier]
    )


def _event_id(kind: str, key: str) -> str:
    safe_kind = re.sub(r"[^a-z0-9_]", "_", kind.casefold())[:38]
    digest = hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]
    return f"{safe_kind}_{digest}"
