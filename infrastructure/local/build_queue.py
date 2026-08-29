"""Atomic local build queue used by development and offline installations."""

from __future__ import annotations

import json
import os
import re
import threading
from pathlib import Path

from studio.ports.build_queue import BuildQueueStore

_BUILD_ID = re.compile(r"^build_[a-f0-9]{16}$")
_PENDING = {"queued", "building", "testing"}


class JsonBuildQueueStore(BuildQueueStore):
    """Store one bounded JSON document per build with atomic replacement."""

    durable = True

    def __init__(self, data_directory: Path) -> None:
        self._root = (data_directory / "build-queue").resolve()
        self._root.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()

    def save(self, build_id: str, payload: dict[str, object]) -> None:
        path = self._path(build_id)
        temporary = path.with_suffix(".tmp")
        encoded = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
        with self._lock:
            temporary.write_text(encoded, encoding="utf-8", newline="\n")
            os.replace(temporary, path)

    def load(self, build_id: str, owner_session_id: str) -> dict[str, object] | None:
        payload = self.load_internal(build_id)
        if payload is None or payload.get("owner_session_id") != owner_session_id:
            return None
        return payload

    def load_internal(self, build_id: str) -> dict[str, object] | None:
        path = self._path(build_id)
        with self._lock:
            if not path.is_file():
                return None
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                return None
        return payload if isinstance(payload, dict) else None

    def list_pending(self) -> tuple[dict[str, object], ...]:
        pending: list[dict[str, object]] = []
        with self._lock:
            paths = tuple(sorted(self._root.glob("build_*.json")))[:200]
            for path in paths:
                try:
                    payload = json.loads(path.read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    continue
                if isinstance(payload, dict) and payload.get("state") in _PENDING:
                    pending.append(payload)
        return tuple(pending)

    def _path(self, build_id: str) -> Path:
        if not _BUILD_ID.fullmatch(build_id):
            raise ValueError("Invalid build identifier.")
        path = (self._root / f"{build_id}.json").resolve()
        path.relative_to(self._root)
        return path
