"""Durable state boundary for asynchronous Taskmaster construction jobs."""

from __future__ import annotations

from typing import Protocol


class BuildQueueStore(Protocol):
    """Persist complete build records so a worker can resume after a restart."""

    durable: bool

    def save(self, build_id: str, payload: dict[str, object]) -> None: ...

    def load(self, build_id: str, owner_session_id: str) -> dict[str, object] | None: ...

    def load_internal(self, build_id: str) -> dict[str, object] | None: ...

    def list_pending(self) -> tuple[dict[str, object], ...]: ...
