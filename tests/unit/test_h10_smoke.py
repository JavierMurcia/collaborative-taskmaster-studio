"""H10-09 controlled deployment smoke journey."""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any
from urllib.request import Request

import pytest

from infrastructure.cloud_run.smoke import run_smoke


class FakeRequester:
    def __init__(self, mutate: Callable[[str, dict[str, Any]], None] | None = None) -> None:
        self.calls: list[tuple[str, str]] = []
        self.mutate = mutate

    def __call__(self, request: Request, timeout: float) -> tuple[int, dict[str, Any]]:
        path = request.full_url.removeprefix("https://studio.example")
        self.calls.append((request.method, path))
        payloads: dict[str, tuple[int, dict[str, Any]]] = {
            "/health/live": (200, {"status": "alive"}),
            "/health/startup": (200, {"status": "started"}),
            "/health/ready": (200, {"status": "ready"}),
            "/api/v1/meta": (
                200,
                {
                    "name": "Collaborative Taskmaster Studio",
                    "firestore_database": {
                        "status": "ready",
                        "repository_active": True,
                    },
                },
            ),
            "/api/v1/projects": (
                201,
                {"snapshot": {"project": {"id": "project_smoke"}}},
            ),
            "/api/v1/projects/project_smoke": (
                200,
                {"snapshot": {"project": {"id": "project_smoke"}}},
            ),
        }
        code, payload = payloads[path]
        payload = json.loads(json.dumps(payload))
        if self.mutate is not None:
            self.mutate(path, payload)
        assert timeout == 7.0
        return code, payload


def test_read_only_smoke_checks_health_and_active_firestore() -> None:
    fake = FakeRequester()
    result = run_smoke("https://studio.example", timeout_seconds=7.0, requester=fake)

    assert result.status == "passed"
    assert result.functional_write_executed is False
    assert result.project_id is None
    assert len(result.checks) == 4
    assert all(method == "GET" for method, _ in fake.calls)


def test_functional_smoke_creates_and_restores_one_isolated_project() -> None:
    fake = FakeRequester()
    result = run_smoke(
        "https://studio.example/",
        functional=True,
        timeout_seconds=7.0,
        requester=fake,
    )

    assert result.project_id == "project_smoke"
    assert result.functional_write_executed is True
    assert [(item.name, item.status_code) for item in result.checks[-2:]] == [
        ("project_create", 201),
        ("project_read", 200),
    ]


def test_smoke_fails_closed_when_readiness_is_not_ready() -> None:
    def mutate(path: str, payload: dict[str, Any]) -> None:
        if path == "/health/ready":
            payload["status"] = "not_ready"

    with pytest.raises(RuntimeError, match="readiness"):
        run_smoke(
            "https://studio.example",
            timeout_seconds=7.0,
            requester=FakeRequester(mutate),
        )


@pytest.mark.parametrize(
    "url",
    ["", "http://studio.example", "https://studio.example?token=secret"],
)
def test_smoke_rejects_non_production_urls(url: str) -> None:
    with pytest.raises(ValueError, match="HTTPS"):
        run_smoke(url, requester=FakeRequester())
