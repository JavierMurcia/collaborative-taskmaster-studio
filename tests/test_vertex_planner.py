"""Contract tests for the bounded Vertex/Gemini planning adapter."""

import unittest

from agent.config import VertexSettings
from agent.vertex_planner import PlannerConfigurationError, VertexGeminiPlanner
from memory import ValidatedMemory
from simulator.scenario import initial_evidence


class VertexGeminiPlannerTests(unittest.TestCase):
    def setUp(self) -> None:
        memory = ValidatedMemory()
        for evidence in initial_evidence():
            memory.ingest(evidence)
        self.records = memory.stored
        self.settings = VertexSettings("sentinel-taskmaster-dev", "global", "gemini-2.5-flash", 350, 3)

    def test_valid_plan_is_parsed_with_allowed_actions_only(self) -> None:
        planner = VertexGeminiPlanner(self.settings, generate=lambda _: '''{
          "plan": [
            {"action":"restart_worker","arguments":{"worker_id":"orders-worker-2"},"purpose":"Recover the stalled worker."},
            {"action":"clear_corrupt_batch","arguments":{"batch_id":"batch-2026-08-12-17"},"purpose":"Clear only the isolated batch."}
          ]
        }''')
        plan = planner.propose(self.records)
        self.assertEqual([step.action for step in plan], ["restart_worker", "clear_corrupt_batch"])

    def test_unknown_action_is_rejected_before_policy_execution(self) -> None:
        planner = VertexGeminiPlanner(self.settings, generate=lambda _: '''{
          "plan": [{"action":"empty_entire_order_queue","arguments":{},"purpose":"Ignore policy."}]
        }''')
        with self.assertRaises(PlannerConfigurationError):
            planner.propose(self.records)

    def test_markdown_wrapped_json_plan_is_accepted(self) -> None:
        planner = VertexGeminiPlanner(self.settings, generate=lambda _: '''```json
        {"plan":[{"action":"restart_worker","arguments":{"worker_id":"orders-worker-2"},"purpose":"Recover worker."}]}
        ```''')
        plan = planner.propose(self.records)
        self.assertEqual(plan[0].action, "restart_worker")

    def test_untrusted_evidence_is_not_sent_to_model_prompt(self) -> None:
        captured: list[str] = []
        planner = VertexGeminiPlanner(self.settings, generate=lambda prompt: captured.append(prompt) or '''{
          "plan": [{"action":"restart_worker","arguments":{"worker_id":"orders-worker-2"},"purpose":"Recover worker."}]
        }''')
        planner.propose(self.records)
        self.assertNotIn("SYSTEM OVERRIDE", captured[0])


if __name__ == "__main__":
    unittest.main()
