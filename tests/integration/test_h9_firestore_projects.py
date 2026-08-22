from __future__ import annotations

import json
from collections.abc import Callable
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, TypeVar, cast

import pytest

from infrastructure.firestore import FirestoreProjectRepository
from infrastructure.firestore.retention import DemoRetentionPolicy
from infrastructure.firestore.transactions import Transaction, TransactionExecutor
from infrastructure.local.clock import FrozenClock
from studio.domain.enums import ApprovalStatus, AuditEventType, ProjectState
from studio.domain.errors import (
    DomainError,
    EntityNotFoundError,
    IdempotencyConflictError,
    ProjectAccessDeniedError,
    RepositoryConflictError,
    RevisionImmutableError,
)
from studio.domain.models import (
    Approval,
    ApprovalRecord,
    ArtifactMetadata,
    AuditEvent,
    Briefing,
    Project,
    Revision,
    TaskmasterSpecification,
)

NOW = datetime(2026, 8, 14, 15, 0, tzinfo=UTC)
LATER = datetime(2026, 8, 14, 16, 0, tzinfo=UTC)
FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "academic_delivery_specification.json"
ResultT = TypeVar("ResultT")


class AlreadyExists(Exception):
    pass


class FailedPrecondition(Exception):
    pass


class BackendUnavailable(Exception):
    pass


class RetryError(Exception):
    pass


class FakeSnapshot:
    def __init__(self, data: dict[str, Any] | None, update_time: int) -> None:
        self.exists = data is not None
        self.update_time = update_time
        self._data = deepcopy(data)

    def to_dict(self) -> dict[str, Any] | None:
        return deepcopy(self._data)


class FakeDocument:
    def __init__(self) -> None:
        self.data: dict[str, Any] | None = None
        self.update_time = 0
        self.force_conflict = False
        self.fail_reads = False
        self.create_calls = 0
        self.update_calls = 0
        self.collections: dict[str, FakeCollection] = {}

    def create(self, document_data: dict[str, Any]) -> object:
        self.create_calls += 1
        if self.data is not None:
            raise AlreadyExists
        self.data = deepcopy(document_data)
        self.update_time += 1
        return object()

    def get(self, *, transaction: object | None = None) -> FakeSnapshot:
        if self.fail_reads:
            raise BackendUnavailable("private backend details")
        if isinstance(transaction, FakeTransaction):
            transaction.read_versions[self] = self.update_time
        return FakeSnapshot(self.data, self.update_time)

    def update(self, field_updates: dict[str, Any], option: object | None = None) -> object:
        self.update_calls += 1
        if self.data is None:
            raise EntityNotFoundError("project", "missing")
        if self.force_conflict or option != self.update_time:
            raise FailedPrecondition
        self.data.update(deepcopy(field_updates))
        self.update_time += 1
        return object()

    def collection(self, collection_id: str) -> FakeCollection:
        return self.collections.setdefault(collection_id, FakeCollection())


class FakeCollection:
    def __init__(self) -> None:
        self.documents: dict[str, FakeDocument] = {}
        self.fail_stream = False

    def document(self, document_id: str) -> FakeDocument:
        return self.documents.setdefault(document_id, FakeDocument())

    def order_by(self, field_path: str) -> FakeQuery:
        return FakeQuery(self, field_path)


class FakeQuery:
    def __init__(self, collection: FakeCollection, field_path: str) -> None:
        self.collection = collection
        self.field_path = field_path

    def stream(self) -> tuple[FakeSnapshot, ...]:
        if self.collection.fail_stream:
            raise BackendUnavailable("private stream details")
        documents = [
            document
            for document in self.collection.documents.values()
            if document.data is not None
        ]
        documents.sort(
            key=lambda document: document.data.get(self.field_path, 0)
            if document.data is not None
            else 0
        )
        return tuple(
            FakeSnapshot(document.data, document.update_time) for document in documents
        )


class FakeClient:
    def __init__(self) -> None:
        self.projects = FakeCollection()
        self.collections: list[str] = []
        self.transaction_attempts: list[int] = []

    def collection(self, collection_id: str) -> FakeCollection:
        self.collections.append(collection_id)
        assert collection_id == "projects"
        return self.projects

    def batch(self) -> FakeBatch:
        return FakeBatch()

    def transaction(self, *, max_attempts: int = 5) -> FakeTransaction:
        self.transaction_attempts.append(max_attempts)
        return FakeTransaction()

    def recursive_delete(self, reference: FakeDocument, *, chunk_size: int = 5000) -> int:
        del chunk_size
        deleted = _delete_fake_document(reference)
        return deleted


class FakeBatch:
    def __init__(self) -> None:
        self.actions: list[tuple[str, FakeDocument, dict[str, Any], object | None]] = []

    def create(self, reference: FakeDocument, document_data: dict[str, Any]) -> object:
        self.actions.append(("create", reference, deepcopy(document_data), None))
        return self

    def update(
        self,
        reference: FakeDocument,
        field_updates: dict[str, Any],
        option: object | None = None,
    ) -> object:
        self.actions.append(("update", reference, deepcopy(field_updates), option))
        return self

    def commit(self) -> object:
        for action, document, _, option in self.actions:
            if action == "create" and document.data is not None:
                raise AlreadyExists
            if action == "update" and (
                document.data is None
                or document.force_conflict
                or option != document.update_time
            ):
                raise FailedPrecondition
        for action, document, payload, option in self.actions:
            if action == "create":
                document.create(payload)
            else:
                document.update(payload, option=option)
        return object()


class FakeTransaction(FakeBatch):
    def __init__(self) -> None:
        super().__init__()
        self.read_versions: dict[FakeDocument, int] = {}

    def commit(self) -> object:
        for action, document, _, _ in self.actions:
            if action == "create" and document.data is not None:
                raise AlreadyExists
            if action == "update" and (
                document.data is None
                or document.force_conflict
                or self.read_versions.get(document) != document.update_time
            ):
                raise FailedPrecondition
        for action, document, payload, _ in self.actions:
            if action == "create":
                document.create(payload)
            else:
                assert document.data is not None
                document.data.update(deepcopy(payload))
                document.update_calls += 1
                document.update_time += 1
        return object()


class ImmediateTransactionExecutor:
    max_attempts = 5

    def execute(
        self,
        client: FakeClient,
        operation: Callable[[Transaction], ResultT],
    ) -> ResultT:
        transaction = client.transaction(max_attempts=self.max_attempts)
        result = operation(cast(Transaction, transaction))
        transaction.commit()
        return result


class RetryOnceTransactionExecutor:
    max_attempts = 3

    def __init__(self) -> None:
        self.callback_calls = 0

    def execute(
        self,
        client: FakeClient,
        operation: Callable[[Transaction], ResultT],
    ) -> ResultT:
        abandoned = client.transaction(max_attempts=self.max_attempts)
        operation(cast(Transaction, abandoned))
        self.callback_calls += 1
        retried = client.transaction(max_attempts=self.max_attempts)
        result = operation(cast(Transaction, retried))
        self.callback_calls += 1
        retried.commit()
        return result


class ExhaustedTransactionExecutor:
    max_attempts = 2

    def execute(
        self,
        client: FakeClient,
        operation: Callable[[Transaction], ResultT],
    ) -> ResultT:
        client.transaction(max_attempts=self.max_attempts)
        raise RetryError("private retry internals")


def project(
    *,
    name: str = "Academic delivery",
    owner: str = "session-owner",
    state: ProjectState = ProjectState.IDEA,
) -> Project:
    return Project(
        id="academic_delivery_project",
        name=name,
        owner_session_id=owner,
        state=state,
    )


def specification(*, approved: bool = True) -> TaskmasterSpecification:
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
    if not approved:
        payload["approval"] = {
            "status": "draft",
            "decided_by": None,
            "decided_at": None,
            "note": "",
        }
    return TaskmasterSpecification.model_validate(payload)


def revision(
    number: int = 1,
    *,
    project_id: str = "academic_delivery_project",
    approved: bool = True,
) -> Revision:
    return Revision(
        project_id=project_id,
        number=number,
        specification=specification(approved=approved).model_copy(
            update={"revision": number}, deep=True
        ),
        created_at=LATER,
    )


def approval(
    *,
    status: ApprovalStatus = ApprovalStatus.APPROVED,
    approval_id: str = "approval_revision_one",
) -> ApprovalRecord:
    return ApprovalRecord(
        id=approval_id,
        project_id="academic_delivery_project",
        revision=1,
        approval=Approval(
            status=status,
            decided_by="session-owner",
            decided_at=LATER,
            note="Reviewed explicitly by the owner.",
        ),
        created_at=LATER,
    )


def event(event_id: str, *, summary: str = "Auditable action") -> AuditEvent:
    return AuditEvent(
        id=event_id,
        project_id="academic_delivery_project",
        event_type=AuditEventType.REVISION_CREATED,
        actor_id="studio",
        summary=summary,
        revision=1,
        details={"reason": "contract test"},
    )


def artifact(
    *,
    artifact_id: str = "manifest_artifact",
    project_id: str = "academic_delivery_project",
    revision_number: int = 1,
    relative_path: str = "academic_delivery_project/revision-1/taskmaster.manifest.json",
) -> ArtifactMetadata:
    return ArtifactMetadata(
        id=artifact_id,
        project_id=project_id,
        revision=revision_number,
        relative_path=relative_path,
        sha256="a" * 64,
        framework="google_adk",
        template_version="1.0.0",
        validation_status="valid",
    )


def repository(
    client: FakeClient,
    *,
    now: datetime = NOW,
    transaction_executor: object | None = None,
    retention_policy: DemoRetentionPolicy | None = None,
) -> FirestoreProjectRepository:
    return FirestoreProjectRepository(
        cast(Any, client),
        FrozenClock(now),
        write_option_factory=lambda update_time: update_time,
        transaction_executor=cast(
            TransactionExecutor,
            transaction_executor or ImmediateTransactionExecutor(),
        ),
        retention_policy=retention_policy,
    )


def _delete_fake_document(document: FakeDocument) -> int:
    deleted = 1 if document.data is not None else 0
    document.data = None
    document.update_time += 1
    for collection in document.collections.values():
        for child in collection.documents.values():
            deleted += _delete_fake_document(child)
    document.collections.clear()
    return deleted


def test_create_persists_only_project_root_and_query_fields() -> None:
    client = FakeClient()
    result = repository(client).create(project(), idempotency_key="create-project")
    stored = client.projects.documents[result.project.id].data

    assert result.version == 1
    assert result.project.created_at == NOW
    assert stored is not None
    assert stored["format_version"] == 1
    assert stored["owner_session_id"] == "session-owner"
    assert stored["status"] == "idea"
    assert stored["version"] == 1
    assert stored["project"]["id"] == "academic_delivery_project"
    assert "briefing" not in stored["project"]
    assert "v000001" in client.projects.documents[result.project.id].collections[
        "briefings"
    ].documents
    assert set(client.collections) == {"projects"}
    assert result.revisions == ()
    assert result.approvals == ()
    assert result.artifacts == ()


def test_create_replay_is_idempotent_without_storing_raw_key() -> None:
    client = FakeClient()
    first = repository(client).create(project(), idempotency_key="sensitive-request-key")
    replay = repository(client).create(project(), idempotency_key="sensitive-request-key")
    stored = client.projects.documents[first.project.id].data

    assert replay == first
    assert stored is not None
    assert "sensitive-request-key" not in str(stored)
    assert len(stored["operations"]) == 1


def test_create_rejects_same_idempotency_key_with_different_content() -> None:
    client = FakeClient()
    repository(client).create(project(), idempotency_key="create-project")

    with pytest.raises(IdempotencyConflictError):
        repository(client).create(
            project(name="Different project name"),
            idempotency_key="create-project",
        )


def test_create_rejects_existing_project_from_another_operation() -> None:
    client = FakeClient()
    repository(client).create(project(), idempotency_key="first-create")

    with pytest.raises(RepositoryConflictError):
        repository(client).create(project(), idempotency_key="second-create")


def test_get_returns_defensive_snapshot_and_enforces_owner() -> None:
    client = FakeClient()
    created = repository(client).create(project(), idempotency_key="create")

    allowed = repository(client).get(
        created.project.id,
        owner_session_id="session-owner",
    )
    allowed.project.name = "Changed outside repository"
    restored = repository(client).get(created.project.id)

    assert restored.project.name == "Academic delivery"
    with pytest.raises(ProjectAccessDeniedError):
        repository(client).get(
            created.project.id,
            owner_session_id="another-session",
        )


def test_save_uses_version_and_last_update_precondition() -> None:
    client = FakeClient()
    created = repository(client).create(project(), idempotency_key="create")
    changed = created.project.model_copy(update={"name": "Updated project"})

    saved = repository(client, now=LATER).save(
        changed,
        expected_version=created.version,
        idempotency_key="save-project",
    )
    replay = repository(client, now=LATER).save(
        changed,
        expected_version=created.version,
        idempotency_key="save-project",
    )

    assert saved.version == replay.version == 2
    assert saved.project.name == "Updated project"
    assert saved.project.updated_at == LATER
    assert client.projects.documents[created.project.id].update_calls == 1


def test_save_rejects_stale_domain_version() -> None:
    client = FakeClient()
    created = repository(client).create(project(), idempotency_key="create")

    with pytest.raises(RepositoryConflictError) as captured:
        repository(client).save(
            created.project,
            expected_version=2,
            idempotency_key="stale-save",
        )

    assert captured.value.context == {"expected_version": 2, "actual_version": 1}


def test_save_translates_firestore_precondition_conflict() -> None:
    client = FakeClient()
    created = repository(client).create(project(), idempotency_key="create")
    client.projects.documents[created.project.id].force_conflict = True

    with pytest.raises(RepositoryConflictError) as captured:
        repository(client).save(
            created.project,
            expected_version=1,
            idempotency_key="concurrent-save",
        )

    assert captured.value.context == {"expected_version": 1}


def test_missing_and_invalid_project_ids_are_not_read() -> None:
    client = FakeClient()

    with pytest.raises(EntityNotFoundError):
        repository(client).get("missing_project")
    with pytest.raises(EntityNotFoundError):
        repository(client).get("../outside")


def test_malformed_document_is_rejected_without_returning_partial_data() -> None:
    client = FakeClient()
    document = client.projects.document("academic_delivery_project")
    document.data = {"version": 1, "owner_session_id": "session-owner"}

    with pytest.raises(DomainError) as captured:
        repository(client).get("academic_delivery_project")

    assert captured.value.code == "FIRESTORE_DOCUMENT_INVALID"
    assert captured.value.context == {"project_id": "academic_delivery_project"}


def test_backend_error_is_sanitized() -> None:
    client = FakeClient()
    document = client.projects.document("academic_delivery_project")
    document.fail_reads = True

    with pytest.raises(DomainError) as captured:
        repository(client).get("academic_delivery_project")

    assert captured.value.code == "FIRESTORE_UNAVAILABLE"
    assert "private backend" not in str(captured.value)


def test_briefing_is_versioned_in_its_own_subcollection() -> None:
    client = FakeClient()
    created = repository(client).create(project(), idempotency_key="create")
    root = client.projects.documents[created.project.id]
    first = root.collections["briefings"].documents["v000001"]
    changed_briefing = Briefing(
        problem="Manual delivery coordination",
        goal="Coordinate every delivery",
        confirmed=True,
        confirmed_by="session-owner",
        confirmed_at=LATER,
    )
    changed = created.project.model_copy(update={"briefing": changed_briefing}, deep=True)

    saved = repository(client, now=LATER).save(
        changed,
        expected_version=1,
        idempotency_key="save-briefing",
    )
    second = root.collections["briefings"].documents["v000002"]

    assert first.data is not None and first.data["confirmed"] is False
    assert second.data is not None and second.data["confirmed"] is True
    assert second.data["fields"]["goal"] == "Coordinate every delivery"
    assert root.data is not None and root.data["briefing_version"] == 2
    assert saved.project.briefing == changed_briefing


def test_get_uses_authoritative_briefing_document() -> None:
    client = FakeClient()
    created = repository(client).create(project(), idempotency_key="create")
    root = client.projects.documents[created.project.id]
    assert root.data is not None
    root.data["project"]["briefing"] = Briefing(
        goal="stale embedded value"
    ).model_dump(mode="python")
    briefing = root.collections["briefings"].documents["v000001"]
    assert briefing.data is not None
    briefing.data["fields"]["goal"] = "authoritative child value"

    restored = repository(client).get(created.project.id)

    assert restored.project.briefing.goal == "authoritative child value"


def test_unrelated_project_save_does_not_duplicate_unchanged_briefing() -> None:
    client = FakeClient()
    created = repository(client).create(project(), idempotency_key="create")
    changed = created.project.model_copy(update={"name": "Renamed project"}, deep=True)

    saved = repository(client, now=LATER).save(
        changed,
        expected_version=1,
        idempotency_key="rename-project",
    )
    root = client.projects.documents[created.project.id]

    assert saved.version == 2
    assert root.data is not None and root.data["briefing_version"] == 1
    assert set(root.collections["briefings"].documents) == {"v000001"}


def test_revision_is_created_immutably_and_restored_in_snapshot() -> None:
    client = FakeClient()
    created = repository(client).create(project(), idempotency_key="create")

    saved = repository(client, now=LATER).add_revision(
        created.project.id,
        revision(),
        expected_version=created.version,
        idempotency_key="revision-one",
    )
    restored = repository(client).get(created.project.id)
    document = client.projects.documents[created.project.id]
    stored_revision = document.collections["revisions"].documents["r000001"]

    assert saved.version == restored.version == 2
    assert restored.project.active_revision == 1
    assert [item.number for item in restored.revisions] == [1]
    assert stored_revision.create_calls == 1
    assert stored_revision.update_calls == 0
    assert stored_revision.data is not None
    assert stored_revision.data["approval_status"] == "approved"
    assert stored_revision.data["schema_version"] == "1.0.0"


def test_revision_replay_does_not_duplicate_child_or_root_update() -> None:
    client = FakeClient()
    created = repository(client).create(project(), idempotency_key="create")
    first = repository(client).add_revision(
        created.project.id,
        revision(),
        expected_version=1,
        idempotency_key="revision-one",
    )
    replay = repository(client).add_revision(
        created.project.id,
        revision(),
        expected_version=1,
        idempotency_key="revision-one",
    )
    root = client.projects.documents[created.project.id]
    child = root.collections["revisions"].documents["r000001"]

    assert first == replay
    assert root.update_calls == 1
    assert child.create_calls == 1


def test_revision_rejects_stale_version_and_foreign_project() -> None:
    client = FakeClient()
    created = repository(client).create(project(), idempotency_key="create")

    with pytest.raises(RepositoryConflictError) as stale:
        repository(client).add_revision(
            created.project.id,
            revision(),
            expected_version=2,
            idempotency_key="stale-revision",
        )
    with pytest.raises(RepositoryConflictError) as foreign:
        repository(client).add_revision(
            created.project.id,
            revision(project_id="foreign_project"),
            expected_version=1,
            idempotency_key="foreign-revision",
        )

    assert stale.value.context == {"expected_version": 2, "actual_version": 1}
    assert foreign.value.context["revision_project_id"] == "foreign_project"


def test_grouped_revision_write_leaves_no_child_when_root_conflicts() -> None:
    client = FakeClient()
    created = repository(client).create(project(), idempotency_key="create")
    root = client.projects.documents[created.project.id]
    root.force_conflict = True

    with pytest.raises(RepositoryConflictError):
        repository(client).add_revision(
            created.project.id,
            revision(),
            expected_version=1,
            idempotency_key="concurrent-revision",
        )

    child = root.collections["revisions"].documents["r000001"]
    assert child.data is None
    assert root.data is not None and root.data["active_revision"] is None


def test_malformed_briefing_and_revision_documents_are_rejected() -> None:
    client = FakeClient()
    created = repository(client).create(project(), idempotency_key="create")
    root = client.projects.documents[created.project.id]
    briefing = root.collections["briefings"].documents["v000001"]
    assert briefing.data is not None
    briefing.data["fields"] = {"unexpected": True}

    with pytest.raises(DomainError) as invalid_briefing:
        repository(client).get(created.project.id)
    assert invalid_briefing.value.context["entity"] == "briefing"

    briefing.data["fields"] = Briefing().model_dump(mode="python")
    root.data = root.data or {}
    root.data["active_revision"] = 1
    root.data["revision_numbers"] = [1]
    revision_document = root.collection("revisions").document("r000001")
    revision_document.data = {"project_id": created.project.id, "number": 1}

    with pytest.raises(DomainError) as invalid_revision:
        repository(client).get(created.project.id)
    assert invalid_revision.value.context["entity"] == "revision"


def test_approval_is_separate_and_applied_without_mutating_revision_document() -> None:
    client = FakeClient()
    created = repository(client).create(
        project(state=ProjectState.DESIGN_IN_REVIEW),
        idempotency_key="create",
    )
    with_revision = repository(client).add_revision(
        created.project.id,
        revision(approved=False),
        expected_version=1,
        idempotency_key="revision-one",
    )

    saved = repository(client, now=LATER).record_approval(
        approval(),
        expected_version=with_revision.version,
        idempotency_key="approve-one",
    )
    restored = repository(client).get(created.project.id)
    root = client.projects.documents[created.project.id]
    raw_revision = root.collections["revisions"].documents["r000001"]
    raw_approval = root.collections["approvals"].documents["approval_revision_one"]

    assert saved.version == restored.version == 3
    assert restored.project.state is ProjectState.DESIGN_APPROVED
    assert restored.revisions[0].specification.approval.status is ApprovalStatus.APPROVED
    assert restored.approvals == (approval(),)
    assert raw_revision.data is not None
    assert raw_revision.data["approval_status"] == "draft"
    assert raw_revision.update_calls == 0
    assert raw_approval.data is not None
    assert raw_approval.data["decision"] == "approved"


def test_approval_replay_is_idempotent_and_approved_revision_stays_immutable() -> None:
    client = FakeClient()
    created = repository(client).create(
        project(state=ProjectState.DESIGN_IN_REVIEW),
        idempotency_key="create",
    )
    with_revision = repository(client).add_revision(
        created.project.id,
        revision(approved=False),
        expected_version=1,
        idempotency_key="revision-one",
    )
    first = repository(client, now=LATER).record_approval(
        approval(),
        expected_version=with_revision.version,
        idempotency_key="approve-one",
    )
    replay = repository(client, now=LATER).record_approval(
        approval(),
        expected_version=with_revision.version,
        idempotency_key="approve-one",
    )
    root = client.projects.documents[created.project.id]
    approval_document = root.collections["approvals"].documents[
        "approval_revision_one"
    ]

    assert first == replay
    assert approval_document.create_calls == 1
    with pytest.raises(RevisionImmutableError):
        repository(client, now=LATER).record_approval(
            approval(
                status=ApprovalStatus.REJECTED,
                approval_id="second_decision",
            ),
            expected_version=first.version,
            idempotency_key="reject-approved-revision",
        )


def test_approval_rejects_stale_version_and_inactive_revision() -> None:
    client = FakeClient()
    created = repository(client).create(
        project(state=ProjectState.DESIGN_IN_REVIEW),
        idempotency_key="create",
    )
    with_revision = repository(client).add_revision(
        created.project.id,
        revision(approved=False),
        expected_version=1,
        idempotency_key="revision-one",
    )

    with pytest.raises(RepositoryConflictError) as stale:
        repository(client).record_approval(
            approval(),
            expected_version=1,
            idempotency_key="stale-approval",
        )
    inactive = approval().model_copy(update={"revision": 2}, deep=True)
    with pytest.raises(RepositoryConflictError) as wrong_revision:
        repository(client).record_approval(
            inactive,
            expected_version=with_revision.version,
            idempotency_key="inactive-approval",
        )

    assert stale.value.context == {"expected_version": 1, "actual_version": 2}
    assert wrong_revision.value.context == {
        "expected_revision": 2,
        "active_revision": 1,
    }


def test_events_are_sequential_filterable_and_idempotent() -> None:
    client = FakeClient()
    created = repository(client).create(project(), idempotency_key="create")

    first = repository(client, now=NOW).append(
        event("event_one"), idempotency_key="append-one"
    )
    replay = repository(client, now=NOW).append(
        event("event_one"), idempotency_key="append-one"
    )
    second = repository(client, now=LATER).append(
        event("event_two"), idempotency_key="append-two"
    )
    root = client.projects.documents[created.project.id]

    assert first.sequence == replay.sequence == 1
    assert second.sequence == 2
    assert first.occurred_at == NOW and second.occurred_at == LATER
    assert repository(client).get(created.project.id).version == 1
    assert [
        item.id for item in repository(client).list_for_project(created.project.id)
    ] == ["event_one", "event_two"]
    assert [
        item.id
        for item in repository(client).list_for_project(
            created.project.id, after_sequence=1
        )
    ] == ["event_two"]
    assert root.data is not None and root.data["event_sequence"] == 2
    assert root.collections["events"].documents["event_one"].create_calls == 1


def test_event_conflict_is_atomic_and_stream_errors_are_sanitized() -> None:
    client = FakeClient()
    created = repository(client).create(project(), idempotency_key="create")
    root = client.projects.documents[created.project.id]
    root.force_conflict = True

    with pytest.raises(RepositoryConflictError):
        repository(client).append(
            event("event_one"),
            idempotency_key="concurrent-event",
        )
    assert root.collections["events"].documents["event_one"].data is None
    assert root.data is not None and root.data["event_sequence"] == 0

    root.force_conflict = False
    root.collections["events"].fail_stream = True
    with pytest.raises(DomainError) as captured:
        repository(client).list_for_project(created.project.id)
    assert captured.value.code == "FIRESTORE_UNAVAILABLE"
    assert "private stream" not in str(captured.value)


def test_malformed_approval_and_event_documents_are_rejected() -> None:
    client = FakeClient()
    created = repository(client).create(project(), idempotency_key="create")
    root = client.projects.documents[created.project.id]
    assert root.data is not None
    root.data["approval_ids"] = ["broken_approval"]
    root.collection("approvals").document("broken_approval").data = {
        "id": "broken_approval",
        "project_id": created.project.id,
    }

    with pytest.raises(DomainError) as invalid_approval:
        repository(client).get(created.project.id)
    assert invalid_approval.value.context["entity"] == "approval"

    root.data["approval_ids"] = []
    root.data["event_sequence"] = 1
    root.collection("events").document("broken_event").data = {
        "id": "broken_event",
        "project_id": created.project.id,
        "sequence": 1,
    }
    with pytest.raises(DomainError) as invalid_event:
        repository(client).list_for_project(created.project.id)
    assert invalid_event.value.context["entity"] == "event"


def test_artifact_metadata_is_created_immutably_and_restored() -> None:
    client = FakeClient()
    created = repository(client).create(project(), idempotency_key="create")
    with_revision = repository(client).add_revision(
        created.project.id,
        revision(),
        expected_version=1,
        idempotency_key="revision-one",
    )

    saved = repository(client).add_artifact(
        artifact(),
        expected_version=with_revision.version,
        idempotency_key="artifact-one",
    )
    restored = repository(client).get(created.project.id)
    root = client.projects.documents[created.project.id]
    child = root.collections["artifacts"].documents["manifest_artifact"]

    assert saved.version == restored.version == 3
    assert restored.artifacts == (artifact(),)
    assert child.create_calls == 1 and child.update_calls == 0
    assert child.data is not None
    assert child.data["relative_path"].endswith("taskmaster.manifest.json")
    assert child.data["sha256"] == "a" * 64
    assert "content" not in child.data and "bytes" not in child.data


def test_artifact_replay_is_idempotent_and_duplicate_id_conflicts() -> None:
    client = FakeClient()
    created = repository(client).create(project(), idempotency_key="create")
    with_revision = repository(client).add_revision(
        created.project.id,
        revision(),
        expected_version=1,
        idempotency_key="revision-one",
    )
    first = repository(client).add_artifact(
        artifact(),
        expected_version=with_revision.version,
        idempotency_key="artifact-one",
    )
    replay = repository(client).add_artifact(
        artifact(),
        expected_version=with_revision.version,
        idempotency_key="artifact-one",
    )

    assert first == replay
    root = client.projects.documents[created.project.id]
    assert root.collections["artifacts"].documents["manifest_artifact"].create_calls == 1
    with pytest.raises(RepositoryConflictError):
        repository(client).add_artifact(
            artifact(),
            expected_version=first.version,
            idempotency_key="different-operation",
        )


def test_artifact_rejects_missing_revision_stale_version_and_unsafe_path() -> None:
    client = FakeClient()
    created = repository(client).create(project(), idempotency_key="create")

    with pytest.raises(RepositoryConflictError) as stale:
        repository(client).add_artifact(
            artifact(),
            expected_version=2,
            idempotency_key="stale-artifact",
        )
    with pytest.raises(EntityNotFoundError):
        repository(client).add_artifact(
            artifact(),
            expected_version=created.version,
            idempotency_key="missing-revision",
        )

    with_revision = repository(client).add_revision(
        created.project.id,
        revision(),
        expected_version=1,
        idempotency_key="revision-one",
    )
    with pytest.raises(DomainError) as unsafe:
        repository(client).add_artifact(
            artifact(relative_path="../outside/secret.json"),
            expected_version=with_revision.version,
            idempotency_key="unsafe-artifact",
        )

    assert stale.value.context == {"expected_version": 2, "actual_version": 1}
    assert unsafe.value.code == "ARTIFACT_PATH_INVALID"
    root = client.projects.documents[created.project.id]
    assert "artifacts" not in root.collections


def test_critical_transaction_replays_callback_without_duplicate_writes() -> None:
    client = FakeClient()
    created = repository(client).create(project(), idempotency_key="create")
    executor = RetryOnceTransactionExecutor()

    result = repository(client, transaction_executor=executor).add_revision(
        created.project.id,
        revision(),
        expected_version=created.version,
        idempotency_key="retry-safe-revision",
    )

    root = client.projects.documents[created.project.id]
    child = root.collections["revisions"].documents["r000001"]
    assert result.version == 2
    assert executor.callback_calls == 2
    assert client.transaction_attempts == [3, 3]
    assert child.create_calls == 1
    assert root.update_calls == 1


def test_exhausted_transaction_retries_are_sanitized() -> None:
    client = FakeClient()
    created = repository(client).create(project(), idempotency_key="create")

    with pytest.raises(DomainError) as captured:
        repository(
            client,
            transaction_executor=ExhaustedTransactionExecutor(),
        ).add_revision(
            created.project.id,
            revision(),
            expected_version=created.version,
            idempotency_key="exhausted-revision",
        )

    assert captured.value.code == "FIRESTORE_TRANSACTION_RETRY_EXHAUSTED"
    assert "private" not in str(captured.value).casefold()
    assert client.transaction_attempts == [2]


def test_artifact_grouped_write_is_atomic_on_root_conflict() -> None:
    client = FakeClient()
    created = repository(client).create(project(), idempotency_key="create")
    with_revision = repository(client).add_revision(
        created.project.id,
        revision(),
        expected_version=1,
        idempotency_key="revision-one",
    )
    root = client.projects.documents[created.project.id]
    root.force_conflict = True

    with pytest.raises(RepositoryConflictError):
        repository(client).add_artifact(
            artifact(),
            expected_version=with_revision.version,
            idempotency_key="concurrent-artifact",
        )

    child = root.collections["artifacts"].documents["manifest_artifact"]
    assert child.data is None
    assert root.data is not None and root.data["artifact_ids"] == []


def test_malformed_artifact_document_is_rejected() -> None:
    client = FakeClient()
    created = repository(client).create(project(), idempotency_key="create")
    repository(client).add_revision(
        created.project.id,
        revision(),
        expected_version=1,
        idempotency_key="revision-one",
    )
    root = client.projects.documents[created.project.id]
    assert root.data is not None
    root.data["artifact_ids"] = ["broken_artifact"]
    root.collection("artifacts").document("broken_artifact").data = {
        "metadata": {
            **artifact(artifact_id="broken_artifact").model_dump(mode="python"),
            "relative_path": "../../escape.json",
        }
    }

    with pytest.raises(DomainError) as captured:
        repository(client).get(created.project.id)

    assert captured.value.code == "FIRESTORE_DOCUMENT_INVALID"
    assert captured.value.context["entity"] == "artifact"


def test_fixed_retention_deadline_reaches_root_and_every_child_document() -> None:
    client = FakeClient()
    policy = DemoRetentionPolicy(retention_days=3)
    store = repository(client, retention_policy=policy)
    created = store.create(
        project(state=ProjectState.DESIGN_IN_REVIEW), idempotency_key="create"
    )
    with_revision = store.add_revision(
        created.project.id,
        revision(approved=False),
        expected_version=created.version,
        idempotency_key="revision-one",
    )
    approved = store.record_approval(
        approval(),
        expected_version=with_revision.version,
        idempotency_key="approval-one",
    )
    store.add_artifact(
        artifact(),
        expected_version=approved.version,
        idempotency_key="artifact-one",
    )
    store.append(event("retention_event"), idempotency_key="event-one")

    root = client.projects.documents[created.project.id]
    expected = NOW + timedelta(days=3)
    documents = [
        root,
        root.collections["briefings"].documents["v000001"],
        root.collections["revisions"].documents["r000001"],
        root.collections["approvals"].documents["approval_revision_one"],
        root.collections["events"].documents["retention_event"],
        root.collections["artifacts"].documents["manifest_artifact"],
    ]

    assert all(document.data is not None for document in documents)
    assert {document.data["expires_at"] for document in documents if document.data} == {
        expected
    }


def test_child_with_different_retention_deadline_is_rejected() -> None:
    client = FakeClient()
    created = repository(client).create(project(), idempotency_key="create")
    root = client.projects.documents[created.project.id]
    briefing = root.collections["briefings"].documents["v000001"]
    assert briefing.data is not None
    briefing.data["expires_at"] = LATER + timedelta(days=30)

    with pytest.raises(DomainError) as captured:
        repository(client).get(created.project.id)

    assert captured.value.code == "FIRESTORE_DOCUMENT_INVALID"
    assert captured.value.context["entity"] == "briefing"


def test_demo_reset_recursively_replaces_only_the_owned_firestore_aggregate() -> None:
    client = FakeClient()
    store = repository(client)
    initial = store.create(
        project(state=ProjectState.DESIGN_IN_REVIEW), idempotency_key="create-demo"
    )
    revised = store.add_revision(
        initial.project.id,
        revision(approved=False),
        expected_version=initial.version,
        idempotency_key="add-demo-revision",
    )
    store.append(event("reset_me_event"), idempotency_key="add-demo-event")
    other = project(name="Unrelated project").model_copy(
        update={"id": "unrelated_project"}, deep=True
    )
    store.create(other, idempotency_key="create-unrelated")

    clean = revised.project.model_copy(
        update={
            "state": ProjectState.IDEA,
            "active_revision": None,
            "briefing": Briefing(),
        },
        deep=True,
    )
    result = store.reset_demo(
        clean,
        owner_session_id="session-owner",
        idempotency_key="reset-demo-aggregate",
    )

    assert result.version == 1
    assert result.revisions == ()
    assert result.project.state is ProjectState.IDEA
    assert store.list_for_project(initial.project.id) == ()
    assert store.get("unrelated_project").project.name == "Unrelated project"
    root = client.projects.documents[initial.project.id]
    assert set(root.collections) == {"briefings", "events"}
    assert root.collections["events"].documents == {}


def test_demo_reset_rejects_a_different_firestore_session() -> None:
    client = FakeClient()
    store = repository(client)
    stored = store.create(project(), idempotency_key="create-owned-demo")

    with pytest.raises(ProjectAccessDeniedError):
        store.reset_demo(
            stored.project.model_copy(update={"owner_session_id": "intruder"}),
            owner_session_id="intruder",
            idempotency_key="forbidden-reset",
        )

    assert store.get(stored.project.id).project.owner_session_id == "session-owner"
