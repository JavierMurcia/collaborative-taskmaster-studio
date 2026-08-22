"""Application port for untrusted structured model generation."""

from __future__ import annotations

from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict, Field

from studio.domain.errors import DomainError


class GatewayModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ModelRequest(GatewayModel):
    """A bounded request that cannot expose tools or mutate application state."""

    purpose: str = Field(min_length=1, max_length=64, pattern=r"^[a-z][a-z0-9_]*$")
    system_instruction: str = Field(min_length=1, max_length=8_000)
    prompt: str = Field(min_length=1, max_length=32_000)
    response_schema: dict[str, Any]
    max_output_tokens: int = Field(default=512, ge=1, le=8_192)
    temperature: float = Field(default=0.2, ge=0.0, le=1.0)


class ModelUsage(GatewayModel):
    prompt_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
    total_tokens: int | None = Field(default=None, ge=0)


class ModelMetadata(GatewayModel):
    provider: str
    model: str
    model_version: str | None = None
    location: str
    response_id: str | None = None
    latency_ms: float = Field(ge=0)
    usage: ModelUsage


class ModelResult(GatewayModel):
    payload: dict[str, Any]
    metadata: ModelMetadata


class ModelGateway(Protocol):
    def generate_structured(self, request: ModelRequest) -> ModelResult: ...


def model_metadata_details(metadata: ModelMetadata) -> dict[str, Any]:
    """Return the complete allow-listed telemetry safe for an audit event."""
    return {
        "provider": metadata.provider,
        "model": metadata.model,
        "model_version": metadata.model_version,
        "location": metadata.location,
        "response_id": metadata.response_id,
        "latency_ms": metadata.latency_ms,
        "usage": metadata.usage.model_dump(mode="json"),
    }


def enrich_model_error(error: DomainError, metadata: ModelMetadata) -> DomainError:
    """Attach only trusted model telemetry while retaining stable domain error details."""
    return DomainError(
        error.code,
        error.message,
        context={**error.context, **model_metadata_details(metadata)},
    )


def model_error_details(error: DomainError) -> dict[str, Any]:
    """Extract an allow-listed telemetry subset from a failed model attempt."""
    details: dict[str, Any] = {"error_code": error.code}
    for key in (
        "provider",
        "model",
        "model_version",
        "location",
        "response_id",
        "latency_ms",
        "usage",
    ):
        if key in error.context:
            details[key] = error.context[key]
    return details
