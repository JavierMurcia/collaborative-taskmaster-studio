from __future__ import annotations

from pathlib import Path

from studio.application.agent_catalog import AgentCatalog
from studio.application.plugin_registry import PluginSelection


def test_catalog_persists_updates_and_archives_agents(tmp_path: Path) -> None:
    catalog = AgentCatalog(tmp_path / "data")
    artifact = tmp_path / "generated" / "agent"
    artifact.mkdir(parents=True)
    plugin = PluginSelection(
        plugin_id="studio.documents",
        title="Documentos del Studio",
        availability="available",
        operations=("read",),
        reason="Seleccionado por las entradas y el flujo aprobados.",
    )

    created = catalog.register(
        build_id="build_1234567890abcdef",
        project_id="agent_1234567890abcdef",
        owner_session_id="owner_one",
        name="Redactor técnico",
        purpose="Crear documentación técnica verificable",
        framework="google_adk",
        framework_label="Google ADK",
        builder_runtime="controlled_local_builder",
        contract_digest="a" * 64,
        plugins=(plugin,),
        artifact_directory=artifact,
    )

    reloaded = AgentCatalog(tmp_path / "data")
    assert reloaded.list("owner_one") == (created,)
    assert reloaded.list("another_owner") == ()
    updated = reloaded.update(created.id, "owner_one", name="Redactor verificado", icon="document")
    assert updated.name == "Redactor verificado"
    assert updated.icon == "document"

    reloaded.archive(created.id, "owner_one")
    assert reloaded.list("owner_one") == ()
    assert reloaded.list("owner_one", include_archived=True)[0].status == "archived"


def test_catalog_registration_is_idempotent_for_build(tmp_path: Path) -> None:
    catalog = AgentCatalog(tmp_path)
    artifact = tmp_path / "artifact"
    artifact.mkdir()
    values = dict(
        build_id="build_1234567890abcdef",
        project_id="agent_1234567890abcdef",
        owner_session_id="owner_one",
        name="Investigador",
        purpose="Investigar fuentes públicas",
        framework="google_adk",
        framework_label="Google ADK",
        builder_runtime="controlled_local_builder",
        contract_digest="b" * 64,
        plugins=(),
        artifact_directory=artifact,
    )
    first = catalog.register(**values)
    second = catalog.register(**values)
    assert second.id == first.id
    assert len(catalog.list("owner_one")) == 1
