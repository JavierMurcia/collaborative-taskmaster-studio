"""UI-facing session facade for one local Sentinel demonstration."""

from __future__ import annotations

from typing import Any

from agent import SentinelTaskmaster, VertexGeminiPlanner


class DemoSession:
    def __init__(self) -> None:
        self.agent = self._new_agent()

    def reset(self) -> dict[str, Any]:
        self.agent = self._new_agent()
        return self.snapshot()

    @staticmethod
    def _new_agent() -> SentinelTaskmaster:
        """Use Vertex planning only when the local environment opts into it."""
        return SentinelTaskmaster(planner=VertexGeminiPlanner.from_environment())

    def investigate(self) -> dict[str, Any]:
        self.agent.investigate()
        return self.snapshot()

    def recover(self) -> dict[str, Any]:
        self.agent.run_recovery()
        return self.snapshot()

    def request_scaling(self, workers: int = 1) -> dict[str, Any]:
        self.agent.request_global_scaling(workers)
        return self.snapshot()

    def decide_approval(self, approved: bool) -> dict[str, Any]:
        self.agent.decide_pending_approval(
            approved=approved,
            decided_by="demo-operator",
            note="Decision recorded from local Sentinel dashboard.",
        )
        return self.snapshot()

    def snapshot(self) -> dict[str, Any]:
        report = self.agent.report
        approval = self.agent.control.approvals.get(report.approval_id) if report.approval_id else None
        return {
            "mission": report.as_dict(),
            "metrics": self.agent.state.metrics(),
            "memory": {
                "stored": [record.as_dict() for record in self.agent.memory.stored],
                "quarantined": [record.as_dict() for record in self.agent.memory.quarantined],
            },
            "approval": approval.as_dict() if approval else None,
            "policy_audit": list(self.agent.control.audit_events),
        }
