"""In-memory and durable JSON repositories with identical behavior."""

from __future__ import annotations

import hashlib
import json
import os
import re
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from uuid import uuid4

from infrastructure.local.clock import SystemClock
from studio.domain.enums import ApprovalStatus, ProjectState
from studio.domain.errors import (
    EntityNotFoundError,
    IdempotencyConflictError,
    ProjectAccessDeniedError,
    RepositoryConflictError,
)
from studio.domain.models import (
    ApprovalRecord,
    ArtifactMetadata,
    AuditEvent,
    Project,
    ProjectSnapshot,
    Revision,
)
from studio.domain.transitions import transition_project
from studio.ports.clock import Clock


@dataclass(slots=True)
class _ProjectRecord:
    project: Project
    version: int = 1
    revisions: dict[int, Revision] = field(default_factory=dict)
    approvals: dict[str, ApprovalRecord] = field(default_factory=dict)
    artifacts: dict[str, ArtifactMetadata] = field(default_factory=dict)
    events: list[AuditEvent] = field(default_factory=list)
    operations: dict[str, str] = field(default_factory=dict)


class InMemoryRepository:
    """Thread-safe repository used by unit tests and ephemeral local runs."""

    def __init__(self, clock: Clock | None = None) -> None:
        self._clock = clock or SystemClock()
        self._records: dict[str, _ProjectRecord] = {}
        self._lock = threading.RLock()

    def create(self, project: Project, *, idempotency_key: str) -> ProjectSnapshot:
        fingerprint = _fingerprint("create", project, exclude={"created_at", "updated_at"})
        with self._lock:
            existing = self._records.get(project.id)
            if existing:
                if self._is_replay(existing, idempotency_key, fingerprint):
                    return self._snapshot(existing)
                raise RepositoryConflictError(
                    f"El proyecto {project.id} ya existe.", project_id=project.id
                )
            _require_idempotency_key(idempotency_key)
            stored = project.model_copy(
                update={"created_at": self._clock.now(), "updated_at": self._clock.now()},
                deep=True,
            )
            record = _ProjectRecord(project=stored)
            record.operations[idempotency_key] = fingerprint
            self._records[project.id] = record
            return self._snapshot(record)

    def get(
        self,
        project_id: str,
        *,
        owner_session_id: str | None = None,
    ) -> ProjectSnapshot:
        with self._lock:
            record = self._record(project_id)
            if (
                owner_session_id is not None
                and record.project.owner_session_id != owner_session_id
            ):
                raise ProjectAccessDeniedError(project_id)
            return self._snapshot(record)

    def save(
        self,
        project: Project,
        *,
        expected_version: int,
        idempotency_key: str,
    ) -> ProjectSnapshot:
        fingerprint = _fingerprint("save", project)
        with self._lock:
            record = self._record(project.id)
            if self._is_replay(record, idempotency_key, fingerprint):
                return self._snapshot(record)
            self._expect_version(record, expected_version)
            record.project = project.model_copy(update={"updated_at": self._clock.now()}, deep=True)
            self._commit(record, idempotency_key, fingerprint)
            return self._snapshot(record)

    def add_revision(
        self,
        project_id: str,
        revision: Revision,
        *,
        expected_version: int,
        idempotency_key: str,
    ) -> ProjectSnapshot:
        fingerprint = _fingerprint("add_revision", revision, exclude={"created_at"})
        with self._lock:
            record = self._record(project_id)
            if self._is_replay(record, idempotency_key, fingerprint):
                return self._snapshot(record)
            self._expect_version(record, expected_version)
            if revision.project_id != project_id:
                raise RepositoryConflictError(
                    "La revisión pertenece a otro proyecto.",
                    project_id=project_id,
                    revision_project_id=revision.project_id,
                )
            if revision.number in record.revisions:
                raise RepositoryConflictError(
                    f"La revisión {revision.number} ya existe.", revision=revision.number
                )
            record.revisions[revision.number] = revision.model_copy(deep=True)
            record.project = record.project.model_copy(
                update={
                    "active_revision": revision.number,
                    "updated_at": self._clock.now(),
                },
                deep=True,
            )
            self._commit(record, idempotency_key, fingerprint)
            return self._snapshot(record)

    def record_approval(
        self,
        approval_record: ApprovalRecord,
        *,
        expected_version: int,
        idempotency_key: str,
    ) -> ProjectSnapshot:
        fingerprint = _fingerprint("record_approval", approval_record, exclude={"created_at"})
        with self._lock:
            record = self._record(approval_record.project_id)
            if self._is_replay(record, idempotency_key, fingerprint):
                return self._snapshot(record)
            self._expect_version(record, expected_version)
            if record.project.active_revision != approval_record.revision:
                raise RepositoryConflictError(
                    "La revisión dejó de ser la revisión activa.",
                    expected_revision=approval_record.revision,
                    active_revision=record.project.active_revision,
                )
            revision = record.revisions.get(approval_record.revision)
            if revision is None:
                raise EntityNotFoundError("revision", str(approval_record.revision))
            if approval_record.id in record.approvals:
                raise RepositoryConflictError(
                    f"La aprobación {approval_record.id} ya existe.",
                    approval_id=approval_record.id,
                )
            changed_specification = revision.specification.model_copy(
                update={"approval": approval_record.approval}, deep=True
            )
            record.revisions[revision.number] = revision.replace_specification(
                changed_specification
            )
            record.approvals[approval_record.id] = approval_record.model_copy(deep=True)
            project = record.project
            if (
                approval_record.approval.status is ApprovalStatus.APPROVED
                and project.state is ProjectState.DESIGN_IN_REVIEW
            ):
                project = transition_project(project, ProjectState.DESIGN_APPROVED)
            record.project = project.model_copy(update={"updated_at": self._clock.now()}, deep=True)
            self._commit(record, idempotency_key, fingerprint)
            return self._snapshot(record)

    def add_artifact(
        self,
        artifact: ArtifactMetadata,
        *,
        expected_version: int,
        idempotency_key: str,
    ) -> ProjectSnapshot:
        fingerprint = _fingerprint("add_artifact", artifact)
        with self._lock:
            record = self._record(artifact.project_id)
            if self._is_replay(record, idempotency_key, fingerprint):
                return self._snapshot(record)
            self._expect_version(record, expected_version)
            if artifact.revision not in record.revisions:
                raise EntityNotFoundError("revision", str(artifact.revision))
            if artifact.id in record.artifacts:
                raise RepositoryConflictError(
                    f"El artefacto {artifact.id} ya existe.", artifact_id=artifact.id
                )
            record.artifacts[artifact.id] = artifact.model_copy(deep=True)
            self._commit(record, idempotency_key, fingerprint)
            return self._snapshot(record)

    def append(self, event: AuditEvent, *, idempotency_key: str) -> AuditEvent:
        fingerprint = _fingerprint("append_event", event, exclude={"sequence", "occurred_at"})
        with self._lock:
            record = self._record(event.project_id)
            if self._is_replay(record, idempotency_key, fingerprint):
                replay = next((item for item in record.events if item.id == event.id), None)
                if replay is None:
                    raise IdempotencyConflictError(idempotency_key)
                return replay.model_copy(deep=True)
            if any(item.id == event.id for item in record.events):
                raise RepositoryConflictError(f"El evento {event.id} ya existe.", event_id=event.id)
            stored = event.model_copy(
                update={
                    "sequence": len(record.events) + 1,
                    "occurred_at": self._clock.now(),
                },
                deep=True,
            )
            record.events.append(stored)
            record.operations[idempotency_key] = fingerprint
            return stored.model_copy(deep=True)

    def list_for_project(
        self, project_id: str, *, after_sequence: int = 0
    ) -> tuple[AuditEvent, ...]:
        with self._lock:
            record = self._record(project_id)
            return tuple(
                item.model_copy(deep=True)
                for item in record.events
                if item.sequence is not None and item.sequence > after_sequence
            )

    def reset_demo(
        self,
        project: Project,
        *,
        owner_session_id: str,
        idempotency_key: str,
    ) -> ProjectSnapshot:
        """Replace exactly one session-owned aggregate with a clean demo snapshot."""

        fingerprint = _fingerprint(
            "reset_demo", project, exclude={"created_at", "updated_at"}
        )
        with self._lock:
            _require_idempotency_key(idempotency_key)
            existing = self._records.get(project.id)
            if existing is not None:
                if existing.project.owner_session_id != owner_session_id:
                    raise ProjectAccessDeniedError(project.id)
                if self._is_replay(existing, idempotency_key, fingerprint):
                    return self._snapshot(existing)
            if project.owner_session_id != owner_session_id:
                raise ProjectAccessDeniedError(project.id)
            stored = project.model_copy(
                update={"created_at": self._clock.now(), "updated_at": self._clock.now()},
                deep=True,
            )
            record = _ProjectRecord(project=stored)
            record.operations[idempotency_key] = fingerprint
            self._records[project.id] = record
            return self._snapshot(record)

    def _record(self, project_id: str) -> _ProjectRecord:
        try:
            return self._records[project_id]
        except KeyError as error:
            raise EntityNotFoundError("project", project_id) from error

    def _is_replay(self, record: _ProjectRecord, key: str, fingerprint: str) -> bool:
        _require_idempotency_key(key)
        previous = record.operations.get(key)
        if previous is None:
            return False
        if previous != fingerprint:
            raise IdempotencyConflictError(key)
        return True

    @staticmethod
    def _expect_version(record: _ProjectRecord, expected: int) -> None:
        if record.version != expected:
            raise RepositoryConflictError(
                "Otra operación cambió el proyecto.",
                expected_version=expected,
                actual_version=record.version,
            )

    @staticmethod
    def _commit(record: _ProjectRecord, key: str, fingerprint: str) -> None:
        record.version += 1
        record.operations[key] = fingerprint

    @staticmethod
    def _snapshot(record: _ProjectRecord) -> ProjectSnapshot:
        return ProjectSnapshot(
            project=record.project.model_copy(deep=True),
            version=record.version,
            revisions=tuple(
                item.model_copy(deep=True) for _, item in sorted(record.revisions.items())
            ),
            approvals=tuple(
                item.model_copy(deep=True)
                for item in sorted(record.approvals.values(), key=lambda value: value.created_at)
            ),
            artifacts=tuple(
                item.model_copy(deep=True)
                for item in sorted(record.artifacts.values(), key=lambda value: value.id)
            ),
        )


class JsonLocalRepository(InMemoryRepository):
    """Atomic JSON persistence with a short cross-process lock per project."""

    def __init__(self, data_directory: Path, clock: Clock | None = None) -> None:
        self._data_directory = data_directory.resolve()
        self._projects_directory = self._data_directory / "projects"
        self._locks_directory = self._data_directory / "locks"
        self._projects_directory.mkdir(parents=True, exist_ok=True)
        self._locks_directory.mkdir(parents=True, exist_ok=True)
        super().__init__(clock)
        self._load_all()

    def create(self, project: Project, *, idempotency_key: str) -> ProjectSnapshot:
        with self._file_lock(project.id):
            self._refresh(project.id)
            result = super().create(project, idempotency_key=idempotency_key)
            self._persist(project.id)
            return result

    def get(
        self,
        project_id: str,
        *,
        owner_session_id: str | None = None,
    ) -> ProjectSnapshot:
        with self._lock:
            self._refresh(project_id)
            return super().get(project_id, owner_session_id=owner_session_id)

    def save(
        self,
        project: Project,
        *,
        expected_version: int,
        idempotency_key: str,
    ) -> ProjectSnapshot:
        with self._file_lock(project.id):
            self._refresh(project.id)
            result = super().save(
                project,
                expected_version=expected_version,
                idempotency_key=idempotency_key,
            )
            self._persist(project.id)
            return result

    def add_revision(
        self,
        project_id: str,
        revision: Revision,
        *,
        expected_version: int,
        idempotency_key: str,
    ) -> ProjectSnapshot:
        with self._file_lock(project_id):
            self._refresh(project_id)
            result = super().add_revision(
                project_id,
                revision,
                expected_version=expected_version,
                idempotency_key=idempotency_key,
            )
            self._persist(project_id)
            return result

    def record_approval(
        self,
        approval_record: ApprovalRecord,
        *,
        expected_version: int,
        idempotency_key: str,
    ) -> ProjectSnapshot:
        project_id = approval_record.project_id
        with self._file_lock(project_id):
            self._refresh(project_id)
            result = super().record_approval(
                approval_record,
                expected_version=expected_version,
                idempotency_key=idempotency_key,
            )
            self._persist(project_id)
            return result

    def add_artifact(
        self,
        artifact: ArtifactMetadata,
        *,
        expected_version: int,
        idempotency_key: str,
    ) -> ProjectSnapshot:
        with self._file_lock(artifact.project_id):
            self._refresh(artifact.project_id)
            result = super().add_artifact(
                artifact,
                expected_version=expected_version,
                idempotency_key=idempotency_key,
            )
            self._persist(artifact.project_id)
            return result

    def append(self, event: AuditEvent, *, idempotency_key: str) -> AuditEvent:
        with self._file_lock(event.project_id):
            self._refresh(event.project_id)
            result = super().append(event, idempotency_key=idempotency_key)
            self._persist(event.project_id)
            return result

    def list_for_project(
        self, project_id: str, *, after_sequence: int = 0
    ) -> tuple[AuditEvent, ...]:
        with self._lock:
            self._refresh(project_id)
            return super().list_for_project(project_id, after_sequence=after_sequence)

    def reset_demo(
        self,
        project: Project,
        *,
        owner_session_id: str,
        idempotency_key: str,
    ) -> ProjectSnapshot:
        with self._file_lock(project.id):
            self._refresh(project.id)
            result = super().reset_demo(
                project,
                owner_session_id=owner_session_id,
                idempotency_key=idempotency_key,
            )
            self._persist(project.id)
            return result

    def _load_all(self) -> None:
        for path in self._projects_directory.glob("*.json"):
            self._load_path(path)

    def _refresh(self, project_id: str) -> None:
        path = self._path(project_id)
        if path.exists():
            self._load_path(path)
        else:
            self._records.pop(project_id, None)

    def _load_path(self, path: Path) -> None:
        payload = json.loads(path.read_text(encoding="utf-8"))
        project = Project.model_validate(payload["project"])
        self._records[project.id] = _ProjectRecord(
            project=project,
            version=payload["version"],
            revisions={
                item["number"]: Revision.model_validate(item)
                for item in payload.get("revisions", [])
            },
            approvals={
                item["id"]: ApprovalRecord.model_validate(item)
                for item in payload.get("approvals", [])
            },
            artifacts={
                item["id"]: ArtifactMetadata.model_validate(item)
                for item in payload.get("artifacts", [])
            },
            events=[AuditEvent.model_validate(item) for item in payload.get("events", [])],
            operations=dict(payload.get("operations", {})),
        )

    def _persist(self, project_id: str) -> None:
        record = self._record(project_id)
        payload = {
            "format_version": 1,
            "version": record.version,
            "project": record.project.model_dump(mode="json", by_alias=True),
            "revisions": [
                item.model_dump(mode="json", by_alias=True)
                for _, item in sorted(record.revisions.items())
            ],
            "approvals": [
                item.model_dump(mode="json", by_alias=True) for item in record.approvals.values()
            ],
            "artifacts": [
                item.model_dump(mode="json", by_alias=True) for item in record.artifacts.values()
            ],
            "events": [item.model_dump(mode="json", by_alias=True) for item in record.events],
            "operations": record.operations,
        }
        path = self._path(project_id)
        temporary = path.with_suffix(f".tmp-{uuid4().hex}")
        try:
            temporary.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)

    def _path(self, project_id: str) -> Path:
        _validate_project_id(project_id)
        return self._projects_directory / f"{project_id}.json"

    @contextmanager
    def _file_lock(self, project_id: str, timeout_seconds: float = 3.0) -> Iterator[None]:
        _validate_project_id(project_id)
        lock_path = self._locks_directory / f"{project_id}.lock"
        deadline = time.monotonic() + timeout_seconds
        descriptor: int | None = None
        while descriptor is None:
            try:
                descriptor = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            except FileExistsError:
                if time.monotonic() >= deadline:
                    raise RepositoryConflictError(
                        "No fue posible adquirir el bloqueo de escritura.",
                        project_id=project_id,
                    ) from None
                time.sleep(0.01)
        try:
            os.write(descriptor, str(os.getpid()).encode("ascii"))
            yield
        finally:
            os.close(descriptor)
            lock_path.unlink(missing_ok=True)


def _require_idempotency_key(key: str) -> None:
    if not key.strip():
        raise IdempotencyConflictError(key)


def _validate_project_id(project_id: str) -> None:
    if re.fullmatch(r"[a-z][a-z0-9_]{2,63}", project_id) is None:
        raise EntityNotFoundError("project", project_id)


def _fingerprint(action: str, value: Any, *, exclude: set[str] | None = None) -> str:
    if hasattr(value, "model_dump"):
        payload = value.model_dump(mode="json", by_alias=True, exclude=exclude or set())
    else:
        payload = value
    canonical = json.dumps(
        {"action": action, "payload": payload},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
