from datetime import UTC, datetime

from studio.application.agent_catalog import CatalogAgent
from studio.application.builder_readiness import BuilderCapability, BuilderReadiness
from studio.application.deployment_readiness import assess_deployment
from studio.application.plugin_registry import PluginSelection


def test_deployment_stays_blocked_until_plugin_platform_and_human_release() -> None:
    agent = CatalogAgent(
        id="catalog_1234567890abcdef",
        build_id="build_1234567890abcdef",
        project_id="agent_1234567890abcdef",
        owner_session_id="owner",
        name="Coordinador",
        purpose="Enviar resultados por correo",
        framework="google_adk",
        framework_label="Google ADK",
        builder_runtime="controlled_local_builder",
        contract_digest="a" * 64,
        plugins=(
            PluginSelection(
                plugin_id="google.gmail",
                title="Gmail",
                availability="connection_required",
                operations=("send",),
                reason="Solicitado explícitamente en el diseño.",
            ),
        ),
        artifact_directory="generated/agent",
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    builders = BuilderReadiness(
        active_builder="controlled_adk",
        capabilities=(
            BuilderCapability(
                id="agent_platform",
                label="Agent Platform",
                status="setup_required",
                detail="IAM pendiente.",
            ),
        ),
    )

    readiness = assess_deployment(agent, builders)

    assert readiness.ready is False
    assert [item.id for item in readiness.checks] == [
        "laboratory",
        "plugins",
        "agent_platform",
        "human_release",
    ]
    assert readiness.checks[1].status == "blocked"
