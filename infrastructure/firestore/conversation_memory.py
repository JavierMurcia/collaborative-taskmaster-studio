"""Firestore-backed collaborative conversation memory."""

from __future__ import annotations

import hashlib
from typing import Any

from studio.application.conversation_memory import ConversationRecord

_COLLECTION = "collaborative_conversations"


def _owner_key(owner_session_id: str) -> str:
    return hashlib.sha256(owner_session_id.encode("utf-8")).hexdigest()


def _document_id(owner_session_id: str, conversation_id: str) -> str:
    value = f"{_owner_key(owner_session_id)}:{conversation_id}"
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


class FirestoreConversationMemoryRepository:
    """Small bounded collection; filtering is enforced again in application memory."""

    def __init__(self, client: Any) -> None:
        self._client = client

    def list(self, owner_session_id: str) -> tuple[ConversationRecord, ...]:
        owner = _owner_key(owner_session_id)
        records: list[ConversationRecord] = []
        query = self._client.collection(_COLLECTION).where("owner_hash", "==", owner)
        for snapshot in query.stream():
            payload = snapshot.to_dict() or {}
            if payload.get("owner_hash") != owner:
                continue
            try:
                records.append(ConversationRecord.model_validate(payload.get("conversation", {})))
            except ValueError:
                continue
        return tuple(sorted(records, key=lambda item: item.updated_at, reverse=True)[:40])

    def save(self, owner_session_id: str, record: ConversationRecord) -> None:
        self._client.collection(_COLLECTION).document(
            _document_id(owner_session_id, record.id)
        ).set(
            {
                "owner_hash": _owner_key(owner_session_id),
                "conversation": record.model_dump(mode="json"),
                "updated_at": record.updated_at,
            }
        )

    def delete(self, owner_session_id: str, conversation_id: str) -> None:
        self._client.collection(_COLLECTION).document(
            _document_id(owner_session_id, conversation_id)
        ).delete()
