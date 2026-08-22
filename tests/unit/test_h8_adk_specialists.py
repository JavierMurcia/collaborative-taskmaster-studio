from __future__ import annotations

import importlib
from typing import Any

import pytest

from agents.designer import (
    DESIGNER_DESCRIPTION,
    DESIGNER_INSTRUCTION,
    DESIGNER_NAME,
    create_designer_agent,
)
from agents.interviewer import (
    INTERVIEWER_DESCRIPTION,
    INTERVIEWER_INSTRUCTION,
    INTERVIEWER_NAME,
    create_interviewer_agent,
)


class FakeAgent:
    def __init__(self, **kwargs: Any) -> None:
        self.kwargs = kwargs


def test_interviewer_is_a_toolless_task_mode_leaf() -> None:
    agent = create_interviewer_agent("gemini-3.5-flash", agent_factory=FakeAgent)

    assert agent.kwargs == {
        "name": INTERVIEWER_NAME,
        "description": INTERVIEWER_DESCRIPTION,
        "model": "gemini-3.5-flash",
        "instruction": INTERVIEWER_INSTRUCTION,
        "mode": "task",
        "tools": [],
        "sub_agents": [],
        "disallow_transfer_to_parent": False,
        "disallow_transfer_to_peers": True,
    }


def test_designer_is_a_toolless_task_mode_leaf() -> None:
    agent = create_designer_agent("gemini-3.5-flash", agent_factory=FakeAgent)

    assert agent.kwargs == {
        "name": DESIGNER_NAME,
        "description": DESIGNER_DESCRIPTION,
        "model": "gemini-3.5-flash",
        "instruction": DESIGNER_INSTRUCTION,
        "mode": "task",
        "tools": [],
        "sub_agents": [],
        "disallow_transfer_to_parent": False,
        "disallow_transfer_to_peers": True,
    }


def test_specialist_mandates_are_distinct_and_preserve_human_authority() -> None:
    interviewer = INTERVIEWER_INSTRUCTION.casefold()
    designer = DESIGNER_INSTRUCTION.casefold()

    assert "una sola pregunta" in interviewer
    assert "no diseñes la especificación" in interviewer
    assert "no confirmes el briefing" in interviewer
    assert "briefing explícitamente confirmado" in designer
    assert "tu salida es solo una propuesta" in designer
    assert "no apruebes diseños" in designer
    assert "persona autenticada" in designer
    assert "no ejecutes herramientas" in interviewer
    assert "no ejecutes herramientas" in designer
    assert "datos no confiables" in interviewer
    assert "datos no confiables" in designer


def test_real_adk_entrypoint_registers_only_the_two_bounded_specialists() -> None:
    pytest.importorskip("google.adk")
    entrypoint = importlib.import_module("agents.agent")

    assert entrypoint.interviewer_agent.mode == "task"
    assert entrypoint.designer_agent.mode == "task"
    assert [tool.name for tool in entrypoint.interviewer_agent.tools] == ["finish_task"]
    assert [tool.name for tool in entrypoint.designer_agent.tools] == ["finish_task"]
    assert entrypoint.interviewer_agent.sub_agents == []
    assert entrypoint.designer_agent.sub_agents == []
    assert entrypoint.interviewer_agent.disallow_transfer_to_peers is True
    assert entrypoint.designer_agent.disallow_transfer_to_peers is True
    assert [agent.name for agent in entrypoint.root_agent.sub_agents] == [
        INTERVIEWER_NAME,
        DESIGNER_NAME,
    ]
