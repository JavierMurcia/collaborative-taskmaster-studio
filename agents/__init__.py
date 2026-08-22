"""Google ADK agent package with lazy optional-dependency loading."""

from __future__ import annotations

from importlib import import_module
from typing import Any

__all__ = ["app", "designer_agent", "interviewer_agent", "root_agent"]


def __getattr__(name: str) -> Any:
    if name not in __all__:
        raise AttributeError(name)
    entrypoint = import_module("agents.agent")
    return getattr(entrypoint, name)

