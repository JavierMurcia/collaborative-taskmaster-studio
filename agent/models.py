"""Mission-level contracts for Sentinel Taskmaster."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any


class MissionStatus(str, Enum):
    READY = "ready"
    WAITING_FOR_APPROVAL = "waiting_for_approval"
    RUNNING = "running"
    RECOVERED = "recovered"
    FAILED = "failed"


@dataclass(frozen=True)
class PlanStep:
    step_id: str
    action: str
    arguments: dict[str, Any]
    purpose: str


@dataclass
class MissionReport:
    incident_id: str
    status: MissionStatus
    plan: list[PlanStep]
    trajectory: list[dict[str, Any]] = field(default_factory=list)
    approval_id: str | None = None

    def as_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["status"] = self.status.value
        return data
