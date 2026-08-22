from __future__ import annotations

import pytest

from studio.application.fallback_policy import (
    decide_local_fallback,
    fallback_event_details,
)
from studio.domain.errors import DomainError


@pytest.mark.parametrize(
    ("reason_code", "category", "retryable"),
    [
        ("MODEL_TIMEOUT", "provider_unavailable", True),
        ("MODEL_OUTPUT_INVALID", "invalid_output", False),
        ("SILENT_POLICY_REDUCTION", "safety_rejection", False),
        ("MODEL_QUESTION_LIMIT_REACHED", "budget_limit", False),
        ("INTERVIEW_QUESTION_CACHE_INVALID", "cache_recovery", False),
        ("WORKFLOW_GRAPH_BROKEN", "application_rejection", False),
    ],
)
def test_fallback_reasons_have_stable_categories(
    reason_code: str,
    category: str,
    retryable: bool,
) -> None:
    decision = decide_local_fallback(
        "interview_question",
        "local_catalog",
        reason_code,
        model_attempted=True,
    )

    assert decision.category == category
    assert decision.retryable is retryable
    assert decision.state_preserved is True


def test_fallback_event_keeps_only_allowlisted_model_telemetry() -> None:
    error = DomainError(
        "MODEL_UNAVAILABLE",
        "secret provider message",
        context={
            "provider": "vertex_ai",
            "model": "gemini-3.5-flash",
            "location": "global",
            "latency_ms": 250.0,
            "prompt": "secret user prompt",
            "credentials": "secret credentials",
        },
    )
    decision = decide_local_fallback(
        "taskmaster_specification",
        "deterministic_designer",
        error.code,
        model_attempted=True,
    )

    details = fallback_event_details(
        decision,
        error=error,
        extra={"source_revision": 1},
    )

    assert details["error_code"] == "MODEL_UNAVAILABLE"
    assert details["model"] == "gemini-3.5-flash"
    assert details["latency_ms"] == 250.0
    assert details["fallback"]["strategy"] == "deterministic_designer"
    serialized = str(details)
    assert "secret provider message" not in serialized
    assert "secret user prompt" not in serialized
    assert "secret credentials" not in serialized


def test_budget_fallback_records_that_no_model_call_was_attempted() -> None:
    decision = decide_local_fallback(
        "interview_question",
        "local_catalog",
        "MODEL_QUESTION_LIMIT_REACHED",
        model_attempted=False,
    )

    details = fallback_event_details(decision)

    assert details["error_code"] == "MODEL_QUESTION_LIMIT_REACHED"
    assert details["fallback"]["category"] == "budget_limit"
    assert details["fallback"]["model_attempted"] is False
