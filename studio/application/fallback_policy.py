"""Central, auditable policy for deterministic local model fallbacks."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

from studio.domain.errors import DomainError
from studio.ports.model_gateway import model_error_details

FallbackOperation = Literal[
    "interview_question",
    "briefing_extraction",
    "taskmaster_specification",
    "taskmaster_revision",
]
FallbackStrategy = Literal[
    "local_catalog",
    "local_parser",
    "deterministic_designer",
    "deterministic_reviewer",
]
FallbackCategory = Literal[
    "provider_unavailable",
    "invalid_output",
    "safety_rejection",
    "budget_limit",
    "cache_recovery",
    "application_rejection",
]

_PROVIDER_ERRORS = frozenset(
    {
        "MODEL_TIMEOUT",
        "MODEL_UNAVAILABLE",
        "MODEL_GATEWAY_UNAVAILABLE",
        "MODEL_SDK_UNAVAILABLE",
    }
)
_BUDGET_ERRORS = frozenset(
    {
        "MODEL_TOKEN_LIMIT_EXCEEDED",
        "MODEL_QUESTION_LIMIT_REACHED",
    }
)


class FallbackDecision(BaseModel):
    """Safe decision record; never contains prompt, response, feedback, or credentials."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    mode: Literal["local"] = "local"
    operation: FallbackOperation
    strategy: FallbackStrategy
    reason_code: str
    category: FallbackCategory
    model_attempted: bool
    retryable: bool
    state_preserved: Literal[True] = True


def decide_local_fallback(
    operation: FallbackOperation,
    strategy: FallbackStrategy,
    reason_code: str,
    *,
    model_attempted: bool,
) -> FallbackDecision:
    """Classify one fallback without inspecting untrusted error messages or payloads."""
    category = _category(reason_code)
    return FallbackDecision(
        operation=operation,
        strategy=strategy,
        reason_code=reason_code,
        category=category,
        model_attempted=model_attempted,
        retryable=category == "provider_unavailable",
    )


def fallback_event_details(
    decision: FallbackDecision,
    *,
    error: DomainError | None = None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build an allow-listed event payload while retaining existing telemetry fields."""
    error_details = (
        model_error_details(error)
        if error is not None
        else {"error_code": decision.reason_code}
    )
    return {
        **(extra or {}),
        "operation": decision.operation,
        **error_details,
        "fallback": decision.model_dump(mode="json"),
    }


def _category(reason_code: str) -> FallbackCategory:
    if reason_code in _PROVIDER_ERRORS:
        return "provider_unavailable"
    if reason_code in _BUDGET_ERRORS:
        return "budget_limit"
    if reason_code == "INTERVIEW_QUESTION_CACHE_INVALID":
        return "cache_recovery"
    if reason_code == "SILENT_POLICY_REDUCTION":
        return "safety_rejection"
    if any(
        marker in reason_code
        for marker in ("INVALID", "EMPTY", "SCOPE_CHANGED", "OUTPUT")
    ):
        return "invalid_output"
    return "application_rejection"
