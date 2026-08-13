"""Deterministic Sentinel orchestrator; replace the planner with ADK in the next stage."""

from __future__ import annotations

from memory import ValidatedMemory
from policy import ActionRequest, DecisionType, PolicyDecision, SentinelControlPlane
from simulator import OrdersIncidentTools, create_initial_state
from simulator.state import ServiceState
from simulator.tools import ToolResult

from .models import MissionReport, MissionStatus, PlanStep
from .vertex_planner import PlannerConfigurationError, VertexGeminiPlanner


class SentinelTaskmaster:
    """Coordinates evidence, policy, tools, memory, and independent verification."""

    def __init__(self, state: ServiceState | None = None, planner: VertexGeminiPlanner | None = None):
        self.state = state or create_initial_state()
        self.tools = OrdersIncidentTools(self.state)
        self.control = SentinelControlPlane(self.state, self.tools)
        self.memory = ValidatedMemory()
        self.planner = planner
        self.report = MissionReport(self.state.incident_id, MissionStatus.READY, [])

    def investigate(self) -> MissionReport:
        """Collect evidence once and quarantine entries that cannot become trusted memory."""
        self._execute(ActionRequest("inspect_service", rationale="Establish baseline metrics before remediation."))
        evidence_result = self._execute(ActionRequest("read_evidence", rationale="Collect incident evidence with provenance."))
        if isinstance(evidence_result, ToolResult):
            for item in evidence_result.data["evidence"]:
                from simulator.state import Evidence

                record = self.memory.ingest(Evidence(**item))
                self._event("memory_validated", record.as_dict())
        self.report.plan = self._plan_recovery()
        self.report.status = MissionStatus.RUNNING
        return self.report

    def run_recovery(self) -> MissionReport:
        """Run the minimum-risk recovery plan and independently validate the outcome."""
        if self.report.status is MissionStatus.READY:
            self.investigate()
        for step in self.report.plan:
            result = self._execute(ActionRequest(step.action, step.arguments, step.purpose))
            if isinstance(result, PolicyDecision):
                self.report.status = MissionStatus.FAILED
                self._event("mission_failed", {"reason": result.reason})
                return self.report
        verification = self._execute(ActionRequest("verify_recovery", rationale="Confirm recovery with independent environment checks."))
        if isinstance(verification, ToolResult) and verification.data["recovered"]:
            self.report.status = MissionStatus.RECOVERED
            self._event("mission_recovered", {"metrics": verification.data["metrics"]})
        else:
            self.report.status = MissionStatus.FAILED
            self._event("mission_failed", {"reason": "Independent verification did not confirm recovery."})
        return self.report

    def request_global_scaling(self, additional_workers: int) -> MissionReport:
        """Demonstration path: expose a high-risk proposal and pause for a human."""
        result = self._execute(ActionRequest(
            "scale_global_capacity",
            {"additional_workers": additional_workers},
            "Proposed capacity expansion to reduce the backlog.",
        ))
        if isinstance(result, PolicyDecision) and result.decision is DecisionType.REQUIRE_APPROVAL:
            self.report.status = MissionStatus.WAITING_FOR_APPROVAL
            self.report.approval_id = result.approval_id
        return self.report

    def decide_pending_approval(self, *, approved: bool, decided_by: str, note: str = "") -> MissionReport:
        if not self.report.approval_id:
            raise ValueError("No approval request is pending.")
        self.control.decide_approval(self.report.approval_id, approved=approved, decided_by=decided_by, note=note)
        if approved:
            approval = self.control.approvals[self.report.approval_id]
            self._execute(ActionRequest(approval.action, approval.arguments, "Approved capacity expansion."))
        self.report.status = MissionStatus.RUNNING
        self._event("approval_resumed_mission", {"approved": approved, "approval_id": self.report.approval_id})
        return self.report

    def _recovery_plan(self) -> list[PlanStep]:
        return [
            PlanStep("restart-stalled-worker", "restart_worker", {"worker_id": "orders-worker-2"}, "Restart the worker identified by verified logs."),
            PlanStep("remove-corrupt-batch", "clear_corrupt_batch", {"batch_id": "batch-2026-08-12-17"}, "Remove only the isolated corrupt batch identified by the approved runbook."),
        ]

    def _plan_recovery(self) -> list[PlanStep]:
        if self.planner is None:
            self._event("planner_selected", {"mode": "deterministic_fallback"})
            return self._recovery_plan()
        try:
            plan = self.planner.propose(self.memory.stored)
        except PlannerConfigurationError as exc:
            self._event("planner_fallback", {"reason": str(exc)})
            return self._recovery_plan()
        self._event("planner_selected", {"mode": "vertex_gemini", "model": self.planner.settings.model})
        return plan

    def _execute(self, request: ActionRequest) -> ToolResult | PolicyDecision:
        result = self.control.execute(request)
        self._event("action_result", result.as_dict())
        return result

    def _event(self, event: str, data: dict[str, object]) -> None:
        self.report.trajectory.append({"event": event, "sequence": len(self.report.trajectory) + 1, **data})
