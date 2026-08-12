"""Risk, budget, and approval enforcement outside the model and tool prompts."""

from __future__ import annotations

from dataclasses import asdict
from typing import Callable
from uuid import uuid4

from simulator.state import RiskLevel, ServiceState
from simulator.tools import OrdersIncidentTools, ToolResult

from .models import (
    ActionRequest,
    ApprovalRequest,
    ApprovalStatus,
    DecisionType,
    PolicyDecision,
)


ACTION_RULES = {
    "inspect_service": (RiskLevel.LOW, 0, "Read current metrics without changing service state."),
    "read_evidence": (RiskLevel.LOW, 0, "Read evidence without changing service state."),
    "restart_worker": (RiskLevel.MEDIUM, 1, "Restarts one isolated worker."),
    "clear_corrupt_batch": (RiskLevel.MEDIUM, 1, "Removes only the identified isolated corrupt batch."),
    "scale_global_capacity": (RiskLevel.HIGH, 2, "Changes global service capacity and can affect cost and throughput."),
    "verify_recovery": (RiskLevel.LOW, 0, "Runs an independent read-only recovery check."),
}


class SentinelControlPlane:
    """The only supported route from an agent action proposal to a simulator tool."""

    def __init__(self, state: ServiceState, tools: OrdersIncidentTools):
        self.state = state
        self.tools = tools
        self.approvals: dict[str, ApprovalRequest] = {}
        self.audit_events: list[dict[str, object]] = []

    def evaluate(self, request: ActionRequest) -> PolicyDecision:
        rule = ACTION_RULES.get(request.action)
        if rule is None:
            return self._decision(DecisionType.DENY, request, RiskLevel.HIGH, 0, "Action is not in the approved tool catalog.")
        risk, cost, impact = rule
        if self.state.budget_remaining < cost:
            return self._decision(DecisionType.DENY, request, risk, cost, "Execution budget is insufficient; no tool call is allowed.")
        if risk is RiskLevel.HIGH:
            approval = self._find_matching_approval(request)
            if approval is None:
                approval = self._create_approval(request, risk, impact)
            if approval.status is ApprovalStatus.PENDING:
                return self._decision(DecisionType.REQUIRE_APPROVAL, request, risk, cost, "High-risk action requires an explicit human decision.", approval.approval_id)
            if approval.status is ApprovalStatus.REJECTED:
                return self._decision(DecisionType.DENY, request, risk, cost, "Human approver rejected this high-risk action.", approval.approval_id)
        return self._decision(DecisionType.ALLOW, request, risk, cost, "Action satisfies risk policy and remaining budget.")

    def decide_approval(self, approval_id: str, *, approved: bool, decided_by: str, note: str = "") -> ApprovalRequest:
        approval = self.approvals.get(approval_id)
        if approval is None:
            raise KeyError(f"Unknown approval request: {approval_id}")
        if approval.status is not ApprovalStatus.PENDING:
            raise ValueError(f"Approval {approval_id} has already been decided.")
        approval.status = ApprovalStatus.APPROVED if approved else ApprovalStatus.REJECTED
        approval.decided_by = decided_by
        approval.decision_note = note
        self._audit("approval_decided", {"approval": approval.as_dict()})
        return approval

    def execute(self, request: ActionRequest) -> ToolResult | PolicyDecision:
        decision = self.evaluate(request)
        if decision.decision is not DecisionType.ALLOW:
            self._audit("action_blocked", {"request": asdict(request), "decision": decision.as_dict()})
            return decision
        result = self._dispatch(request)
        self._audit("action_executed", {"request": asdict(request), "decision": decision.as_dict(), "result": result.as_dict()})
        return result

    def _dispatch(self, request: ActionRequest) -> ToolResult:
        dispatch: dict[str, Callable[[], ToolResult]] = {
            "inspect_service": self.tools.inspect_service,
            "read_evidence": self.tools.read_evidence,
            "restart_worker": lambda: self.tools.restart_worker(str(request.arguments["worker_id"])),
            "clear_corrupt_batch": lambda: self.tools.clear_corrupt_batch(str(request.arguments["batch_id"])),
            "scale_global_capacity": lambda: self.tools.scale_global_capacity(int(request.arguments["additional_workers"]), approved=True),
            "verify_recovery": self.tools.verify_recovery,
        }
        try:
            return dispatch[request.action]()
        except KeyError as exc:
            raise ValueError(f"Missing required argument for {request.action}: {exc.args[0]}") from exc

    def _create_approval(self, request: ActionRequest, risk: RiskLevel, impact: str) -> ApprovalRequest:
        approval = ApprovalRequest(
            approval_id=f"apr-{uuid4().hex[:10]}",
            action=request.action,
            arguments=dict(request.arguments),
            rationale=request.rationale,
            risk=risk,
            impact=impact,
        )
        self.approvals[approval.approval_id] = approval
        self._audit("approval_requested", {"approval": approval.as_dict()})
        return approval

    def _find_matching_approval(self, request: ActionRequest) -> ApprovalRequest | None:
        for approval in self.approvals.values():
            if approval.action == request.action and approval.arguments == request.arguments:
                return approval
        return None

    def _decision(self, kind: DecisionType, request: ActionRequest, risk: RiskLevel, cost: int, reason: str, approval_id: str | None = None) -> PolicyDecision:
        decision = PolicyDecision(kind, request.action, risk, reason, cost, approval_id)
        self._audit("policy_evaluated", {"request": asdict(request), "decision": decision.as_dict()})
        return decision

    def _audit(self, event: str, data: dict[str, object]) -> None:
        self.audit_events.append({"event": event, "sequence": len(self.audit_events) + 1, **data})
