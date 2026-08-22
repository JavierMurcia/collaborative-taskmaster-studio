from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from fastapi.testclient import TestClient

from app.main import create_app
from infrastructure.local.clock import FrozenClock
from infrastructure.local.repositories import InMemoryRepository
from tests.api.test_h5_api import create_project, headers, reach_revision_two


def test_api_generates_and_lists_approved_adk_artifacts(tmp_path: Path) -> None:
    clock = FrozenClock(datetime(2026, 8, 13, 16, 0, tzinfo=UTC))
    repository = InMemoryRepository(clock)
    api = TestClient(create_app(repository, repository, clock, tmp_path / "generated"))
    project_id = create_project(api)
    reach_revision_two(api, project_id)
    approved = api.post(
        f"/api/v1/projects/{project_id}/revisions/2/approval",
        headers=headers("approve-before-generation"),
        json={"decision": "approved", "note": "Aprobado para generar archivos."},
    )
    assert approved.status_code == 200

    generated = api.post(
        f"/api/v1/projects/{project_id}/generation",
        headers=headers("generate-approved-project"),
        json={"revision": 2},
    )

    assert generated.status_code == 201
    payload = generated.json()
    assert payload["manifest"]["framework"] == "google_adk"
    assert payload["manifest"]["template_version"] == "1.0.0"
    assert payload["artifact"]["validation_status"] == "valid"
    assert payload["output_relative_path"].endswith("revision-2")
    listed = api.get(f"/api/v1/projects/{project_id}/artifacts", headers=headers())
    assert listed.status_code == 200
    assert listed.json() == [payload["artifact"]]


def test_api_rejects_generation_before_human_approval(tmp_path: Path) -> None:
    clock = FrozenClock(datetime(2026, 8, 13, 16, 0, tzinfo=UTC))
    repository = InMemoryRepository(clock)
    api = TestClient(create_app(repository, repository, clock, tmp_path / "generated"))
    project_id = create_project(api)
    reach_revision_two(api, project_id)

    rejected = api.post(
        f"/api/v1/projects/{project_id}/generation",
        headers=headers("generate-without-approval"),
        json={"revision": 2},
    )

    assert rejected.status_code == 400
    assert rejected.json()["error"]["code"] == "GENERATION_REQUIRES_APPROVAL"
    assert not (tmp_path / "generated").exists()
