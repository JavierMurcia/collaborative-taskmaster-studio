"""Runtime configuration for local and Cloud Run Sentinel deployments."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def load_local_environment() -> None:
    """Load the ignored project .env file when python-dotenv is available."""
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    load_dotenv(Path(__file__).resolve().parents[1] / ".env")


@dataclass(frozen=True)
class VertexSettings:
    project_id: str
    location: str
    model: str
    max_output_tokens: int
    max_plan_steps: int

    @classmethod
    def from_environment(cls) -> "VertexSettings | None":
        load_local_environment()
        if os.getenv("SENTINEL_ENABLE_VERTEX_PLANNER", "false").lower() != "true":
            return None
        project_id = os.getenv("GOOGLE_CLOUD_PROJECT", "").strip()
        if not project_id:
            return None
        return cls(
            project_id=project_id,
            location=os.getenv("GOOGLE_CLOUD_LOCATION", "global"),
            model=os.getenv("SENTINEL_GEMINI_MODEL", "gemini-3.5-flash"),
            max_output_tokens=int(os.getenv("SENTINEL_MAX_OUTPUT_TOKENS", "350")),
            max_plan_steps=int(os.getenv("SENTINEL_MAX_PLAN_STEPS", "3")),
        )
