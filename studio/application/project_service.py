"""Application service for project creation and session-scoped retrieval."""

from __future__ import annotations

import hashlib

from studio.domain.enums import AuditEventType
from studio.domain.errors import ProjectAccessDeniedError
from studio.domain.models import AuditEvent, Briefing, Project, ProjectSnapshot
from studio.ports.clock import Clock
from studio.ports.repositories import EventRepository, ProjectRepository


class ProjectService:
    def __init__(
        self,
        projects: ProjectRepository,
        events: EventRepository,
        clock: Clock,
    ) -> None:
        self._projects = projects
        self._events = events
        self._clock = clock

    def create_project(
        self,
        *,
        project_id: str,
        name: str,
        description: str,
        owner_session_id: str,
        idempotency_key: str,
    ) -> ProjectSnapshot:
        project = Project(
            id=project_id,
            name=name,
            owner_session_id=owner_session_id,
            briefing=Briefing(problem=description.strip(), goal=_initial_objective(description)),
            created_at=self._clock.now(),
            updated_at=self._clock.now(),
        )
        snapshot = self._projects.create(project, idempotency_key=f"{idempotency_key}:project")
        self._events.append(
            AuditEvent(
                id=_event_id("project_created", idempotency_key),
                project_id=project_id,
                event_type=AuditEventType.PROJECT_CREATED,
                actor_id=owner_session_id,
                summary="Proyecto creado; la descripción inicial se conservó.",
                occurred_at=self._clock.now(),
                details={"state": snapshot.project.state.value},
            ),
            idempotency_key=f"{idempotency_key}:event",
        )
        return snapshot

    def get_snapshot(self, project_id: str, *, owner_session_id: str) -> ProjectSnapshot:
        snapshot = self._projects.get(project_id, owner_session_id=owner_session_id)
        self.ensure_owner(snapshot, owner_session_id)
        return snapshot

    @staticmethod
    def ensure_owner(snapshot: ProjectSnapshot, owner_session_id: str) -> None:
        if snapshot.project.owner_session_id != owner_session_id:
            raise ProjectAccessDeniedError(snapshot.project.id)


def _initial_objective(description: str) -> str:
    normalized = description.casefold()
    if "requisit" in normalized and "evidencia" in normalized:
        return "Organizar requisitos semanales y sus evidencias"
    return description.strip()


def _event_id(kind: str, key: str) -> str:
    digest = hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]
    return f"{kind}_{digest}"
