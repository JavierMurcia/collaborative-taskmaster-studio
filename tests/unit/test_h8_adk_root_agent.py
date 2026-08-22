from __future__ import annotations

import importlib
from pathlib import Path
from typing import Any

import pytest

from agents.root_agent import (
    ROOT_DESCRIPTION,
    ROOT_INSTRUCTION,
    RootAgentSettings,
    create_adk_app,
    create_root_agent,
)
from studio.domain.errors import DomainError


class FakeComponent:
    def __init__(self, **kwargs: Any) -> None:
        self.kwargs = kwargs


def test_root_factory_has_no_tools_and_accepts_only_registered_subagents() -> None:
    interviewer = object()
    designer = object()
    settings = RootAgentSettings(model="gemini-3.5-flash")

    root = create_root_agent(
        settings,
        sub_agents=[interviewer, designer],
        agent_factory=FakeComponent,
    )

    assert root.kwargs == {
        "name": "studio_root_agent",
        "description": ROOT_DESCRIPTION,
        "model": "gemini-3.5-flash",
        "instruction": ROOT_INSTRUCTION,
        "tools": [],
        "sub_agents": [interviewer, designer],
        "disallow_transfer_to_parent": True,
    }


def test_root_instruction_preserves_human_and_application_authority() -> None:
    normalized = ROOT_INSTRUCTION.casefold()

    assert "no apruebes diseños" in normalized
    assert "persona autenticada" in normalized
    assert "no inventes proyectos" in normalized
    assert "no ejecutes herramientas externas" in normalized
    assert "subagentes registrados" in normalized
    assert "datos no confiables" in normalized
    assert "cadenas de razonamiento" in normalized


def test_adk_app_factory_only_wraps_the_root() -> None:
    root = object()
    app = create_adk_app(
        root,
        RootAgentSettings(app_name="agents"),
        app_factory=FakeComponent,
    )

    assert app.kwargs == {"name": "agents", "root_agent": root}


@pytest.mark.parametrize(
    ("environment", "code"),
    [
        ({"STUDIO_GEMINI_MODEL": "invalid model"}, "ADK_MODEL_INVALID"),
        ({"STUDIO_ADK_ROOT_NAME": "Root-Agent"}, "ADK_AGENT_NAME_INVALID"),
        ({"STUDIO_ADK_APP_NAME": "A"}, "ADK_APP_NAME_INVALID"),
    ],
)
def test_root_settings_fail_closed(environment: dict[str, str], code: str) -> None:
    with pytest.raises(DomainError) as captured:
        RootAgentSettings.from_environment(environment)

    assert captured.value.code == code


def test_agents_cli_manifest_points_to_the_real_adk_package() -> None:
    root = Path(__file__).resolve().parents[2]
    manifest = (root / "agents-cli-manifest.yaml").read_text(encoding="utf-8")

    assert "agent_directory: agents" in manifest
    assert "entrypoint: agents.agent:root_agent" in manifest
    assert "contract: schemas/taskmaster-specification-1.0.0.json" in manifest
    assert "guided_interview" in manifest
    assert "deployment_target: none" in manifest
    assert "session_type: in_memory" in manifest
    assert (root / "agents" / "agent.py").is_file()


def test_installed_adk_can_load_the_discovery_entrypoint_without_a_model_call() -> None:
    pytest.importorskip("google.adk")
    from google.adk.cli.utils.agent_loader import AgentLoader

    entrypoint = importlib.import_module("agents.agent")
    project_root = Path(__file__).resolve().parents[2]
    loaded = AgentLoader(str(project_root)).load_agent("agents")

    assert entrypoint.root_agent.name == "studio_root_agent"
    assert [tool.name for tool in entrypoint.root_agent.tools] == [
        "interviewer_agent",
        "designer_agent",
    ]
    assert [agent.name for agent in entrypoint.root_agent.sub_agents] == [
        "interviewer_agent",
        "designer_agent",
    ]
    assert all(agent.parent_agent is entrypoint.root_agent for agent in entrypoint.root_agent.sub_agents)
    assert entrypoint.app.name == "agents"
    assert entrypoint.app.root_agent is entrypoint.root_agent
    assert loaded.name == "agents"
    assert loaded.root_agent.name == "studio_root_agent"
    assert [tool.name for tool in loaded.root_agent.tools] == [
        "interviewer_agent",
        "designer_agent",
    ]
    assert [agent.name for agent in loaded.root_agent.sub_agents] == [
        "interviewer_agent",
        "designer_agent",
    ]
