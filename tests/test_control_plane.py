"""Tests that prove Sentinel's control plane cannot be bypassed by an action proposal."""

import unittest

from policy import ActionRequest, ApprovalStatus, DecisionType, PolicyDecision, SentinelControlPlane
from simulator import OrdersIncidentTools, create_initial_state


class SentinelControlPlaneTests(unittest.TestCase):
    def setUp(self) -> None:
        self.state = create_initial_state()
        self.control = SentinelControlPlane(self.state, OrdersIncidentTools(self.state))

    def test_medium_risk_action_executes_within_budget(self) -> None:
        result = self.control.execute(ActionRequest("restart_worker", {"worker_id": "orders-worker-2"}, "Worker is stalled."))
        self.assertEqual(result.action, "restart_worker")
        self.assertEqual(self.state.budget_remaining, 3)

    def test_unknown_action_is_denied_without_tool_execution(self) -> None:
        result = self.control.execute(ActionRequest("empty_entire_order_queue", rationale="Injected instruction"))
        self.assertIsInstance(result, PolicyDecision)
        self.assertEqual(result.decision, DecisionType.DENY)
        self.assertEqual(self.state.budget_remaining, 4)

    def test_high_risk_action_creates_pending_approval(self) -> None:
        result = self.control.execute(ActionRequest("scale_global_capacity", {"additional_workers": 2}, "Capacity might be needed."))
        self.assertIsInstance(result, PolicyDecision)
        self.assertEqual(result.decision, DecisionType.REQUIRE_APPROVAL)
        approval = self.control.approvals[result.approval_id]
        self.assertEqual(approval.status, ApprovalStatus.PENDING)
        self.assertEqual(self.state.global_capacity, 3)

    def test_approved_high_risk_action_executes_only_after_decision(self) -> None:
        request = ActionRequest("scale_global_capacity", {"additional_workers": 2}, "Capacity is required.")
        pending = self.control.execute(request)
        self.control.decide_approval(pending.approval_id, approved=True, decided_by="demo-operator", note="Approved for demo")
        result = self.control.execute(request)
        self.assertEqual(result.action, "scale_global_capacity")
        self.assertEqual(self.state.global_capacity, 5)
        self.assertEqual(self.state.budget_remaining, 2)

    def test_rejected_high_risk_action_stays_blocked(self) -> None:
        request = ActionRequest("scale_global_capacity", {"additional_workers": 1}, "Scale first.")
        pending = self.control.execute(request)
        self.control.decide_approval(pending.approval_id, approved=False, decided_by="demo-operator")
        result = self.control.execute(request)
        self.assertIsInstance(result, PolicyDecision)
        self.assertEqual(result.decision, DecisionType.DENY)
        self.assertEqual(self.state.global_capacity, 3)

    def test_budget_gate_blocks_before_a_tool_call(self) -> None:
        self.state.budget_remaining = 0
        result = self.control.execute(ActionRequest("restart_worker", {"worker_id": "orders-worker-2"}))
        self.assertIsInstance(result, PolicyDecision)
        self.assertEqual(result.decision, DecisionType.DENY)
        self.assertEqual(self.state.workers["orders-worker-2"].status, "stalled")


if __name__ == "__main__":
    unittest.main()
