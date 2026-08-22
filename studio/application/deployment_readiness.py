"""Read-only deployment gate for cataloged agents."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict

from studio.application.agent_catalog import CatalogAgent
from studio.application.builder_readiness import BuilderReadiness


class DeploymentCheck(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str
    status: Literal["passed", "blocked", "required"]
    detail: str


class AgentDeploymentReadiness(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    agent_id: str
    target: Literal["gemini_enterprise_agent_platform"] = "gemini_enterprise_agent_platform"
    ready: bool
    checks: tuple[DeploymentCheck, ...]
    next_action: str


def assess_deployment(agent: CatalogAgent, builders: BuilderReadiness) -> AgentDeploymentReadiness:
    pending_plugins = tuple(
        plugin.title for plugin in agent.plugins if plugin.availability != "available"
    )
    platform = next(item for item in builders.capabilities if item.id == "agent_platform")
    checks = (
        DeploymentCheck(
            id="laboratory",
            status="passed" if agent.status == "ready" else "blocked",
            detail="El agente superó el laboratorio local."
            if agent.status == "ready"
            else "El agente no está listo.",
        ),
        DeploymentCheck(
            id="plugins",
            status="blocked" if pending_plugins else "passed",
            detail=(
                "Conectar antes de desplegar: " + ", ".join(pending_plugins)
                if pending_plugins
                else "Todos los plugins seleccionados están disponibles."
            ),
        ),
        DeploymentCheck(
            id="agent_platform",
            status="required" if platform.status == "setup_required" else "passed",
            detail=platform.detail,
        ),
        DeploymentCheck(
            id="human_release",
            status="required",
            detail="El despliegue necesita una confirmación humana separada y explícita.",
        ),
    )
    ready = all(item.status == "passed" for item in checks)
    return AgentDeploymentReadiness(
        agent_id=agent.id,
        ready=ready,
        checks=checks,
        next_action=(
            "Autorizar el despliegue controlado."
            if ready
            else "Completar los requisitos bloqueados sin incorporar credenciales al repositorio."
        ),
    )
