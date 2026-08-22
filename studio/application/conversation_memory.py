"""Persistent, session-scoped memory for the collaborative chat."""

from __future__ import annotations

import builtins
import json
import re
import unicodedata
from typing import Any, Literal, Protocol, cast

from pydantic import BaseModel, ConfigDict, Field

from studio.domain.errors import DomainError
from studio.ports.clock import Clock


class ConversationMessage(BaseModel):
    """A visible chat message. Extra UI metadata is retained for recovery."""

    model_config = ConfigDict(extra="allow", str_strip_whitespace=True)

    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=6000)


class ConversationRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(pattern=r"^chat_[A-Za-z0-9-]{1,80}$")
    title: str = Field(min_length=1, max_length=100)
    messages: tuple[ConversationMessage, ...] = Field(max_length=32)
    document_ids: tuple[str, ...] = Field(default_factory=tuple, max_length=8)
    phase: Literal["discovery", "clarification", "alignment"] = "discovery"
    updated_at: str


class ConversationMemoryRepository(Protocol):
    def list(self, owner_session_id: str) -> tuple[ConversationRecord, ...]: ...

    def save(self, owner_session_id: str, record: ConversationRecord) -> None: ...

    def delete(self, owner_session_id: str, conversation_id: str) -> None: ...


class ConversationMemoryService:
    """Validates and persists recoverable chat state without cross-chat leakage."""

    _MAX_SERIALIZED_BYTES = 256_000
    _CONVERSATION_ID = re.compile(r"^chat_[A-Za-z0-9-]{1,80}$")

    def __init__(self, repository: ConversationMemoryRepository, clock: Clock) -> None:
        self._repository = repository
        self._clock = clock

    def list(self, owner_session_id: str) -> tuple[ConversationRecord, ...]:
        return self._repository.list(owner_session_id)[:40]

    def save(
        self,
        owner_session_id: str,
        *,
        conversation_id: str,
        title: str,
        messages: builtins.list[dict[str, Any]],
        phase: str,
        document_ids: builtins.list[str] | None = None,
    ) -> ConversationRecord:
        record = ConversationRecord(
            id=conversation_id,
            title=title,
            messages=tuple(ConversationMessage.model_validate(item) for item in messages),
            document_ids=tuple(document_ids or ()),
            phase=cast(Literal["discovery", "clarification", "alignment"], phase),
            updated_at=self._clock.now().isoformat(),
        )
        serialized = json.dumps(record.model_dump(mode="json"), ensure_ascii=False)
        if len(serialized.encode("utf-8")) > self._MAX_SERIALIZED_BYTES:
            raise DomainError(
                "CONVERSATION_MEMORY_TOO_LARGE",
                "La conversación supera el límite seguro de memoria persistente.",
            )
        self._repository.save(owner_session_id, record)
        return record

    def delete(self, owner_session_id: str, conversation_id: str) -> None:
        if not self._CONVERSATION_ID.fullmatch(conversation_id):
            raise DomainError("CONVERSATION_ID_INVALID", "La conversación no es válida.")
        self._repository.delete(owner_session_id, conversation_id)

    def recall(
        self,
        owner_session_id: str,
        query: str,
        *,
        exclude_conversation_id: str | None = None,
        limit: int = 4,
    ) -> tuple[dict[str, Any], ...]:
        """Retrieve relevant visible excerpts; never returns hidden tool payloads."""

        query_tokens = _memory_tokens(query)
        if len(query_tokens) < 2:
            return ()
        candidates: builtins.list[tuple[float, dict[str, Any]]] = []
        for position, conversation in enumerate(self.list(owner_session_id)):
            if conversation.id == exclude_conversation_id:
                continue
            for message in conversation.messages:
                tokens = _memory_tokens(message.content)
                overlap = query_tokens & tokens
                if not overlap:
                    continue
                score = len(overlap) / max(1, len(query_tokens)) + 0.1 / (position + 1)
                candidates.append(
                    (
                        score,
                        {
                            "conversation_id": conversation.id,
                            "title": conversation.title,
                            "role": message.role,
                            "excerpt": message.content[:700],
                            "score": round(score, 3),
                        },
                    )
                )
        candidates.sort(key=lambda item: item[0], reverse=True)
        return tuple(item for _, item in candidates[: max(1, min(limit, 8))])


_MEMORY_STOPWORDS = frozenset(
    {"para", "como", "esto", "esta", "este", "pero", "porque", "quiero", "necesito", "tengo", "donde", "cuando", "desde", "sobre", "entre", "unos", "unas", "the", "and", "with"}
)


def _memory_tokens(value: str) -> set[str]:
    normalized = unicodedata.normalize("NFKD", value.casefold())
    plain = "".join(character for character in normalized if not unicodedata.combining(character))
    return {
        token for token in re.findall(r"[a-z0-9]{3,}", plain)
        if token not in _MEMORY_STOPWORDS
    }
