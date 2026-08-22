"""H11-05 safe reset of the official demonstration."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from infrastructure.local.clock import FrozenClock
from infrastructure.local.repositories import InMemoryRepository, JsonLocalRepository
from studio.application.demo_fixture import load_final_demo_specification
from studio.application.demo_reset import CONFIRMATION_PHRASE, DemoResetService
from studio.domain.enums import AuditEventType, ProjectState
from studio.domain.errors import DomainError, ProjectAccessDeniedError
from studio.domain.models import AuditEvent, Project, Revision, TaskmasterSpecification

NOW = datetime(2026, 8, 20, 15, 0, tzinfo=UTC)
OWNER = "demo_owner"
PROJECT_ID = "official_demo_instance"


def _project(project_id: str, owner: str = OWNER) -> Project:
    return Project(
        id=project_id,
        name="Proyecto antes del reinicio",
        owner_session_id=owner,
        created_at=NOW,
        updated_at=NOW,
    )


def _populate(repository: InMemoryRepository, project_id: str = PROJECT_ID) -> None:
    repository.create(_project(project_id), idempotency_key=f"create-{project_id}")
    specification = TaskmasterSpecification.model_validate(load_final_demo_specification())
    repository.add_revision(
        project_id,
        Revision(project_id=project_id, number=1, specification=specification),
        expected_version=1,
        idempotency_key=f"revision-{project_id}",
    )
    repository.append(
        AuditEvent(
            id=f"event_{project_id}",
            project_id=project_id,
            event_type=AuditEventType.REVISION_CREATED,
            actor_id=OWNER,
            summary="Dato de trayectoria que debe desaparecer.",
        ),
        idempotency_key=f"event-{project_id}",
    )


def test_reset_restores_only_the_owned_project_and_removes_generated_files(tmp_path: Path) -> None:
    repository = InMemoryRepository(FrozenClock(NOW))
    _populate(repository)
    _populate(repository, "unrelated_project")
    generated = tmp_path / "generated" / PROJECT_ID / "revision-1"
    generated.mkdir(parents=True)
    (generated / "artifact.txt").write_text("demo", encoding="utf-8")

    result = DemoResetService(repository, FrozenClock(NOW), tmp_path / "generated").reset(
        PROJECT_ID,
        owner_session_id=OWNER,
        confirmation=CONFIRMATION_PHRASE,
        idempotency_key="reset-official-demo",
    )

    assert result.fixture_id == "academic_delivery_official_demo"
    assert result.generated_files_removed is True
    assert result.events == ()
    assert result.snapshot.version == 1
    assert result.snapshot.project.state is ProjectState.IDEA
    assert result.snapshot.project.active_revision is None
    assert result.snapshot.project.owner_session_id == OWNER
    assert result.snapshot.revisions == ()
    assert result.snapshot.approvals == ()
    assert result.snapshot.artifacts == ()
    assert repository.list_for_project(PROJECT_ID) == ()
    assert not (tmp_path / "generated" / PROJECT_ID).exists()
    assert repository.get("unrelated_project").revisions
    assert repository.list_for_project("unrelated_project")


def test_reset_is_idempotent_for_the_same_operation_key(tmp_path: Path) -> None:
    repository = InMemoryRepository(FrozenClock(NOW))
    _populate(repository)
    service = DemoResetService(repository, FrozenClock(NOW), tmp_path / "generated")

    first = service.reset(
        PROJECT_ID,
        owner_session_id=OWNER,
        confirmation=CONFIRMATION_PHRASE,
        idempotency_key="same-reset-operation",
    )
    regenerated = tmp_path / "generated" / PROJECT_ID
    regenerated.mkdir(parents=True)
    (regenerated / "new-run.txt").write_text("new run", encoding="utf-8")
    current = repository.get(PROJECT_ID)
    repository.save(
        current.project.model_copy(update={"state": ProjectState.INTERVIEW}),
        expected_version=current.version,
        idempotency_key="progress-after-reset",
    )
    replay = service.reset(
        PROJECT_ID,
        owner_session_id=OWNER,
        confirmation=CONFIRMATION_PHRASE,
        idempotency_key="same-reset-operation",
    )

    assert replay.snapshot.version == 2
    assert replay.reset_id == first.reset_id
    assert replay.generated_files_removed is False
    assert (regenerated / "new-run.txt").exists()


def test_reset_fails_closed_without_confirmation_or_ownership(tmp_path: Path) -> None:
    repository = InMemoryRepository(FrozenClock(NOW))
    _populate(repository)
    service = DemoResetService(repository, FrozenClock(NOW), tmp_path / "generated")

    with pytest.raises(DomainError, match="Confirme explícitamente"):
        service.reset(
            PROJECT_ID,
            owner_session_id=OWNER,
            confirmation="reiniciar",
            idempotency_key="invalid-confirmation",
        )
    with pytest.raises(ProjectAccessDeniedError):
        service.reset(
            PROJECT_ID,
            owner_session_id="another_owner",
            confirmation=CONFIRMATION_PHRASE,
            idempotency_key="invalid-owner",
        )
    assert repository.get(PROJECT_ID).revisions


def test_json_reset_survives_a_repository_restart(tmp_path: Path) -> None:
    data = tmp_path / "data"
    repository = JsonLocalRepository(data, FrozenClock(NOW))
    _populate(repository)
    DemoResetService(repository, FrozenClock(NOW), tmp_path / "generated").reset(
        PROJECT_ID,
        owner_session_id=OWNER,
        confirmation=CONFIRMATION_PHRASE,
        idempotency_key="durable-reset",
    )

    restored = JsonLocalRepository(data, FrozenClock(NOW))
    snapshot = restored.get(PROJECT_ID, owner_session_id=OWNER)
    assert snapshot.version == 1
    assert snapshot.revisions == ()
    assert restored.list_for_project(PROJECT_ID) == ()


def test_reset_endpoint_requires_phrase_and_returns_clean_snapshot(tmp_path: Path) -> None:
    repository = InMemoryRepository(FrozenClock(NOW))
    _populate(repository)
    client = TestClient(
        create_app(repository, repository, FrozenClock(NOW), tmp_path / "generated")
    )
    headers = {
        "X-Studio-Session": OWNER,
        "Idempotency-Key": "api-reset-operation",
    }

    rejected = client.post(
        f"/api/v1/projects/{PROJECT_ID}/demo/reset",
        headers=headers,
        json={"confirmation": "NO"},
    )
    response = client.post(
        f"/api/v1/projects/{PROJECT_ID}/demo/reset",
        headers=headers,
        json={"confirmation": CONFIRMATION_PHRASE},
    )

    assert rejected.status_code == 422
    assert response.status_code == 200
    payload = response.json()
    assert payload["events"] == []
    assert payload["snapshot"]["version"] == 1
    assert payload["snapshot"]["revisions"] == []


def test_chat_only_interface_does_not_expose_the_legacy_reset_view() -> None:
    root = Path(__file__).resolve().parents[2]
    html = (root / "app" / "static" / "index.html").read_text(encoding="utf-8")
    javascript = (root / "app" / "static" / "app.js").read_text(encoding="utf-8")

    assert 'id="open-reset"' not in html
    assert 'id="reset-dialog"' not in html
    assert 'pattern="REINICIAR_DEMO"' not in html
    assert "/demo/reset" not in javascript


def test_demo_reset_evidence_is_machine_readable() -> None:
    root = Path(__file__).resolve().parents[2]
    evidence = json.loads(
        (root / "docs" / "evidence" / "h11-05-demo-reset.json").read_text(
            encoding="utf-8"
        )
    )
    assert evidence["status"] == "complete"
    assert evidence["scope"] == "session_owned_active_project"
    assert evidence["confirmation_phrase"] == CONFIRMATION_PHRASE
    assert evidence["other_projects_affected"] is False
