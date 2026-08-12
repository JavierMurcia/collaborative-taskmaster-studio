"""Domain state for the deterministic Sentinel incident simulator."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any


class IncidentStatus(str, Enum):
    DEGRADED = "degraded"
    RECOVERED = "recovered"
    FAILED = "failed"


class RiskLevel(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


@dataclass
class Worker:
    worker_id: str
    status: str = "healthy"
    restart_count: int = 0


@dataclass
class Evidence:
    evidence_id: str
    source: str
    message: str
    trust: str
    timestamp: str
    tags: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ServiceState:
    """The observable state an agent must bring back within target limits."""

    incident_id: str
    status: IncidentStatus
    queue_depth: int
    latency_ms: int
    error_rate_percent: float
    corrupt_batch_pending: bool
    global_capacity: int
    workers: dict[str, Worker]
    budget_remaining: int
    alerts: list[str] = field(default_factory=list)
    action_history: list[dict[str, Any]] = field(default_factory=list)

    queue_target: int = 100
    latency_target_ms: int = 500
    error_rate_target_percent: float = 1.0

    def metrics(self) -> dict[str, Any]:
        return {
            "incident_id": self.incident_id,
            "status": self.status.value,
            "queue_depth": self.queue_depth,
            "latency_ms": self.latency_ms,
            "error_rate_percent": self.error_rate_percent,
            "corrupt_batch_pending": self.corrupt_batch_pending,
            "global_capacity": self.global_capacity,
            "budget_remaining": self.budget_remaining,
            "alerts": list(self.alerts),
            "workers": {worker_id: asdict(worker) for worker_id, worker in self.workers.items()},
        }

    def meets_recovery_targets(self) -> bool:
        return (
            self.queue_depth <= self.queue_target
            and self.latency_ms <= self.latency_target_ms
            and self.error_rate_percent <= self.error_rate_target_percent
            and not self.alerts
            and not self.corrupt_batch_pending
        )

    def refresh_status(self) -> IncidentStatus:
        self.status = IncidentStatus.RECOVERED if self.meets_recovery_targets() else IncidentStatus.DEGRADED
        return self.status

