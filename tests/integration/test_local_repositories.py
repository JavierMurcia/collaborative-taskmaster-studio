from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from infrastructure.local.clock import FrozenClock
from infrastructure.local.repositories import InMemoryRepository, JsonLocalRepository
from studio.domain.enums import ApprovalStatus, AuditEventType, ProjectState
from studio.domain.errors import (
    EntityNotFoundError,
    IdempotencyConflictError,
    RepositoryConflictError,
)
from studio.domain.models import (
    Approval,
    ApprovalRecord,
    ArtifactMetadata,
    AuditEvent,
    Project,
    Revision,
    TaskmasterSpecification,
)

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "academic_delivery_specification.json"
NOW = datetime(2026, 8, 13, 12, 0, tzinfo=UTC)


def specification(*, approved: bool = True) -> TaskmasterSpecification:
    payload: dict[str, Any] = json.loads(FIXTURE.read_text(encoding="utf-8"))
    if not approved:
        payload["approval"] = {
            "status": "draft",
            "decided_by": None,
            "decided_at": None,
            "note": "",
        }
    return TaskmasterSpecification.model_validate(payload)


def project(*, state: ProjectState = ProjectState.IDEA) -> Project:
    return Project(
        id="academic_delivery_coordinator",
        name="Academic delivery coordinator",
        state=state,
    )


def revision(*, approved: bool = True) -> Revision:
    return Revision(
        project_id="academic_delivery_coordinator",
        number=2,
        specification=specification(approved=approved),
    )


def event(event_id: str) -> AuditEvent:
    return AuditEvent(
        id=event_id,
        project_id="academic_delivery_coordinator",
        event_type=AuditEventType.REVISION_CREATED,
        actor_id="studio",
        summary="Revision created without private reasoning.",
        revision=2,
    )


def test_in_memory_create_is_idempotent_and_returns_defensive_snapshots() -> None:
    repository = InMemoryRepository(FrozenClock(NOW))
    first = repository.create(project(), idempotency_key="create-001")
    replay = repository.create(project(), idempotency_key="create-001")
    assert first.version == replay.version == 1
    assert first.project.created_at == NOW

    first.project.name = "Changed outside repository"
    assert repository.get("academic_delivery_coordinator").project.name != first.project.name


def test_idempotency_key_cannot_be_reused_for_different_content() -> None:
    repository = InMemoryRepository(FrozenClock(NOW))
    repository.create(project(), idempotency_key="same-key")
    changed = project().model_copy(update={"name": "Different name"})
    with pytest.raises(IdempotencyConflictError):
        repository.create(changed, idempotency_key="same-key")


def test_revision_replay_does_not_create_duplicates() -> None:
    repository = InMemoryRepository(FrozenClock(NOW))
    created = repository.create(project(), idempotency_key="create")
    first = repository.add_revision(
        created.project.id,
        revision(),
        expected_version=created.version,
        idempotency_key="revision-002",
    )
    replay = repository.add_revision(
        created.project.id,
        revision(),
        expected_version=created.version,
        idempotency_key="revision-002",
    )
    assert first.version == replay.version == 2
    assert len(replay.revisions) == 1
    assert replay.project.active_revision == 2


def test_optimistic_version_rejects_stale_write() -> None:
    repository = InMemoryRepository(FrozenClock(NOW))
    original = repository.create(project(), idempotency_key="create")
    updated = original.project.model_copy(update={"name": "First update"})
    repository.save(updated, expected_version=1, idempotency_key="save-first")
    stale = original.project.model_copy(update={"name": "Stale update"})
    with pytest.raises(RepositoryConflictError) as captured:
        repository.save(stale, expected_version=1, idempotency_key="save-stale")
    assert captured.value.context == {"expected_version": 1, "actual_version": 2}


def test_events_are_sequential_filterable_and_idempotent() -> None:
    repository = InMemoryRepository(FrozenClock(NOW))
    repository.create(project(), idempotency_key="create")
    first = repository.append(event("event_one"), idempotency_key="event-op-one")
    replay = repository.append(event("event_one"), idempotency_key="event-op-one")
    second = repository.append(event("event_two"), idempotency_key="event-op-two")
    assert first.sequence == replay.sequence == 1
    assert second.sequence == 2
    assert [item.sequence for item in repository.list_for_project(project().id)] == [1, 2]
    assert [item.id for item in repository.list_for_project(project().id, after_sequence=1)] == [
        "event_two"
    ]


def test_approval_transaction_updates_revision_and_project_state() -> None:
    repository = InMemoryRepository(FrozenClock(NOW))
    created = repository.create(
        project(state=ProjectState.DESIGN_IN_REVIEW), idempotency_key="create"
    )
    with_revision = repository.add_revision(
        created.project.id,
        revision(approved=False),
        expected_version=created.version,
        idempotency_key="revision",
    )
    approval = Approval(
        status=ApprovalStatus.APPROVED,
        decided_by="student",
        decided_at=NOW,
        note="Approved after review.",
    )
    approved = repository.record_approval(
        ApprovalRecord(
            id="approval_revision_two",
            project_id=created.project.id,
            revision=2,
            approval=approval,
        ),
        expected_version=with_revision.version,
        idempotency_key="approval",
    )
    assert approved.version == 3
    assert approved.project.state is ProjectState.DESIGN_APPROVED
    assert approved.revisions[0].specification.approval.status is ApprovalStatus.APPROVED
    assert len(approved.approvals) == 1


def test_artifact_metadata_is_attached_to_existing_revision() -> None:
    repository = InMemoryRepository(FrozenClock(NOW))
    created = repository.create(project(), idempotency_key="create")
    with_revision = repository.add_revision(
        created.project.id,
        revision(),
        expected_version=created.version,
        idempotency_key="revision",
    )
    result = repository.add_artifact(
        ArtifactMetadata(
            id="manifest_artifact",
            project_id=created.project.id,
            revision=2,
            relative_path="manifest.json",
            sha256="0" * 64,
            framework="google_adk",
            template_version="1.0.0",
            validation_status="valid",
        ),
        expected_version=with_revision.version,
        idempotency_key="artifact",
    )
    assert result.version == 3
    assert result.artifacts[0].relative_path == "manifest.json"


def test_json_repository_survives_restart(tmp_path: Path) -> None:
    data = tmp_path / "studio-data"
    first_repository = JsonLocalRepository(data, FrozenClock(NOW))
    created = first_repository.create(project(), idempotency_key="create")
    stored = first_repository.add_revision(
        created.project.id,
        revision(),
        expected_version=created.version,
        idempotency_key="revision",
    )
    first_repository.append(event("event_one"), idempotency_key="event-one")

    restarted = JsonLocalRepository(data, FrozenClock(NOW))
    restored = restarted.get(created.project.id)
    assert restored.version == stored.version
    assert restored.project.active_revision == 2
    assert restored.revisions[0].specification.metadata.id == "academic_delivery_coordinator"
    assert restarted.list_for_project(created.project.id)[0].sequence == 1
    replay = restarted.add_revision(
        created.project.id,
        revision(),
        expected_version=created.version,
        idempotency_key="revision",
    )
    assert replay.version == stored.version
    assert len(replay.revisions) == 1


def test_json_repository_rejects_path_traversal(tmp_path: Path) -> None:
    repository = JsonLocalRepository(tmp_path / "studio-data")
    with pytest.raises(EntityNotFoundError) as captured:
        repository.get("../outside")
    assert captured.value.code == "ENTITY_NOT_FOUND"


def test_two_json_repository_instances_protect_concurrent_writes(tmp_path: Path) -> None:
    data = tmp_path / "studio-data"
    first = JsonLocalRepository(data, FrozenClock(NOW))
    first.create(project(), idempotency_key="create")
    second = JsonLocalRepository(data, FrozenClock(NOW))
    first_snapshot = first.get(project().id)
    second_snapshot = second.get(project().id)

    def save_name(repository: JsonLocalRepository, snapshot: Any, name: str, key: str) -> str:
        changed = snapshot.project.model_copy(update={"name": name})
        try:
            repository.save(changed, expected_version=snapshot.version, idempotency_key=key)
        except RepositoryConflictError:
            return "conflict"
        return "saved"

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(
            executor.map(
                lambda arguments: save_name(*arguments),
                [
                    (first, first_snapshot, "First writer", "save-first"),
                    (second, second_snapshot, "Second writer", "save-second"),
                ],
            )
        )
    assert sorted(results) == ["conflict", "saved"]
    assert JsonLocalRepository(data).get(project().id).version == 2
