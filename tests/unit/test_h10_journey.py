"""H10-10 deployed Collaborative Partner journey."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any
from urllib.request import Request

import pytest

from infrastructure.cloud_run.journey import run_demo_journey

PROJECT_ID = "project_h10_journey"


class ScriptedRequester:
    def __init__(self, script: Sequence[tuple[str, str, int, Any]]) -> None:
        self.script = list(script)
        self.calls: list[tuple[str, str]] = []

    def __call__(self, request: Request, timeout: float) -> tuple[int, Any]:
        assert timeout == 12.0
        expected_method, expected_path, status, payload = self.script.pop(0)
        path = request.full_url.removeprefix("https://studio.example")
        assert (request.method, path) == (expected_method, expected_path)
        self.calls.append((request.method, path))
        return status, payload


def _script(*, approved_state: str = "diseno_aprobado") -> list[tuple[str, str, int, Any]]:
    prefix = f"/api/v1/projects/{PROJECT_ID}"
    return [
        (
            "POST",
            "/api/v1/projects",
            201,
            {"snapshot": {"project": {"id": PROJECT_ID}}},
        ),
        (
            "POST",
            f"{prefix}/interview/start",
            200,
            {"next_question": {"question_id": "ask_deadline_and_hours"}},
        ),
        (
            "POST",
            f"{prefix}/interview/messages",
            200,
            {"next_question": {"question_id": "ask_input_and_result"}},
        ),
        (
            "POST",
            f"{prefix}/interview/messages",
            200,
            {"next_question": {"question_id": "ask_autonomy_and_approval"}},
        ),
        (
            "POST",
            f"{prefix}/interview/messages",
            200,
            {"next_question": None, "notes": {"can_confirm": True}},
        ),
        ("POST", f"{prefix}/briefing/confirm", 200, {}),
        ("POST", f"{prefix}/revisions", 201, {"revision": {"number": 1}}),
        (
            "POST",
            f"{prefix}/revisions/1/feedback",
            201,
            {"revision": {"number": 2}},
        ),
        (
            "GET",
            f"{prefix}/revisions/2/diff?from_revision=1",
            200,
            {"from_revision": 1, "to_revision": 2},
        ),
        (
            "POST",
            f"{prefix}/revisions/2/approval",
            200,
            {"snapshot": {"project": {"state": approved_state}}},
        ),
        (
            "POST",
            f"{prefix}/generation",
            201,
            {
                "artifact": {
                    "id": "artifact_h10",
                    "validation_status": "valid",
                    "framework": "google_adk",
                },
                "manifest": {"template_version": "1.0.0"},
            },
        ),
        (
            "POST",
            f"{prefix}/evaluations",
            201,
            {
                "report": {
                    "decision": "ready",
                    "scenarios": [
                        {"passed": True},
                        {"passed": True},
                        {"passed": True},
                    ],
                }
            },
        ),
        (
            "GET",
            f"{prefix}/events",
            200,
            [
                {"event_type": "briefing_confirmed"},
                {"event_type": "model_generation_completed"},
                {"event_type": "model_fallback_used"},
                {"event_type": "revision_approved"},
                {"event_type": "artifact_generated"},
                {"event_type": "evaluation_completed"},
                *({"event_type": "state_transitioned"} for _ in range(12)),
            ],
        ),
    ]


def test_journey_proves_collaboration_approval_generation_and_evaluation() -> None:
    fake = ScriptedRequester(_script())
    result = run_demo_journey(
        "https://studio.example",
        timeout_seconds=12.0,
        requester=fake,
    )

    assert result.status == "passed"
    assert result.project_id == PROJECT_ID
    assert result.approved_revision == 2
    assert result.artifact_id == "artifact_h10"
    assert result.evaluation_decision == "ready"
    assert result.model_completed_events == 1
    assert result.model_fallback_events == 1
    assert result.audit_event_count == 18
    assert len(result.steps) == 13
    assert fake.script == []


def test_journey_fails_closed_if_human_approval_is_not_recorded() -> None:
    fake = ScriptedRequester(_script(approved_state="diseno_en_revision"))

    with pytest.raises(RuntimeError, match="decisión humana"):
        run_demo_journey(
            "https://studio.example",
            timeout_seconds=12.0,
            requester=fake,
        )

    assert all(not path.endswith("/generation") for _, path in fake.calls)


@pytest.mark.parametrize("url", ["", "http://studio.example", "https://studio.example?q=x"])
def test_journey_requires_a_clean_https_url(url: str) -> None:
    with pytest.raises(ValueError, match="HTTPS"):
        run_demo_journey(url, requester=ScriptedRequester([]))
