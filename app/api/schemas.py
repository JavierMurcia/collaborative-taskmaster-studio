"""Validated HTTP request contracts for milestone H5."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class RequestModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class CreateProjectRequest(RequestModel):
    name: str = Field(min_length=3, max_length=100)
    description: str = Field(min_length=10, max_length=800)


class InterviewAnswerRequest(RequestModel):
    question_id: str = Field(pattern=r"^[a-z][a-z0-9_]{2,63}$")
    answer: str = Field(min_length=1, max_length=4000)


class BriefingCorrectionRequest(RequestModel):
    field: Literal[
        "deadline",
        "available_hours",
        "input_format",
        "external_actions",
        "approval_owner",
        "success_criteria",
    ]
    value: Any


class FeedbackRequest(RequestModel):
    expected_revision: int = Field(ge=1)
    feedback: str = Field(min_length=3, max_length=4000)


class ApprovalRequest(RequestModel):
    decision: Literal["approved", "rejected"]
    note: str = Field(default="", max_length=1000)


class GenerationRequest(RequestModel):
    revision: int = Field(ge=1)


class EvaluationRequest(RequestModel):
    revision: int = Field(ge=1)


class DemoResetRequest(RequestModel):
    confirmation: Literal["REINICIAR_DEMO"]


class AgentMessageRequest(RequestModel):
    message: str = Field(min_length=1, max_length=6000)


class AgentDecisionRequest(RequestModel):
    run_id: str = Field(pattern=r"^run_[a-f0-9]{16}$")
    decision: Literal["approved", "changes_requested", "rejected"]
    note: str = Field(default="", max_length=1000)


class CollaborativeChatTurn(RequestModel):
    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=6000)
    evidence: list[str] = Field(default_factory=list, max_length=8)


class CollaborativeChatRequest(RequestModel):
    message: str = Field(min_length=1, max_length=6000)
    history: list[CollaborativeChatTurn] = Field(default_factory=list, max_length=16)
    conversation_id: str | None = Field(default=None, pattern=r"^chat_[A-Za-z0-9-]{1,80}$")
    document_ids: list[str] = Field(default_factory=list, max_length=8)


class RefreshIdentityRequest(RequestModel):
    # Accepted only to migrate sessions created before the refresh token became
    # an HttpOnly cookie. New sessions never expose this token to JavaScript.
    refresh_token: str | None = Field(default=None, min_length=20, max_length=4096)


class CollaborativeConversationRequest(RequestModel):
    title: str = Field(min_length=1, max_length=100)
    messages: list[dict[str, Any]] = Field(default_factory=list, max_length=32)
    phase: Literal["discovery", "clarification", "alignment"] = "discovery"
    document_ids: list[str] = Field(default_factory=list, max_length=8)


class ChatBuildRequest(RequestModel):
    agent_draft: dict[str, Any]
    confirmation: Literal["CONSTRUIR_AGENTE"]


class ChatBuildDecisionRequest(RequestModel):
    decision: Literal["approved", "rejected"]


class CatalogAgentUpdateRequest(RequestModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    icon: Literal["spark", "workflow", "document", "research", "operations", "shield"] | None = None
