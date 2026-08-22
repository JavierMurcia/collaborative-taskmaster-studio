"""Human-only approval decisions and the generation authorization gate."""

from __future__ import annotations

import hashlib
import re
from typing import Literal

from studio.application.project_service import ProjectService
from studio.domain.enums import ApprovalStatus, AuditEventType, ProjectState
from studio.domain.errors import DomainError, IdempotencyConflictError
from studio.domain.models import (
    Approval,
    ApprovalRecord,
    AuditEvent,
    ProjectSnapshot,
    TaskmasterSpecification,
)
from studio.ports.clock import Clock
from studio.ports.repositories import EventRepository, ProjectRepository


class ApprovalService:
    def __init__(
        self,
        projects: ProjectRepository,
        events: EventRepository,
        clock: Clock,
    ) -> None:
        self._projects = projects
        self._events = events
        self._clock = clock

    def decide(
        self,
        project_id: str,
        *,
        revision: int,
        decision: Literal["approved", "rejected"],
        actor_id: str,
        actor_type: Literal["human", "agent", "system"],
        note: str,
        approval_id: str,
        owner_session_id: str,
        idempotency_key: str,
    ) -> ProjectSnapshot:
        snapshot = self._projects.get(project_id, owner_session_id=owner_session_id)
        ProjectService.ensure_owner(snapshot, owner_session_id)
        if actor_type != "human":
            raise DomainError(
                "HUMAN_APPROVAL_REQUIRED",
                "Gemini, agentes y sistemas no pueden aprobar ni rechazar una revisión.",
                context={"actor_type": actor_type},
            )
        existing = next(
            (record for record in snapshot.approvals if record.id == approval_id),
            None,
        )
        if existing is not None:
            expected_status = (
                ApprovalStatus.APPROVED if decision == "approved" else ApprovalStatus.REJECTED
            )
            if (
                existing.revision == revision
                and existing.approval.status is expected_status
                and existing.approval.decided_by == actor_id
                and existing.approval.note == note.strip()
            ):
                return snapshot
            raise IdempotencyConflictError(idempotency_key)
        if snapshot.project.state not in {
            ProjectState.DESIGN_IN_REVIEW,
            ProjectState.DESIGN_APPROVED,
        }:
            raise DomainError(
                "APPROVAL_NOT_AVAILABLE",
                "La revisión no está disponible para decisión humana.",
                context={"state": snapshot.project.state.value},
            )
        if snapshot.project.active_revision != revision:
            raise DomainError(
                "STALE_APPROVAL_REVISION",
                "Solo puede decidirse sobre la revisión activa.",
                context={
                    "requested_revision": revision,
                    "active_revision": snapshot.project.active_revision,
                },
            )
        status = ApprovalStatus.APPROVED if decision == "approved" else ApprovalStatus.REJECTED
        record = ApprovalRecord(
            id=approval_id,
            project_id=project_id,
            revision=revision,
            approval=Approval(
                status=status,
                decided_by=actor_id,
                decided_at=self._clock.now(),
                note=note.strip(),
            ),
            created_at=self._clock.now(),
        )
        saved = self._projects.record_approval(
            record,
            expected_version=snapshot.version,
            idempotency_key=f"{idempotency_key}:approval",
        )
        event_type = (
            AuditEventType.REVISION_APPROVED
            if status is ApprovalStatus.APPROVED
            else AuditEventType.REVISION_REJECTED
        )
        self._events.append(
            AuditEvent(
                id=_event_id(event_type.value, idempotency_key),
                project_id=project_id,
                event_type=event_type,
                actor_id=actor_id,
                summary=(
                    "Revisión aprobada explícitamente por una persona."
                    if status is ApprovalStatus.APPROVED
                    else "Revisión rechazada explícitamente por una persona."
                ),
                revision=revision,
                occurred_at=self._clock.now(),
                details={"decision": status.value, "approval_id": approval_id},
            ),
            idempotency_key=f"{idempotency_key}:event",
        )
        return saved


def require_approved_revision(snapshot: ProjectSnapshot) -> TaskmasterSpecification:
    active = snapshot.project.active_revision
    revision = next((item for item in snapshot.revisions if item.number == active), None)
    if (
        snapshot.project.state is not ProjectState.DESIGN_APPROVED
        or revision is None
        or revision.specification.approval.status is not ApprovalStatus.APPROVED
    ):
        raise DomainError(
            "GENERATION_REQUIRES_APPROVAL",
            "No se pueden generar archivos sin una revisión activa aprobada por una persona.",
            context={"active_revision": active, "state": snapshot.project.state.value},
        )
    return revision.specification.model_copy(deep=True)


def _event_id(kind: str, key: str) -> str:
    safe_kind = re.sub(r"[^a-z0-9_]", "_", kind.casefold())[:38]
    digest = hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]
    return f"{safe_kind}_{digest}"
