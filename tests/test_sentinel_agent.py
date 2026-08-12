"""End-to-end behavior tests for the deterministic Sentinel agent."""

import unittest

from agent import MissionStatus, SentinelTaskmaster
from memory import MemoryDisposition


class SentinelTaskmasterTests(unittest.TestCase):
    def test_agent_investigates_quarantines_and_recovers(self) -> None:
        agent = SentinelTaskmaster()
        agent.investigate()
        self.assertEqual(len(agent.memory.stored), 3)
        self.assertEqual(len(agent.memory.quarantined), 1)
        self.assertEqual(agent.memory.quarantined[0].disposition, MemoryDisposition.QUARANTINED)

        report = agent.run_recovery()
        self.assertEqual(report.status, MissionStatus.RECOVERED)
        self.assertTrue(agent.state.meets_recovery_targets())
        self.assertTrue(any(event["event"] == "mission_recovered" for event in report.trajectory))

    def test_high_risk_path_pauses_for_human_approval(self) -> None:
        agent = SentinelTaskmaster()
        report = agent.request_global_scaling(1)
        self.assertEqual(report.status, MissionStatus.WAITING_FOR_APPROVAL)
        self.assertIsNotNone(report.approval_id)
        self.assertEqual(agent.state.global_capacity, 3)

        agent.decide_pending_approval(approved=False, decided_by="demo-operator", note="Use reversible plan first.")
        self.assertEqual(agent.state.global_capacity, 3)
        self.assertEqual(agent.report.status, MissionStatus.RUNNING)


if __name__ == "__main__":
    unittest.main()
