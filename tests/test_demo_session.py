"""Tests for the UI-facing local demonstration session."""

import unittest

from app.session import DemoSession


class DemoSessionTests(unittest.TestCase):
    def test_dashboard_state_shows_quarantine_and_recovery(self) -> None:
        session = DemoSession()
        session.investigate()
        after_investigation = session.snapshot()
        self.assertEqual(len(after_investigation["memory"]["quarantined"]), 1)
        self.assertEqual(after_investigation["mission"]["status"], "running")

        recovered = session.recover()
        self.assertEqual(recovered["mission"]["status"], "recovered")
        self.assertTrue(recovered["metrics"]["queue_depth"] <= 100)

    def test_dashboard_state_exposes_pending_human_approval(self) -> None:
        session = DemoSession()
        waiting = session.request_scaling()
        self.assertEqual(waiting["mission"]["status"], "waiting_for_approval")
        self.assertEqual(waiting["approval"]["status"], "pending")


if __name__ == "__main__":
    unittest.main()
