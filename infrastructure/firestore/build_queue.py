"""Firestore-backed durable queue for asynchronous Taskmaster builds."""

from __future__ import annotations

import hashlib
from typing import Any

from studio.ports.build_queue import BuildQueueStore

_COLLECTION = "agent_build_queue"
_PENDING = ("queued", "building", "testing")


def _owner_key(owner_session_id: str) -> str:
    return hashlib.sha256(owner_session_id.encode("utf-8")).hexdigest()


class FirestoreBuildQueueStore(BuildQueueStore):
    """Persist owner-scoped build records independently from Cloud Run instances."""

    durable = True

    def __init__(self, client: Any) -> None:
        self._client = client

    def save(self, build_id: str, payload: dict[str, object]) -> None:
        owner = str(payload.get("owner_session_id", ""))
        self._client.collection(_COLLECTION).document(build_id).set(
            {
                "owner_hash": _owner_key(owner),
                "state": payload.get("state"),
                "updated_at": payload.get("updated_at"),
                "record": payload,
            }
        )

    def load(self, build_id: str, owner_session_id: str) -> dict[str, object] | None:
        snapshot = self._client.collection(_COLLECTION).document(build_id).get()
        if not snapshot.exists:
            return None
        document = snapshot.to_dict() or {}
        if document.get("owner_hash") != _owner_key(owner_session_id):
            return None
        record = document.get("record")
        return record if isinstance(record, dict) else None

    def list_pending(self) -> tuple[dict[str, object], ...]:
        records: list[dict[str, object]] = []
        query = self._client.collection(_COLLECTION).where("state", "in", _PENDING)
        for snapshot in query.limit(50).stream():
            record = (snapshot.to_dict() or {}).get("record")
            if isinstance(record, dict):
                records.append(record)
        return tuple(records)

