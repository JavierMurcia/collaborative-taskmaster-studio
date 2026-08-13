"""Runtime configuration for local and Cloud Run Sentinel deployments."""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class VertexSettings:
    project_id: str
    location: str
    model: str
    max_output_tokens: int
    max_plan_steps: int

    @classmethod
    def from_environment(cls) -> "VertexSettings | None":
        project_id = os.getenv("GOOGLE_CLOUD_PROJECT", "").strip()
        if not project_id:
            return None
        return cls(
            project_id=project_id,
            location=os.getenv("GOOGLE_CLOUD_LOCATION", "global"),
            model=os.getenv("SENTINEL_GEMINI_MODEL", "gemini-2.5-flash"),
            max_output_tokens=int(os.getenv("SENTINEL_MAX_OUTPUT_TOKENS", "350")),
            max_plan_steps=int(os.getenv("SENTINEL_MAX_PLAN_STEPS", "3")),
        )
