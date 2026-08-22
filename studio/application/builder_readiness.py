"""Truthful readiness report for local and Google agent construction backends."""

from __future__ import annotations

import importlib.util
import os
import shutil
from typing import Literal

from pydantic import BaseModel, ConfigDict


class BuilderCapability(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str
    label: str
    status: Literal["active", "available", "setup_required", "unsupported"]
    detail: str


class BuilderReadiness(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    active_builder: str
    capabilities: tuple[BuilderCapability, ...]


def inspect_builder_readiness() -> BuilderReadiness:
    agents_cli = shutil.which("agents-cli")
    antigravity = shutil.which("antigravity") or importlib.util.find_spec("google.antigravity")
    adk = importlib.util.find_spec("google.adk")
    requested = os.getenv("STUDIO_AGENT_BUILDER", "controlled_adk").casefold()
    active = "controlled_adk"
    if requested == "agents_cli" and agents_cli:
        active = "agents_cli"
    elif requested == "antigravity" and antigravity:
        active = "antigravity"
    capabilities = (
        BuilderCapability(
            id="controlled_adk",
            label="Constructor controlado Google ADK",
            status="active" if active == "controlled_adk" else "available",
            detail=(
                "Generación determinista, contrato firmado y laboratorio sin red."
                if adk
                else "Genera la plantilla ADK; el SDK debe instalarse para ejecutarla."
            ),
        ),
        BuilderCapability(
            id="agents_cli",
            label="Google Agents CLI",
            status="active"
            if active == "agents_cli"
            else ("available" if agents_cli else "setup_required"),
            detail=(
                "CLI detectada y habilitable mediante STUDIO_AGENT_BUILDER=agents_cli."
                if agents_cli
                else "No instalada en este entorno; en Windows Google recomienda utilizar WSL2."
            ),
        ),
        BuilderCapability(
            id="antigravity",
            label="Antigravity",
            status="active"
            if active == "antigravity"
            else ("available" if antigravity else "setup_required"),
            detail=(
                "Runtime detectado y habilitable mediante STUDIO_AGENT_BUILDER=antigravity."
                if antigravity
                else "No hay un runtime o SDK de Antigravity instalado; no se simula su uso."
            ),
        ),
        BuilderCapability(
            id="agent_platform",
            label="Gemini Enterprise Agent Platform",
            status="setup_required",
            detail="El despliegue exige identidad, IAM y autorización explícita en Google Cloud.",
        ),
    )
    return BuilderReadiness(active_builder=active, capabilities=capabilities)
