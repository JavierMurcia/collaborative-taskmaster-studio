"""Google ADK discovery entry point for Collaborative Taskmaster Studio."""

from agents.designer import create_designer_agent
from agents.interviewer import create_interviewer_agent
from agents.root_agent import RootAgentSettings, create_adk_app, create_root_agent

settings = RootAgentSettings.from_environment()
interviewer_agent = create_interviewer_agent(settings.model)
designer_agent = create_designer_agent(settings.model)
root_agent = create_root_agent(
    settings,
    sub_agents=[interviewer_agent, designer_agent],
)
app = create_adk_app(root_agent, settings)

__all__ = ["app", "designer_agent", "interviewer_agent", "root_agent"]
