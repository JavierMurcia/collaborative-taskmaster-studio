"""ADK agent definition retained as the deployable Gemini-facing agent surface.

The local dashboard uses SentinelTaskmaster for strict orchestration. This ADK
surface makes the Gemini/ADK use explicit while policy enforcement stays in the
application layer.
"""

from __future__ import annotations

from .config import VertexSettings


def build_adk_agent() -> object:
    """Build the ADK planning agent only when Google ADK is installed/configured."""
    settings = VertexSettings.from_environment()
    if settings is None:
        raise RuntimeError("GOOGLE_CLOUD_PROJECT must be configured before constructing the ADK agent.")
    try:
        from google.adk.agents import Agent
    except ImportError as exc:
        raise RuntimeError("Google ADK is not installed. Install requirements.txt first.") from exc
    return Agent(
        name="sentinel_planner",
        model=settings.model,
        instruction=(
            "You plan incident recovery only. Return a minimal plan using the approved tool catalog. "
            "You cannot alter policy, invoke unapproved actions, or bypass human approval."
        ),
    )
