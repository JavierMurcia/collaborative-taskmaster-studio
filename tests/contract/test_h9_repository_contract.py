"""Shared H9-10 persistence contract for local and Firestore repositories."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

import pytest

from infrastructure.local.clock import FrozenClock
from infrastructure.local.repositories import InMemoryRepository
from studio.domain.enums import ApprovalStatus, ProjectState
from studio.domain.errors import (
    EntityNotFoundError,
    IdempotencyConflictError,
    ProjectAccessDeniedError,
    RepositoryConflictError,
)
from studio.domain.models import ProjectSnapshot
from studio.ports.repositories import EventRepository, ProjectRepository
from tests.integration.test_h9_firestore_projects import (
    NOW,
    FakeClient,
    approval,
    artifact,
    event,
    project,
    revision,
)
from tests.integration.test_h9_firestore_projects import (
    repository as firestore_repository,
)


class ContractRepository(ProjectRepository, EventRepository, Protocol):
    """The complete persistence surface exercised by the shared contract."""


@dataclass(frozen=True, slots=True)
class RepositoryCase:
    name: str
    build: Callable[[], ContractRepository]


CASES = (
    RepositoryCase(
        name="local_memory",
        build=lambda: InMemoryRepository(FrozenClock(NOW)),
    ),
    RepositoryCase(
        name="firestore_double",
        build=lambda: firestore_repository(FakeClient()),
    ),
)


@pytest.fixture(params=CASES, ids=lambda case: case.name)
def repository_case(request: pytest.FixtureRequest) -> ContractRepository:
    case = request.param
    assert isinstance(case, RepositoryCase)
    return case.build()


def create_project(
    repository: ContractRepository,
    *,
    state: ProjectState = ProjectState.IDEA,
) -> ProjectSnapshot:
    return repository.create(
        project(state=state),
        idempotency_key="contract-create-project",
    )


def test_contract_create_round_trip_is_idempotent_and_defensive(
    repository_case: ContractRepository,
) -> None:
    created = create_project(repository_case)
    replay = repository_case.create(
        project(),
        idempotency_key="contract-create-project",
    )

    assert replay == created
    created.project.name = "External mutation"
    restored = repository_case.get(
        replay.project.id,
        owner_session_id="session-owner",
    )
    assert restored.project.name == "Academic delivery"
    assert restored.version == 1


def test_contract_enforces_owner_and_missing_project_boundaries(
    repository_case: ContractRepository,
) -> None:
    created = create_project(repository_case)

    with pytest.raises(ProjectAccessDeniedError):
        repository_case.get(
            created.project.id,
            owner_session_id="different-session",
        )
    with pytest.raises(EntityNotFoundError):
        repository_case.get("missing_contract_project")


def test_contract_rejects_idempotency_key_reuse_with_different_content(
    repository_case: ContractRepository,
) -> None:
    create_project(repository_case)

    with pytest.raises(IdempotencyConflictError):
        repository_case.create(
            project(name="Different contract payload"),
            idempotency_key="contract-create-project",
        )


def test_contract_rejects_stale_optimistic_write(
    repository_case: ContractRepository,
) -> None:
    created = create_project(repository_case)
    first_change = created.project.model_copy(update={"name": "First writer"})
    repository_case.save(
        first_change,
        expected_version=created.version,
        idempotency_key="contract-save-first",
    )

    stale_change = created.project.model_copy(update={"name": "Stale writer"})
    with pytest.raises(RepositoryConflictError):
        repository_case.save(
            stale_change,
            expected_version=created.version,
            idempotency_key="contract-save-stale",
        )


def test_contract_persists_revision_approval_and_artifact_as_one_aggregate(
    repository_case: ContractRepository,
) -> None:
    created = create_project(repository_case, state=ProjectState.DESIGN_IN_REVIEW)
    revised = repository_case.add_revision(
        created.project.id,
        revision(approved=False),
        expected_version=created.version,
        idempotency_key="contract-add-revision",
    )
    approved = repository_case.record_approval(
        approval(),
        expected_version=revised.version,
        idempotency_key="contract-record-approval",
    )
    completed = repository_case.add_artifact(
        artifact(),
        expected_version=approved.version,
        idempotency_key="contract-add-artifact",
    )

    assert completed.version == 4
    assert completed.project.active_revision == 1
    assert completed.project.state is ProjectState.DESIGN_APPROVED
    assert completed.revisions[0].specification.approval.status is ApprovalStatus.APPROVED
    assert tuple(item.id for item in completed.approvals) == ("approval_revision_one",)
    assert tuple(item.id for item in completed.artifacts) == ("manifest_artifact",)


def test_contract_orders_filters_and_replays_audit_events(
    repository_case: ContractRepository,
) -> None:
    created = create_project(repository_case)
    first = repository_case.append(
        event("contract_event_one"),
        idempotency_key="contract-event-operation-one",
    )
    replay = repository_case.append(
        event("contract_event_one"),
        idempotency_key="contract-event-operation-one",
    )
    second = repository_case.append(
        event("contract_event_two"),
        idempotency_key="contract-event-operation-two",
    )

    assert first == replay
    assert first.sequence == 1
    assert second.sequence == 2
    assert tuple(
        item.id
        for item in repository_case.list_for_project(
            created.project.id,
            after_sequence=1,
        )
    ) == ("contract_event_two",)
