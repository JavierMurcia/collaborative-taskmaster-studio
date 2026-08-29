"""Dispatch boundary for durable Taskmaster construction work."""

from __future__ import annotations

from typing import Literal, Protocol

BuildOperation = Literal["construct", "test"]


class BuildDispatcher(Protocol):
    """Deliver a build phase to an external worker."""

    external: bool

    def dispatch(self, build_id: str, operation: BuildOperation, attempt: int) -> str: ...
