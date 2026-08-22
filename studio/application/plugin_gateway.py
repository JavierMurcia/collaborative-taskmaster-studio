"""Fail-closed execution gateway for registered Taskmaster plugins."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from studio.application.plugin_registry import PluginRegistry
from studio.domain.errors import DomainError

PluginHandler = Callable[[str, dict[str, object]], dict[str, object]]


class PluginInvocation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    plugin_id: str
    operation_id: str
    arguments: dict[str, object] = Field(default_factory=dict)
    approved: bool = False


class PluginInvocationResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    plugin_id: str
    operation_id: str
    status: str
    output: dict[str, object]


class PluginGateway:
    """Dispatch only installed handlers after registry and approval checks."""

    def __init__(
        self,
        registry: PluginRegistry,
        handlers: dict[str, PluginHandler] | None = None,
    ) -> None:
        self._registry = registry
        self._handlers = dict(handlers or {})

    def invoke(self, invocation: PluginInvocation) -> PluginInvocationResult:
        manifest = self._registry.get(invocation.plugin_id)
        if manifest is None:
            raise DomainError("PLUGIN_NOT_REGISTERED", "El plugin solicitado no está registrado.")
        operation = next(
            (item for item in manifest.operations if item.id == invocation.operation_id),
            None,
        )
        if operation is None:
            raise DomainError("PLUGIN_OPERATION_UNKNOWN", "La operación no pertenece al plugin.")
        if manifest.availability != "available":
            raise DomainError(
                "PLUGIN_CONNECTION_REQUIRED",
                "El plugin requiere una conexión autorizada antes de utilizarse.",
                context={"plugin_id": manifest.id, "auth": manifest.auth},
            )
        if operation.requires_approval and not invocation.approved:
            raise DomainError(
                "PLUGIN_APPROVAL_REQUIRED",
                "La operación produce efectos externos y requiere aprobación humana.",
                context={"plugin_id": manifest.id, "operation_id": operation.id},
            )
        handler = self._handlers.get(manifest.id)
        if handler is None:
            raise DomainError(
                "PLUGIN_HANDLER_UNAVAILABLE",
                "El plugin está declarado, pero su adaptador de ejecución no está activo.",
                context={"plugin_id": manifest.id},
            )
        output: dict[str, Any] = handler(operation.id, invocation.arguments)
        return PluginInvocationResult(
            plugin_id=manifest.id,
            operation_id=operation.id,
            status="completed",
            output=output,
        )
