"""H10-10 journey against the real application composition without cloud calls."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit
from urllib.request import Request

from fastapi.testclient import TestClient

from app.main import create_app
from infrastructure.cloud_run.journey import run_demo_journey
from infrastructure.local.clock import FrozenClock
from infrastructure.local.repositories import InMemoryRepository


class TestClientRequester:
    def __init__(self, client: TestClient) -> None:
        self.client = client

    def __call__(self, request: Request, timeout: float) -> tuple[int, Any]:
        assert timeout == 30.0
        parsed = urlsplit(request.full_url)
        response = self.client.request(
            request.method,
            parsed.path + (f"?{parsed.query}" if parsed.query else ""),
            headers=dict(request.header_items()),
            content=request.data,
        )
        return response.status_code, response.json()


def test_real_local_composition_completes_the_entire_demo_journey(tmp_path: Path) -> None:
    clock = FrozenClock(datetime(2026, 8, 20, 20, 0, tzinfo=UTC))
    repository = InMemoryRepository(clock)
    client = TestClient(
        create_app(
            repository,
            repository,
            clock,
            generated_root=tmp_path / "generated",
        )
    )

    result = run_demo_journey(
        "https://studio.example",
        timeout_seconds=30.0,
        requester=TestClientRequester(client),
    )

    assert result.status == "passed"
    assert result.approved_revision == 2
    assert result.evaluation_decision == "ready"
    assert result.model_completed_events == 0
    assert result.audit_event_count >= 18
    assert (tmp_path / "generated" / result.project_id / "revision-2").is_dir()
