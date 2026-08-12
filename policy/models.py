"""Typed contracts for Sentinel's independent control plane."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any

from simulator.state import RiskLevel


class DecisionType(str, Enum):
    ALLOW = "allow"
    REQUIRE_APPROVAL = "require_approval"
    DENY = "deny"


class ApprovalStatus(str, Enum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


@dataclass(frozen=True)
class ActionRequest:
    action: str
    arguments: dict[str, Any] = field(default_factory=dict)
    rationale: str = ""


@dataclass(frozen=True)
class PolicyDecision:
    decision: DecisionType
    action: str
    risk: RiskLevel
    reason: str
    budget_cost: int
    approval_id: str | None = None

    def as_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["decision"] = self.decision.value
        data["risk"] = self.risk.value
        return data


@dataclass
class ApprovalRequest:
    approval_id: str
    action: str
    arguments: dict[str, Any]
    rationale: str
    risk: RiskLevel
    impact: str
    status: ApprovalStatus = ApprovalStatus.PENDING
    decided_by: str | None = None
    decision_note: str | None = None

    def as_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["risk"] = self.risk.value
        data["status"] = self.status.value
        return data
