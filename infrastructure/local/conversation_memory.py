"""In-memory and durable local collaborative conversation stores."""

from __future__ import annotations

import hashlib
import os
import threading
from pathlib import Path
from uuid import uuid4

from studio.application.conversation_memory import ConversationRecord


def _owner_key(owner_session_id: str) -> str:
    return hashlib.sha256(owner_session_id.encode("utf-8")).hexdigest()


class InMemoryConversationMemoryRepository:
    def __init__(self) -> None:
        self._records: dict[str, dict[str, ConversationRecord]] = {}
        self._lock = threading.RLock()

    def list(self, owner_session_id: str) -> tuple[ConversationRecord, ...]:
        with self._lock:
            records = self._records.get(_owner_key(owner_session_id), {}).values()
            return tuple(sorted(records, key=lambda item: item.updated_at, reverse=True))

    def save(self, owner_session_id: str, record: ConversationRecord) -> None:
        with self._lock:
            self._records.setdefault(_owner_key(owner_session_id), {})[record.id] = record

    def delete(self, owner_session_id: str, conversation_id: str) -> None:
        with self._lock:
            self._records.get(_owner_key(owner_session_id), {}).pop(conversation_id, None)


class JsonConversationMemoryRepository:
    """Atomic JSON persistence, partitioned by a one-way session hash."""

    def __init__(self, data_directory: Path) -> None:
        self._root = (data_directory / "conversations").resolve()
        self._root.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()

    def list(self, owner_session_id: str) -> tuple[ConversationRecord, ...]:
        directory = self._owner_directory(owner_session_id)
        if not directory.exists():
            return ()
        records: list[ConversationRecord] = []
        with self._lock:
            for path in directory.glob("chat_*.json"):
                try:
                    records.append(ConversationRecord.model_validate_json(path.read_text("utf-8")))
                except (OSError, ValueError):
                    continue
        return tuple(sorted(records, key=lambda item: item.updated_at, reverse=True)[:40])

    def save(self, owner_session_id: str, record: ConversationRecord) -> None:
        directory = self._owner_directory(owner_session_id)
        directory.mkdir(parents=True, exist_ok=True)
        target = self._target(directory, record.id)
        temporary = directory / f".{record.id}.{uuid4().hex}.tmp"
        with self._lock:
            temporary.write_text(record.model_dump_json(indent=2), encoding="utf-8")
            os.replace(temporary, target)

    def delete(self, owner_session_id: str, conversation_id: str) -> None:
        directory = self._owner_directory(owner_session_id)
        target = self._target(directory, conversation_id)
        with self._lock:
            target.unlink(missing_ok=True)

    def _owner_directory(self, owner_session_id: str) -> Path:
        return self._root / _owner_key(owner_session_id)

    @staticmethod
    def _target(directory: Path, conversation_id: str) -> Path:
        if not conversation_id.startswith("chat_") or not conversation_id.replace("-", "").replace("_", "").isalnum():
            raise ValueError("Invalid conversation id")
        return directory / f"{conversation_id}.json"
