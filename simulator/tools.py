"""Safe, deterministic tools exposed by the Sentinel incident simulator."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from .scenario import initial_evidence
from .state import Evidence, RiskLevel, ServiceState


@dataclass(frozen=True)
class ToolResult:
    ok: bool
    action: str
    risk: RiskLevel
    message: str
    data: dict[str, Any]
    budget_cost: int = 0
    approval_required: bool = False

    def as_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["risk"] = self.risk.value
        return result


class OrdersIncidentTools:
    """Tool contract for the first Sentinel demo.

    The tools mutate only in-memory simulator state. A separate policy layer will
    decide whether a returned high-risk request is allowed to be executed.
    """

    def __init__(self, state: ServiceState, evidence: list[Evidence] | None = None):
        self.state = state
        self._evidence = evidence if evidence is not None else initial_evidence()

    def inspect_service(self) -> ToolResult:
        return self._record(
            action="inspect_service",
            risk=RiskLevel.LOW,
            message="Current orders-ingestion state retrieved.",
            data=self.state.metrics(),
        )

    def read_evidence(self) -> ToolResult:
        return self._record(
            action="read_evidence",
            risk=RiskLevel.LOW,
            message="Incident evidence retrieved; callers must validate trust before persisting it.",
            data={"evidence": [item.as_dict() for item in self._evidence]},
        )

    def restart_worker(self, worker_id: str) -> ToolResult:
        worker = self.state.workers.get(worker_id)
        if worker is None:
            return self._record(
                action="restart_worker",
                risk=RiskLevel.MEDIUM,
                message=f"Worker '{worker_id}' does not exist.",
                data={"worker_id": worker_id},
            )
        if self.state.budget_remaining < 1:
            return self._budget_exhausted("restart_worker", RiskLevel.MEDIUM)

        worker.status = "healthy"
        worker.restart_count += 1
        self.state.budget_remaining -= 1
        self.state.queue_depth = max(0, self.state.queue_depth - 340)
        self.state.latency_ms = max(0, self.state.latency_ms - 900)
        self.state.error_rate_percent = max(0, round(self.state.error_rate_percent - 3.4, 1))
        self._refresh_alerts()
        return self._record(
            action="restart_worker",
            risk=RiskLevel.MEDIUM,
            message=f"Worker '{worker_id}' restarted. The corrupt batch still blocks full recovery.",
            data={"worker": asdict(worker), "metrics": self.state.metrics()},
            budget_cost=1,
        )

    def clear_corrupt_batch(self, batch_id: str) -> ToolResult:
        if batch_id != "batch-2026-08-12-17":
            return self._record(
                action="clear_corrupt_batch",
                risk=RiskLevel.MEDIUM,
                message=f"Batch '{batch_id}' is not the isolated corrupt batch.",
                data={"batch_id": batch_id},
            )
        if not self.state.corrupt_batch_pending:
            return self._record(
                action="clear_corrupt_batch",
                risk=RiskLevel.MEDIUM,
                message="The isolated corrupt batch was already cleared.",
                data={"batch_id": batch_id, "metrics": self.state.metrics()},
            )
        if self.state.budget_remaining < 1:
            return self._budget_exhausted("clear_corrupt_batch", RiskLevel.MEDIUM)

        self.state.corrupt_batch_pending = False
        self.state.budget_remaining -= 1
        self.state.queue_depth = max(0, self.state.queue_depth - 830)
        self.state.latency_ms = max(0, self.state.latency_ms - 1_500)
        self.state.error_rate_percent = max(0, round(self.state.error_rate_percent - 4.2, 1))
        self._refresh_alerts()
        self.state.refresh_status()
        return self._record(
            action="clear_corrupt_batch",
            risk=RiskLevel.MEDIUM,
            message="Isolated corrupt batch cleared. Recovery targets are now met.",
            data={"batch_id": batch_id, "metrics": self.state.metrics()},
            budget_cost=1,
        )

    def scale_global_capacity(self, additional_workers: int, *, approved: bool = False) -> ToolResult:
        if additional_workers < 1 or additional_workers > 3:
            return self._record(
                action="scale_global_capacity",
                risk=RiskLevel.HIGH,
                message="Global capacity changes must add between 1 and 3 workers.",
                data={"additional_workers": additional_workers},
                approval_required=True,
            )
        if not approved:
            return self._record(
                action="scale_global_capacity",
                risk=RiskLevel.HIGH,
                message="Global capacity scaling is blocked pending explicit human approval.",
                data={"additional_workers": additional_workers, "metrics": self.state.metrics()},
                approval_required=True,
            )
        if self.state.budget_remaining < 2:
            return self._budget_exhausted("scale_global_capacity", RiskLevel.HIGH, approval_required=True)

        self.state.global_capacity += additional_workers
        self.state.budget_remaining -= 2
        self.state.queue_depth = max(0, self.state.queue_depth - additional_workers * 250)
        self.state.latency_ms = max(0, self.state.latency_ms - additional_workers * 400)
        self.state.error_rate_percent = max(0, round(self.state.error_rate_percent - additional_workers * 0.8, 1))
        self._refresh_alerts()
        self.state.refresh_status()
        return self._record(
            action="scale_global_capacity",
            risk=RiskLevel.HIGH,
            message="Global capacity increased after recorded human approval.",
            data={"additional_workers": additional_workers, "metrics": self.state.metrics()},
            budget_cost=2,
            approval_required=True,
        )

    def verify_recovery(self) -> ToolResult:
        status = self.state.refresh_status()
        return self._record(
            action="verify_recovery",
            risk=RiskLevel.LOW,
            message=(
                "Independent verification passed: recovery targets are met."
                if status.value == "recovered"
                else "Independent verification failed: recovery targets are not yet met."
            ),
            data={"recovered": status.value == "recovered", "metrics": self.state.metrics()},
        )

    def _refresh_alerts(self) -> None:
        alerts: list[str] = []
        if self.state.queue_depth > self.state.queue_target:
            alerts.append("queue_backlog_critical")
        if self.state.latency_ms > self.state.latency_target_ms:
            alerts.append("orders_latency_critical")
        if self.state.error_rate_percent > self.state.error_rate_target_percent:
            alerts.append("orders_error_rate_critical")
        self.state.alerts = alerts

    def _budget_exhausted(self, action: str, risk: RiskLevel, approval_required: bool = False) -> ToolResult:
        return self._record(
            action=action,
            risk=risk,
            message="Execution budget exhausted; no state was changed.",
            data={"metrics": self.state.metrics()},
            approval_required=approval_required,
        )

    def _record(
        self,
        *,
        action: str,
        risk: RiskLevel,
        message: str,
        data: dict[str, Any],
        budget_cost: int = 0,
        approval_required: bool = False,
    ) -> ToolResult:
        result = ToolResult(
            ok=True,
            action=action,
            risk=risk,
            message=message,
            data=data,
            budget_cost=budget_cost,
            approval_required=approval_required,
        )
        self.state.action_history.append(result.as_dict())
        return result

