"""Tests for safe local Vertex configuration loading."""

import os
import unittest
from unittest.mock import patch

from agent.config import VertexSettings


class VertexSettingsTests(unittest.TestCase):
    def test_missing_project_disables_live_planner(self) -> None:
        with patch.dict(os.environ, {"GOOGLE_CLOUD_PROJECT": ""}, clear=False):
            with patch("agent.config.load_local_environment"):
                self.assertIsNone(VertexSettings.from_environment())

    def test_environment_applies_explicit_cost_limits(self) -> None:
        values = {
            "GOOGLE_CLOUD_PROJECT": "sentinel-taskmaster-dev",
            "GOOGLE_CLOUD_LOCATION": "global",
            "SENTINEL_GEMINI_MODEL": "gemini-3.5-flash",
            "SENTINEL_ENABLE_VERTEX_PLANNER": "true",
            "SENTINEL_MAX_OUTPUT_TOKENS": "350",
            "SENTINEL_MAX_PLAN_STEPS": "3",
        }
        with patch.dict(os.environ, values, clear=False):
            with patch("agent.config.load_local_environment"):
                settings = VertexSettings.from_environment()
        self.assertEqual(settings.project_id, "sentinel-taskmaster-dev")
        self.assertEqual(settings.max_output_tokens, 350)
        self.assertEqual(settings.max_plan_steps, 3)
