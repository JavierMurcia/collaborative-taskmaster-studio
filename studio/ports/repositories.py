"""Persistence ports shared by local storage and future Firestore adapters."""

from typing import Protocol

from studio.domain.models import (
    ApprovalRecord,
    ArtifactMetadata,
    AuditEvent,
    Project,
    ProjectSnapshot,
    Revision,
)


class ProjectRepository(Protocol):
    def create(self, project: Project, *, idempotency_key: str) -> ProjectSnapshot: ...

    def get(
        self,
        project_id: str,
        *,
        owner_session_id: str | None = None,
    ) -> ProjectSnapshot: ...

    def save(
        self,
        project: Project,
        *,
        expected_version: int,
        idempotency_key: str,
    ) -> ProjectSnapshot: ...

    def add_revision(
        self,
        project_id: str,
        revision: Revision,
        *,
        expected_version: int,
        idempotency_key: str,
    ) -> ProjectSnapshot: ...

    def record_approval(
        self,
        record: ApprovalRecord,
        *,
        expected_version: int,
        idempotency_key: str,
    ) -> ProjectSnapshot: ...

    def add_artifact(
        self,
        artifact: ArtifactMetadata,
        *,
        expected_version: int,
        idempotency_key: str,
    ) -> ProjectSnapshot: ...

    def reset_demo(
        self,
        project: Project,
        *,
        owner_session_id: str,
        idempotency_key: str,
    ) -> ProjectSnapshot: ...


class EventRepository(Protocol):
    def append(self, event: AuditEvent, *, idempotency_key: str) -> AuditEvent: ...

    def list_for_project(
        self, project_id: str, *, after_sequence: int = 0
    ) -> tuple[AuditEvent, ...]: ...
