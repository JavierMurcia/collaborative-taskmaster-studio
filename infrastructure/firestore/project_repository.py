"""Firestore aggregate repository through H9-09 fixed demo retention."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable, Iterable, Mapping
from datetime import datetime
from pathlib import PurePosixPath
from typing import Any, Protocol

from pydantic import ValidationError

from infrastructure.firestore.indexes import EVENT_SEQUENCE_FIELD
from infrastructure.firestore.retention import TTL_FIELD, DemoRetentionPolicy
from infrastructure.firestore.transactions import (
    FirestoreTransactionExecutor,
    Transaction,
    TransactionExecutor,
)
from infrastructure.local.clock import SystemClock
from studio.domain.enums import ApprovalStatus, ProjectState
from studio.domain.errors import (
    DomainError,
    EntityNotFoundError,
    IdempotencyConflictError,
    ProjectAccessDeniedError,
    RepositoryConflictError,
)
from studio.domain.models import (
    ApprovalRecord,
    ArtifactMetadata,
    AuditEvent,
    Briefing,
    Project,
    ProjectSnapshot,
    Revision,
)
from studio.domain.transitions import transition_project
from studio.ports.clock import Clock

_PROJECT_ID = re.compile(r"^[a-z][a-z0-9_]{2,63}$")
_PROJECTS = "projects"
_WINDOWS_RESERVED = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{number}" for number in range(1, 10)),
    *(f"LPT{number}" for number in range(1, 10)),
}


class DocumentSnapshot(Protocol):
    exists: bool
    update_time: object

    def to_dict(self) -> dict[str, Any] | None: ...


class DocumentReference(Protocol):
    def create(self, document_data: dict[str, Any]) -> object: ...

    def get(self, *, transaction: object | None = None) -> DocumentSnapshot: ...

    def update(self, field_updates: dict[str, Any], option: object | None = None) -> object: ...

    def collection(self, collection_id: str) -> CollectionReference: ...


class CollectionReference(Protocol):
    def document(self, document_id: str) -> DocumentReference: ...

    def order_by(self, field_path: str) -> Query: ...


class Query(Protocol):
    def stream(self) -> Iterable[DocumentSnapshot]: ...


class WriteBatch(Protocol):
    def create(
        self,
        reference: DocumentReference,
        document_data: dict[str, Any],
    ) -> object: ...

    def update(
        self,
        reference: DocumentReference,
        field_updates: dict[str, Any],
        option: object | None = None,
    ) -> object: ...

    def commit(self) -> object: ...


class FirestoreClient(Protocol):
    def collection(self, collection_id: str) -> CollectionReference: ...

    def batch(self) -> WriteBatch: ...

    def transaction(self, *, max_attempts: int = 5) -> object: ...

    def recursive_delete(
        self,
        reference: DocumentReference,
        *,
        chunk_size: int = 5000,
    ) -> int: ...


WriteOptionFactory = Callable[[object], object]


class FirestoreProjectRepository:
    """Persist the project aggregate and immutable child metadata."""

    def __init__(
        self,
        client: FirestoreClient,
        clock: Clock | None = None,
        *,
        write_option_factory: WriteOptionFactory | None = None,
        transaction_executor: TransactionExecutor | None = None,
        transaction_max_attempts: int = 5,
        retention_policy: DemoRetentionPolicy | None = None,
    ) -> None:
        self._client = client
        self._clock = clock or SystemClock()
        self._write_option_factory = write_option_factory or _last_update_option
        self._transaction_executor = (
            transaction_executor
            or FirestoreTransactionExecutor(max_attempts=transaction_max_attempts)
        )
        self._retention_policy = retention_policy or DemoRetentionPolicy()

    def create(self, project: Project, *, idempotency_key: str) -> ProjectSnapshot:
        _require_idempotency_key(idempotency_key)
        fingerprint = _fingerprint(
            "create",
            project,
            exclude={"created_at", "updated_at"},
        )
        document = self._document(project.id)
        now = self._clock.now()
        expires_at = self._retention_policy.expires_at(now)
        stored = project.model_copy(
            update={"created_at": now, "updated_at": now},
            deep=True,
        )
        payload = _payload(
            stored,
            version=1,
            operations={},
            briefing_version=1,
            revision_numbers=(),
            approval_ids=(),
            event_sequence=0,
            artifact_ids=(),
            expires_at=expires_at,
        )
        payload["operations"] = _record_operation({}, idempotency_key, fingerprint)
        try:
            batch = self._client.batch()
            batch.create(document, payload)
            batch.create(
                _briefing_document(document, 1),
                _briefing_payload(
                    stored.briefing, stored, version=1, expires_at=expires_at
                ),
            )
            batch.commit()
        except Exception as error:
            if not _is_error(error, "AlreadyExists"):
                raise _unavailable("crear", project.id) from error
            try:
                existing, _ = self._read(document, project.id)
            except EntityNotFoundError:
                raise RepositoryConflictError(
                    "Existe información subordinada sin un proyecto raíz válido.",
                    project_id=project.id,
                ) from error
            if _is_replay(existing, project.id, idempotency_key, fingerprint):
                return self._snapshot_with_children(document, existing, project.id)
            raise RepositoryConflictError(
                f"El proyecto {project.id} ya existe.",
                project_id=project.id,
            ) from error
        return _snapshot(payload, project.id, briefing=stored.briefing)

    def get(
        self,
        project_id: str,
        *,
        owner_session_id: str | None = None,
    ) -> ProjectSnapshot:
        document = self._document(project_id)
        payload, _ = self._read(document, project_id)
        project = _project(payload, project_id)
        if (
            owner_session_id is not None
            and project.owner_session_id != owner_session_id
        ):
            raise ProjectAccessDeniedError(project_id)
        return self._snapshot_with_children(document, payload, project_id)

    def reset_demo(
        self,
        project: Project,
        *,
        owner_session_id: str,
        idempotency_key: str,
    ) -> ProjectSnapshot:
        """Recursively replace one owned demo aggregate, including every child collection."""

        _require_idempotency_key(idempotency_key)
        if project.owner_session_id != owner_session_id:
            raise ProjectAccessDeniedError(project.id)
        fingerprint = _fingerprint_model(
            "reset_demo", project, exclude={"created_at", "updated_at"}
        )
        document = self._document(project.id)
        try:
            current, _ = self._read(document, project.id)
        except EntityNotFoundError:
            current = None
        if current is not None:
            stored_owner = _project(current, project.id).owner_session_id
            if stored_owner != owner_session_id:
                raise ProjectAccessDeniedError(project.id)
            if _is_replay(current, project.id, idempotency_key, fingerprint):
                return self._snapshot_with_children(document, current, project.id)

        now = self._clock.now()
        expires_at = self._retention_policy.expires_at(now)
        stored = project.model_copy(
            update={"created_at": now, "updated_at": now}, deep=True
        )
        payload = _payload(
            stored,
            version=1,
            operations=_record_operation({}, idempotency_key, fingerprint),
            briefing_version=1,
            revision_numbers=(),
            approval_ids=(),
            event_sequence=0,
            artifact_ids=(),
            expires_at=expires_at,
        )
        try:
            self._client.recursive_delete(document, chunk_size=500)
            batch = self._client.batch()
            batch.create(document, payload)
            batch.create(
                _briefing_document(document, 1),
                _briefing_payload(stored.briefing, stored, version=1, expires_at=expires_at),
            )
            batch.commit()
        except Exception as error:
            if _is_error(error, "AlreadyExists", "Aborted", "Conflict", "FailedPrecondition"):
                raise RepositoryConflictError(
                    "Otra operación cambió el proyecto durante el reinicio.",
                    project_id=project.id,
                ) from error
            raise _unavailable("reiniciar demostración", project.id) from error
        return _snapshot(payload, project.id, briefing=stored.briefing)

    def save(
        self,
        project: Project,
        *,
        expected_version: int,
        idempotency_key: str,
    ) -> ProjectSnapshot:
        _require_idempotency_key(idempotency_key)
        fingerprint = _fingerprint("save", project)
        document = self._document(project.id)
        current, update_time = self._read(document, project.id)
        if _is_replay(current, project.id, idempotency_key, fingerprint):
            return self._snapshot_with_children(document, current, project.id)
        actual_version = _version(current, project.id)
        if actual_version != expected_version:
            raise RepositoryConflictError(
                "Otra operación cambió el proyecto.",
                expected_version=expected_version,
                actual_version=actual_version,
            )
        stored = project.model_copy(update={"updated_at": self._clock.now()}, deep=True)
        current_briefing = self._read_briefing(document, current, project.id)
        current_briefing_version = current.get("briefing_version")
        briefing_changed = (
            not isinstance(current_briefing_version, int)
            or isinstance(current_briefing_version, bool)
            or current_briefing != stored.briefing
        )
        operations = _record_operation(
            _operations(current, project.id),
            idempotency_key,
            fingerprint,
        )
        next_version = actual_version + 1
        next_briefing_version = (
            next_version if briefing_changed else _briefing_version(current, project.id)
        )
        expires_at = _expires_at(current, project.id)
        updated = _payload(
            stored,
            version=next_version,
            operations=operations,
            briefing_version=next_briefing_version,
            revision_numbers=_revision_numbers(current, project.id),
            approval_ids=_approval_ids(current, project.id),
            event_sequence=_event_sequence(current, project.id),
            artifact_ids=_artifact_ids(current, project.id),
            expires_at=expires_at,
        )
        try:
            batch = self._client.batch()
            if briefing_changed:
                batch.create(
                    _briefing_document(document, next_briefing_version),
                    _briefing_payload(
                        stored.briefing,
                        stored,
                        version=next_briefing_version,
                        expires_at=expires_at,
                    ),
                )
            batch.update(
                document,
                updated,
                option=self._write_option_factory(update_time),
            )
            batch.commit()
        except Exception as error:
            if _is_error(error, "Aborted", "Conflict", "FailedPrecondition"):
                raise RepositoryConflictError(
                    "Otra operación cambió el proyecto.",
                    expected_version=expected_version,
                ) from error
            if _is_error(error, "NotFound"):
                raise EntityNotFoundError("project", project.id) from error
            raise _unavailable("actualizar", project.id) from error
        return self._snapshot_with_children(document, updated, project.id)

    def add_revision(
        self,
        project_id: str,
        revision: Revision,
        *,
        expected_version: int,
        idempotency_key: str,
    ) -> ProjectSnapshot:
        _require_idempotency_key(idempotency_key)
        fingerprint = _fingerprint_model(
            "add_revision",
            revision,
            exclude={"created_at"},
        )
        document = self._document(project_id)
        def operation(transaction: Transaction) -> dict[str, Any]:
            current, _ = self._read(document, project_id, transaction=transaction)
            if _is_replay(current, project_id, idempotency_key, fingerprint):
                return current
            actual_version = _version(current, project_id)
            if actual_version != expected_version:
                raise RepositoryConflictError(
                    "Otra operación cambió el proyecto.",
                    expected_version=expected_version,
                    actual_version=actual_version,
                )
            if revision.project_id != project_id:
                raise RepositoryConflictError(
                    "La revisión pertenece a otro proyecto.",
                    project_id=project_id,
                    revision_project_id=revision.project_id,
                )
            numbers = _revision_numbers(current, project_id)
            if revision.number in numbers:
                raise RepositoryConflictError(
                    f"La revisión {revision.number} ya existe.",
                    revision=revision.number,
                )
            if numbers and revision.number < numbers[-1]:
                raise RepositoryConflictError(
                    "La nueva revisión no puede preceder a la revisión activa.",
                    revision=revision.number,
                    active_revision=numbers[-1],
                )
            project = _project(current, project_id).model_copy(
                update={
                    "active_revision": revision.number,
                    "updated_at": self._clock.now(),
                },
                deep=True,
            )
            updated = _payload(
                project,
                version=actual_version + 1,
                operations=_record_operation(
                    _operations(current, project_id), idempotency_key, fingerprint
                ),
                briefing_version=_briefing_version(current, project_id),
                revision_numbers=(*numbers, revision.number),
                approval_ids=_approval_ids(current, project_id),
                event_sequence=_event_sequence(current, project_id),
                artifact_ids=_artifact_ids(current, project_id),
                expires_at=_expires_at(current, project_id),
            )
            transaction.create(
                _revision_document(document, revision.number),
                _revision_payload(
                    revision, expires_at=_expires_at(current, project_id)
                ),
            )
            transaction.update(document, updated)
            return updated

        try:
            updated = self._transaction_executor.execute(self._client, operation)
        except Exception as error:
            if isinstance(error, DomainError):
                raise
            if _is_error(error, "AlreadyExists"):
                raise RepositoryConflictError(
                    f"La revisión {revision.number} ya existe.",
                    revision=revision.number,
                ) from error
            if _is_error(error, "Aborted", "Conflict", "FailedPrecondition"):
                raise RepositoryConflictError(
                    "Otra operación cambió el proyecto.",
                    expected_version=expected_version,
                ) from error
            if _is_error(error, "NotFound"):
                raise EntityNotFoundError("project", project_id) from error
            if _is_retry_exhausted(error):
                raise _retry_exhausted("crear revisión", project_id) from error
            raise _unavailable("crear revisión", project_id) from error
        return self._snapshot_with_children(document, updated, project_id)

    def record_approval(
        self,
        approval_record: ApprovalRecord,
        *,
        expected_version: int,
        idempotency_key: str,
    ) -> ProjectSnapshot:
        _require_idempotency_key(idempotency_key)
        project_id = approval_record.project_id
        fingerprint = _fingerprint_model(
            "record_approval",
            approval_record,
            exclude={"created_at"},
        )
        document = self._document(project_id)
        def operation(transaction: Transaction) -> dict[str, Any]:
            current, _ = self._read(document, project_id, transaction=transaction)
            if _is_replay(current, project_id, idempotency_key, fingerprint):
                return current
            actual_version = _version(current, project_id)
            if actual_version != expected_version:
                raise RepositoryConflictError(
                    "Otra operación cambió el proyecto.",
                    expected_version=expected_version,
                    actual_version=actual_version,
                )
            snapshot = self._snapshot_with_children(
                document, current, project_id, transaction=transaction
            )
            if snapshot.project.active_revision != approval_record.revision:
                raise RepositoryConflictError(
                    "La revisión dejó de ser la revisión activa.",
                    expected_revision=approval_record.revision,
                    active_revision=snapshot.project.active_revision,
                )
            revision = next(
                (
                    item
                    for item in snapshot.revisions
                    if item.number == approval_record.revision
                ),
                None,
            )
            if revision is None:
                raise EntityNotFoundError("revision", str(approval_record.revision))
            if approval_record.id in _approval_ids(current, project_id):
                raise RepositoryConflictError(
                    f"La aprobación {approval_record.id} ya existe.",
                    approval_id=approval_record.id,
                )
            changed_specification = revision.specification.model_copy(
                update={"approval": approval_record.approval}, deep=True
            )
            revision.replace_specification(changed_specification)
            project = snapshot.project
            if (
                approval_record.approval.status is ApprovalStatus.APPROVED
                and project.state is ProjectState.DESIGN_IN_REVIEW
            ):
                project = transition_project(project, ProjectState.DESIGN_APPROVED)
            project = project.model_copy(
                update={"updated_at": self._clock.now()}, deep=True
            )
            updated = _payload(
                project,
                version=actual_version + 1,
                operations=_record_operation(
                    _operations(current, project_id), idempotency_key, fingerprint
                ),
                briefing_version=_briefing_version(current, project_id),
                revision_numbers=_revision_numbers(current, project_id),
                approval_ids=(*_approval_ids(current, project_id), approval_record.id),
                event_sequence=_event_sequence(current, project_id),
                artifact_ids=_artifact_ids(current, project_id),
                expires_at=_expires_at(current, project_id),
            )
            transaction.create(
                _approval_document(document, approval_record.id),
                _approval_payload(
                    approval_record, expires_at=_expires_at(current, project_id)
                ),
            )
            transaction.update(document, updated)
            return updated

        try:
            updated = self._transaction_executor.execute(self._client, operation)
        except Exception as error:
            if isinstance(error, DomainError):
                raise
            if _is_error(error, "AlreadyExists"):
                raise RepositoryConflictError(
                    f"La aprobación {approval_record.id} ya existe.",
                    approval_id=approval_record.id,
                ) from error
            if _is_error(error, "Aborted", "Conflict", "FailedPrecondition"):
                raise RepositoryConflictError(
                    "Otra operación cambió el proyecto.",
                    expected_version=expected_version,
                ) from error
            if _is_error(error, "NotFound"):
                raise EntityNotFoundError("project", project_id) from error
            if _is_retry_exhausted(error):
                raise _retry_exhausted("registrar aprobación", project_id) from error
            raise _unavailable("registrar aprobación", project_id) from error
        return self._snapshot_with_children(document, updated, project_id)

    def add_artifact(
        self,
        artifact: ArtifactMetadata,
        *,
        expected_version: int,
        idempotency_key: str,
    ) -> ProjectSnapshot:
        _require_idempotency_key(idempotency_key)
        project_id = artifact.project_id
        fingerprint = _fingerprint_model("add_artifact", artifact)
        document = self._document(project_id)
        if not _safe_relative_path(artifact.relative_path):
            raise DomainError(
                "ARTIFACT_PATH_INVALID",
                "La ruta del artefacto debe ser relativa y permanecer dentro del proyecto.",
                context={"artifact_id": artifact.id},
            )
        def operation(transaction: Transaction) -> dict[str, Any]:
            current, _ = self._read(document, project_id, transaction=transaction)
            if _is_replay(current, project_id, idempotency_key, fingerprint):
                return current
            actual_version = _version(current, project_id)
            if actual_version != expected_version:
                raise RepositoryConflictError(
                    "Otra operación cambió el proyecto.",
                    expected_version=expected_version,
                    actual_version=actual_version,
                )
            if artifact.revision not in _revision_numbers(current, project_id):
                raise EntityNotFoundError("revision", str(artifact.revision))
            artifact_ids = _artifact_ids(current, project_id)
            if artifact.id in artifact_ids:
                raise RepositoryConflictError(
                    f"El artefacto {artifact.id} ya existe.", artifact_id=artifact.id
                )
            updated = _payload(
                _project(current, project_id),
                version=actual_version + 1,
                operations=_record_operation(
                    _operations(current, project_id), idempotency_key, fingerprint
                ),
                briefing_version=_briefing_version(current, project_id),
                revision_numbers=_revision_numbers(current, project_id),
                approval_ids=_approval_ids(current, project_id),
                event_sequence=_event_sequence(current, project_id),
                artifact_ids=(*artifact_ids, artifact.id),
                expires_at=_expires_at(current, project_id),
            )
            transaction.create(
                _artifact_document(document, artifact.id),
                _artifact_payload(
                    artifact, expires_at=_expires_at(current, project_id)
                ),
            )
            transaction.update(document, updated)
            return updated

        try:
            updated = self._transaction_executor.execute(self._client, operation)
        except Exception as error:
            if isinstance(error, DomainError):
                raise
            if _is_error(error, "AlreadyExists"):
                raise RepositoryConflictError(
                    f"El artefacto {artifact.id} ya existe.",
                    artifact_id=artifact.id,
                ) from error
            if _is_error(error, "Aborted", "Conflict", "FailedPrecondition"):
                raise RepositoryConflictError(
                    "Otra operación cambió el proyecto.",
                    expected_version=expected_version,
                ) from error
            if _is_error(error, "NotFound"):
                raise EntityNotFoundError("project", project_id) from error
            if _is_retry_exhausted(error):
                raise _retry_exhausted("registrar artefacto", project_id) from error
            raise _unavailable("registrar artefacto", project_id) from error
        return self._snapshot_with_children(document, updated, project_id)

    def append(self, event: AuditEvent, *, idempotency_key: str) -> AuditEvent:
        _require_idempotency_key(idempotency_key)
        project_id = event.project_id
        fingerprint = _fingerprint_model(
            "append_event",
            event,
            exclude={"sequence", "occurred_at"},
        )
        document = self._document(project_id)
        def operation(transaction: Transaction) -> AuditEvent:
            current, _ = self._read(document, project_id, transaction=transaction)
            if _is_replay(current, project_id, idempotency_key, fingerprint):
                try:
                    raw, _ = self._read(
                        _event_document(document, event.id),
                        project_id,
                        transaction=transaction,
                    )
                except EntityNotFoundError as error:
                    raise _invalid_document(project_id, "event") from error
                return _event_from_payload(raw, project_id)
            sequence = _event_sequence(current, project_id) + 1
            stored = event.model_copy(
                update={"sequence": sequence, "occurred_at": self._clock.now()},
                deep=True,
            )
            updated = _payload(
                _project(current, project_id),
                version=_version(current, project_id),
                operations=_record_operation(
                    _operations(current, project_id), idempotency_key, fingerprint
                ),
                briefing_version=_briefing_version(current, project_id),
                revision_numbers=_revision_numbers(current, project_id),
                approval_ids=_approval_ids(current, project_id),
                event_sequence=sequence,
                artifact_ids=_artifact_ids(current, project_id),
                expires_at=_expires_at(current, project_id),
            )
            transaction.create(
                _event_document(document, stored.id),
                _event_payload(stored, expires_at=_expires_at(current, project_id)),
            )
            transaction.update(document, updated)
            return stored

        try:
            stored = self._transaction_executor.execute(self._client, operation)
        except Exception as error:
            if isinstance(error, DomainError):
                raise
            if _is_error(error, "AlreadyExists"):
                raise RepositoryConflictError(
                    f"El evento {event.id} ya existe.",
                    event_id=event.id,
                ) from error
            if _is_error(error, "Aborted", "Conflict", "FailedPrecondition"):
                raise RepositoryConflictError(
                    "Otra operación cambió la trayectoria del proyecto.",
                ) from error
            if _is_error(error, "NotFound"):
                raise EntityNotFoundError("project", project_id) from error
            if _is_retry_exhausted(error):
                raise _retry_exhausted("registrar evento", project_id) from error
            raise _unavailable("registrar evento", project_id) from error
        return stored.model_copy(deep=True)

    def list_for_project(
        self,
        project_id: str,
        *,
        after_sequence: int = 0,
    ) -> tuple[AuditEvent, ...]:
        document = self._document(project_id)
        root, _ = self._read(document, project_id)
        maximum = _event_sequence(root, project_id)
        expires_at = _expires_at(root, project_id)
        try:
            snapshots = tuple(
                document.collection("events")
                .order_by(EVENT_SEQUENCE_FIELD)
                .stream()
            )
        except Exception as error:
            raise _unavailable("listar eventos", project_id) from error
        events: list[AuditEvent] = []
        seen: set[int] = set()
        for snapshot in snapshots:
            raw = snapshot.to_dict()
            if not isinstance(raw, dict):
                raise _invalid_document(project_id, "event")
            if raw.get(TTL_FIELD) != expires_at:
                raise _invalid_document(project_id, "event")
            event = _event_from_payload(raw, project_id)
            if event.sequence is None or event.sequence in seen or event.sequence > maximum:
                raise _invalid_document(project_id, "event")
            seen.add(event.sequence)
            if event.sequence > after_sequence:
                events.append(event)
        return tuple(sorted(events, key=lambda item: item.sequence or 0))

    def _document(self, project_id: str) -> DocumentReference:
        if not _PROJECT_ID.fullmatch(project_id):
            raise EntityNotFoundError("project", project_id)
        return self._client.collection(_PROJECTS).document(project_id)

    def _snapshot_with_children(
        self,
        document: DocumentReference,
        payload: Mapping[str, Any],
        project_id: str,
        *,
        transaction: object | None = None,
    ) -> ProjectSnapshot:
        _expires_at(payload, project_id)
        briefing = self._read_briefing(
            document, payload, project_id, transaction=transaction
        )
        revisions = self._read_revisions(
            document, payload, project_id, transaction=transaction
        )
        approvals = self._read_approvals(
            document, payload, project_id, transaction=transaction
        )
        artifacts = self._read_artifacts(
            document, payload, project_id, transaction=transaction
        )
        revisions = _apply_approvals(revisions, approvals, project_id)
        return _snapshot(
            payload,
            project_id,
            briefing=briefing,
            revisions=revisions,
            approvals=approvals,
            artifacts=artifacts,
        )

    def _read_briefing(
        self,
        document: DocumentReference,
        payload: Mapping[str, Any],
        project_id: str,
        *,
        transaction: object | None = None,
    ) -> Briefing:
        version = payload.get("briefing_version")
        if version is None:
            return _project(payload, project_id).briefing.model_copy(deep=True)
        version = _briefing_version(payload, project_id)
        try:
            raw, _ = self._read(
                _briefing_document(document, version),
                project_id,
                transaction=transaction,
            )
        except EntityNotFoundError as error:
            raise _invalid_document(project_id, "briefing") from error
        try:
            briefing = Briefing.model_validate(raw["fields"])
        except (KeyError, TypeError, ValidationError, ValueError) as error:
            raise _invalid_document(project_id, "briefing") from error
        if (
            raw.get("project_id") != project_id
            or raw.get("version") != version
            or raw.get(TTL_FIELD) != _expires_at(payload, project_id)
        ):
            raise _invalid_document(project_id, "briefing")
        return briefing

    def _read_revisions(
        self,
        document: DocumentReference,
        payload: Mapping[str, Any],
        project_id: str,
        *,
        transaction: object | None = None,
    ) -> tuple[Revision, ...]:
        revisions: list[Revision] = []
        for number in _revision_numbers(payload, project_id):
            try:
                raw, _ = self._read(
                    _revision_document(document, number),
                    project_id,
                    transaction=transaction,
                )
            except EntityNotFoundError as error:
                raise _invalid_document(project_id, "revision") from error
            try:
                revision = Revision(
                    project_id=raw["project_id"],
                    number=raw["number"],
                    specification=raw["specification"],
                    created_at=raw["created_at"],
                )
            except (KeyError, TypeError, ValidationError, ValueError) as error:
                raise _invalid_document(project_id, "revision") from error
            if (
                revision.project_id != project_id
                or revision.number != number
                or raw.get(TTL_FIELD) != _expires_at(payload, project_id)
            ):
                raise _invalid_document(project_id, "revision")
            revisions.append(revision)
        return tuple(revisions)

    def _read_approvals(
        self,
        document: DocumentReference,
        payload: Mapping[str, Any],
        project_id: str,
        *,
        transaction: object | None = None,
    ) -> tuple[ApprovalRecord, ...]:
        approvals: list[ApprovalRecord] = []
        for approval_id in _approval_ids(payload, project_id):
            try:
                raw, _ = self._read(
                    _approval_document(document, approval_id),
                    project_id,
                    transaction=transaction,
                )
            except EntityNotFoundError as error:
                raise _invalid_document(project_id, "approval") from error
            try:
                approval = ApprovalRecord(
                    id=raw["id"],
                    project_id=raw["project_id"],
                    revision=raw["revision"],
                    approval=raw["approval"],
                    created_at=raw["created_at"],
                )
            except (KeyError, TypeError, ValidationError, ValueError) as error:
                raise _invalid_document(project_id, "approval") from error
            if (
                approval.id != approval_id
                or approval.project_id != project_id
                or raw.get(TTL_FIELD) != _expires_at(payload, project_id)
            ):
                raise _invalid_document(project_id, "approval")
            approvals.append(approval)
        return tuple(approvals)

    def _read_artifacts(
        self,
        document: DocumentReference,
        payload: Mapping[str, Any],
        project_id: str,
        *,
        transaction: object | None = None,
    ) -> tuple[ArtifactMetadata, ...]:
        artifacts: list[ArtifactMetadata] = []
        revisions = set(_revision_numbers(payload, project_id))
        for artifact_id in _artifact_ids(payload, project_id):
            try:
                raw, _ = self._read(
                    _artifact_document(document, artifact_id),
                    project_id,
                    transaction=transaction,
                )
            except EntityNotFoundError as error:
                raise _invalid_document(project_id, "artifact") from error
            try:
                artifact = ArtifactMetadata.model_validate(raw["metadata"])
            except (KeyError, TypeError, ValidationError, ValueError) as error:
                raise _invalid_document(project_id, "artifact") from error
            if (
                artifact.id != artifact_id
                or artifact.project_id != project_id
                or artifact.revision not in revisions
                or not _safe_relative_path(artifact.relative_path)
                or raw.get(TTL_FIELD) != _expires_at(payload, project_id)
            ):
                raise _invalid_document(project_id, "artifact")
            artifacts.append(artifact)
        return tuple(artifacts)

    @staticmethod
    def _read(
        document: DocumentReference,
        project_id: str,
        *,
        transaction: object | None = None,
    ) -> tuple[dict[str, Any], object]:
        try:
            snapshot = document.get(transaction=transaction)
        except Exception as error:
            raise _unavailable("leer", project_id) from error
        if not snapshot.exists:
            raise EntityNotFoundError("project", project_id)
        payload = snapshot.to_dict()
        if not isinstance(payload, dict):
            raise _invalid_document(project_id)
        return payload, snapshot.update_time


def _payload(
    project: Project,
    *,
    version: int,
    operations: dict[str, dict[str, str]],
    briefing_version: int,
    revision_numbers: tuple[int, ...],
    approval_ids: tuple[str, ...],
    event_sequence: int,
    artifact_ids: tuple[str, ...],
    expires_at: object,
) -> dict[str, Any]:
    return {
        "format_version": 1,
        "version": version,
        "owner_session_id": project.owner_session_id,
        "name": project.name,
        "status": project.state.value,
        "active_revision": project.active_revision,
        "briefing_version": briefing_version,
        "revision_numbers": list(revision_numbers),
        "approval_ids": list(approval_ids),
        "event_sequence": event_sequence,
        "artifact_ids": list(artifact_ids),
        "created_at": project.created_at,
        "updated_at": project.updated_at,
        "project": project.model_dump(mode="python", by_alias=True, exclude={"briefing"}),
        "operations": operations,
        TTL_FIELD: expires_at,
    }


def _snapshot(
    payload: Mapping[str, Any],
    project_id: str,
    *,
    briefing: Briefing | None = None,
    revisions: tuple[Revision, ...] = (),
    approvals: tuple[ApprovalRecord, ...] = (),
    artifacts: tuple[ArtifactMetadata, ...] = (),
) -> ProjectSnapshot:
    project = _project(payload, project_id)
    if briefing is not None:
        project = project.model_copy(update={"briefing": briefing}, deep=True)
    return ProjectSnapshot(
        project=project.model_copy(deep=True),
        version=_version(payload, project_id),
        revisions=tuple(item.model_copy(deep=True) for item in revisions),
        approvals=tuple(item.model_copy(deep=True) for item in approvals),
        artifacts=tuple(item.model_copy(deep=True) for item in artifacts),
    )


def _project(payload: Mapping[str, Any], project_id: str) -> Project:
    try:
        project = Project.model_validate(payload["project"])
    except (KeyError, TypeError, ValidationError, ValueError) as error:
        raise _invalid_document(project_id) from error
    if project.id != project_id or payload.get("owner_session_id") != project.owner_session_id:
        raise _invalid_document(project_id)
    return project


def _briefing_document(document: DocumentReference, version: int) -> DocumentReference:
    return document.collection("briefings").document(f"v{version:06d}")


def _revision_document(document: DocumentReference, number: int) -> DocumentReference:
    return document.collection("revisions").document(f"r{number:06d}")


def _approval_document(document: DocumentReference, approval_id: str) -> DocumentReference:
    return document.collection("approvals").document(approval_id)


def _event_document(document: DocumentReference, event_id: str) -> DocumentReference:
    return document.collection("events").document(event_id)


def _artifact_document(document: DocumentReference, artifact_id: str) -> DocumentReference:
    return document.collection("artifacts").document(artifact_id)


def _briefing_payload(
    briefing: Briefing,
    project: Project,
    *,
    version: int,
    expires_at: object,
) -> dict[str, Any]:
    return {
        "format_version": 1,
        "project_id": project.id,
        "version": version,
        "fields": briefing.model_dump(mode="python", by_alias=True),
        "missing_fields": list(briefing.open_questions),
        "confirmed": briefing.confirmed,
        "created_at": project.updated_at,
        TTL_FIELD: expires_at,
    }


def _revision_payload(revision: Revision, *, expires_at: object) -> dict[str, Any]:
    specification = revision.specification
    return {
        "format_version": 1,
        "project_id": revision.project_id,
        "number": revision.number,
        "schema_version": specification.schema_version,
        "specification": specification.model_dump(mode="python", by_alias=True),
        "approval_status": specification.approval.status.value,
        "source_revision": revision.number - 1 if revision.number > 1 else None,
        "created_at": revision.created_at,
        TTL_FIELD: expires_at,
    }


def _approval_payload(
    record: ApprovalRecord, *, expires_at: object
) -> dict[str, Any]:
    return {
        "format_version": 1,
        "id": record.id,
        "project_id": record.project_id,
        "revision": record.revision,
        "decision": record.approval.status.value,
        "decided_by": record.approval.decided_by,
        "decided_at": record.approval.decided_at,
        "note": record.approval.note,
        "approval": record.approval.model_dump(mode="python", by_alias=True),
        "created_at": record.created_at,
        TTL_FIELD: expires_at,
    }


def _event_payload(event: AuditEvent, *, expires_at: object) -> dict[str, Any]:
    return {
        "format_version": 1,
        **event.model_dump(mode="python", by_alias=True),
        "type": event.event_type.value,
        "actor": event.actor_id,
        "created_at": event.occurred_at,
        TTL_FIELD: expires_at,
    }


def _artifact_payload(
    artifact: ArtifactMetadata, *, expires_at: object
) -> dict[str, Any]:
    return {
        "format_version": 1,
        "id": artifact.id,
        "project_id": artifact.project_id,
        "revision": artifact.revision,
        "relative_path": artifact.relative_path,
        "sha256": artifact.sha256,
        "framework": artifact.framework,
        "template_version": artifact.template_version,
        "validation_status": artifact.validation_status,
        "metadata": artifact.model_dump(mode="python", by_alias=True),
        TTL_FIELD: expires_at,
    }


def _safe_relative_path(value: str) -> bool:
    if "\\" in value or ":" in value or "\x00" in value:
        return False
    path = PurePosixPath(value)
    return (
        bool(path.parts)
        and not path.is_absolute()
        and all(
            part not in {"", ".", ".."}
            and part.rstrip(" .") == part
            and part.split(".", 1)[0].upper() not in _WINDOWS_RESERVED
            for part in path.parts
        )
    )


def _event_from_payload(payload: Mapping[str, Any], project_id: str) -> AuditEvent:
    try:
        event = AuditEvent.model_validate(
            {
                "id": payload["id"],
                "project_id": payload["project_id"],
                "event_type": payload["event_type"],
                "actor_id": payload["actor_id"],
                "sequence": payload["sequence"],
                "summary": payload.get("summary", ""),
                "revision": payload.get("revision"),
                "occurred_at": payload["occurred_at"],
                "details": payload.get("details", {}),
            }
        )
    except (KeyError, TypeError, ValidationError, ValueError) as error:
        raise _invalid_document(project_id, "event") from error
    if event.project_id != project_id:
        raise _invalid_document(project_id, "event")
    return event


def _apply_approvals(
    revisions: tuple[Revision, ...],
    approvals: tuple[ApprovalRecord, ...],
    project_id: str,
) -> tuple[Revision, ...]:
    changed = list(revisions)
    for approval in approvals:
        index = next(
            (index for index, item in enumerate(changed) if item.number == approval.revision),
            None,
        )
        if index is None:
            raise _invalid_document(project_id, "approval")
        specification = changed[index].specification.model_copy(
            update={"approval": approval.approval},
            deep=True,
        )
        try:
            changed[index] = changed[index].replace_specification(specification)
        except DomainError as error:
            raise _invalid_document(project_id, "approval") from error
    return tuple(changed)


def _version(payload: Mapping[str, Any], project_id: str) -> int:
    version = payload.get("version")
    if not isinstance(version, int) or isinstance(version, bool) or version < 1:
        raise _invalid_document(project_id)
    return version


def _expires_at(payload: Mapping[str, Any], project_id: str) -> datetime:
    value = payload.get(TTL_FIELD)
    if (
        not isinstance(value, datetime)
        or value.tzinfo is None
        or value.utcoffset() is None
    ):
        raise _invalid_document(project_id)
    return value


def _briefing_version(payload: Mapping[str, Any], project_id: str) -> int:
    version = payload.get("briefing_version")
    if not isinstance(version, int) or isinstance(version, bool) or version < 1:
        raise _invalid_document(project_id)
    return version


def _revision_numbers(payload: Mapping[str, Any], project_id: str) -> tuple[int, ...]:
    raw = payload.get("revision_numbers")
    if raw is None:
        active = payload.get("active_revision")
        return (active,) if isinstance(active, int) and not isinstance(active, bool) else ()
    if not isinstance(raw, list):
        raise _invalid_document(project_id)
    numbers: list[int] = []
    for number in raw:
        if (
            not isinstance(number, int)
            or isinstance(number, bool)
            or number < 1
            or number in numbers
        ):
            raise _invalid_document(project_id)
        numbers.append(number)
    if numbers != sorted(numbers):
        raise _invalid_document(project_id)
    active = payload.get("active_revision")
    if numbers and active != numbers[-1]:
        raise _invalid_document(project_id)
    if not numbers and active is not None:
        raise _invalid_document(project_id)
    return tuple(numbers)


def _approval_ids(payload: Mapping[str, Any], project_id: str) -> tuple[str, ...]:
    raw = payload.get("approval_ids", [])
    if not isinstance(raw, list):
        raise _invalid_document(project_id)
    identifiers: list[str] = []
    for identifier in raw:
        if (
            not isinstance(identifier, str)
            or not _PROJECT_ID.fullmatch(identifier)
            or identifier in identifiers
        ):
            raise _invalid_document(project_id)
        identifiers.append(identifier)
    return tuple(identifiers)


def _artifact_ids(payload: Mapping[str, Any], project_id: str) -> tuple[str, ...]:
    raw = payload.get("artifact_ids", [])
    if not isinstance(raw, list):
        raise _invalid_document(project_id)
    identifiers: list[str] = []
    for identifier in raw:
        if (
            not isinstance(identifier, str)
            or not _PROJECT_ID.fullmatch(identifier)
            or identifier in identifiers
        ):
            raise _invalid_document(project_id)
        identifiers.append(identifier)
    return tuple(identifiers)


def _event_sequence(payload: Mapping[str, Any], project_id: str) -> int:
    sequence = payload.get("event_sequence", 0)
    if not isinstance(sequence, int) or isinstance(sequence, bool) or sequence < 0:
        raise _invalid_document(project_id)
    return sequence


def _operations(
    payload: Mapping[str, Any],
    project_id: str,
) -> dict[str, dict[str, str]]:
    operations = payload.get("operations")
    if not isinstance(operations, dict):
        raise _invalid_document(project_id)
    result: dict[str, dict[str, str]] = {}
    for key, value in operations.items():
        if not isinstance(key, str) or not isinstance(value, dict):
            raise _invalid_document(project_id)
        key_hash = value.get("key_sha256")
        fingerprint = value.get("fingerprint")
        if not isinstance(key_hash, str) or not isinstance(fingerprint, str):
            raise _invalid_document(project_id)
        result[key] = {"key_sha256": key_hash, "fingerprint": fingerprint}
    return result


def _is_replay(
    payload: Mapping[str, Any],
    project_id: str,
    key: str,
    fingerprint: str,
) -> bool:
    operation = _operations(payload, project_id).get(_operation_id(key))
    if operation is None:
        return False
    if operation["key_sha256"] != _key_hash(key) or operation["fingerprint"] != fingerprint:
        raise IdempotencyConflictError(key)
    return True


def _record_operation(
    operations: dict[str, dict[str, str]],
    key: str,
    fingerprint: str,
) -> dict[str, dict[str, str]]:
    changed = dict(operations)
    changed[_operation_id(key)] = {
        "key_sha256": _key_hash(key),
        "fingerprint": fingerprint,
    }
    return changed


def _operation_id(key: str) -> str:
    return f"op_{hashlib.sha256(key.encode('utf-8')).hexdigest()[:32]}"


def _key_hash(key: str) -> str:
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


def _require_idempotency_key(key: str) -> None:
    if not key.strip() or len(key) > 200:
        raise IdempotencyConflictError(key)


def _fingerprint(action: str, value: Project, *, exclude: set[str] | None = None) -> str:
    return _fingerprint_model(action, value, exclude=exclude)


def _fingerprint_model(
    action: str,
    value: Project | Revision | ApprovalRecord | ArtifactMetadata | AuditEvent,
    *,
    exclude: set[str] | None = None,
) -> str:
    payload = value.model_dump(mode="json", by_alias=True, exclude=exclude or set())
    canonical = json.dumps(
        {"action": action, "value": payload},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _last_update_option(update_time: object) -> object:
    from google.cloud.firestore_v1 import _helpers

    return _helpers.LastUpdateOption(update_time)


def _is_error(error: Exception, *names: str) -> bool:
    return error.__class__.__name__ in names


def _is_retry_exhausted(error: Exception) -> bool:
    return _is_error(
        error,
        "TransactionRetryExhaustedError",
        "RetryError",
    )


def _retry_exhausted(operation: str, project_id: str) -> DomainError:
    return DomainError(
        "FIRESTORE_TRANSACTION_RETRY_EXHAUSTED",
        "Firestore agotó los reintentos de una operación crítica.",
        context={"operation": operation, "project_id": project_id},
    )


def _unavailable(operation: str, project_id: str) -> DomainError:
    return DomainError(
        "FIRESTORE_UNAVAILABLE",
        "Firestore no pudo completar la operación del proyecto.",
        context={"operation": operation, "project_id": project_id},
    )


def _invalid_document(project_id: str, entity: str = "project") -> DomainError:
    context: dict[str, Any] = {"project_id": project_id}
    if entity != "project":
        context["entity"] = entity
    return DomainError(
        "FIRESTORE_DOCUMENT_INVALID",
        "Un documento Firestore no cumple el contrato de persistencia.",
        context=context,
    )
