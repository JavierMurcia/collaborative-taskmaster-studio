from __future__ import annotations

import pytest

from studio.application.plugin_gateway import PluginGateway, PluginInvocation
from studio.application.plugin_registry import PluginRegistry
from studio.domain.errors import DomainError


def test_registry_selects_small_relevant_plugin_set() -> None:
    registry = PluginRegistry()

    selected = registry.select(
        purpose="Investigar contratos en documentos y enviar un resumen por correo",
        workflow=("Buscar fuentes en Internet", "Leer PDF", "Preparar correo"),
        inputs=("Documentos PDF",),
        outputs=("Informe",),
        external_actions=("google.gmail",),
    )

    assert 1 <= len(selected) <= 3
    assert selected[0].plugin_id == "google.gmail"
    assert any(item.plugin_id == "studio.web" for item in selected)


def test_gateway_fails_closed_for_unconnected_and_unhandled_plugins() -> None:
    registry = PluginRegistry()
    gateway = PluginGateway(registry)

    with pytest.raises(DomainError, match="conexión autorizada"):
        gateway.invoke(
            PluginInvocation(
                plugin_id="google.gmail",
                operation_id="read_messages",
            )
        )

    with pytest.raises(DomainError, match="adaptador de ejecución"):
        gateway.invoke(
            PluginInvocation(plugin_id="studio.web", operation_id="search")
        )


def test_gateway_dispatches_registered_read_only_handler() -> None:
    gateway = PluginGateway(
        PluginRegistry(),
        handlers={"studio.web": lambda operation, arguments: {
            "operation": operation,
            "query": arguments["query"],
        }},
    )

    result = gateway.invoke(
        PluginInvocation(
            plugin_id="studio.web",
            operation_id="search",
            arguments={"query": "agentes"},
        )
    )

    assert result.status == "completed"
    assert result.output == {"operation": "search", "query": "agentes"}
