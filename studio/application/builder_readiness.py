"""Truthful readiness report for local and Google agent construction backends."""

from __future__ import annotations

import importlib.util
import os
import shutil
import subprocess
from pathlib import Path
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
    antigravity_python = os.getenv("STUDIO_ANTIGRAVITY_PYTHON", "").strip()
    antigravity = _antigravity_worker_available(antigravity_python)
    adk = _module_available("google.adk")
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
                "Entorno aislado detectado y habilitable mediante STUDIO_AGENT_BUILDER=antigravity."
                if antigravity
                else (
                    "Configura STUDIO_ANTIGRAVITY_PYTHON con un Python aislado que tenga "
                    "google-antigravity; no se simula su uso."
                )
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


def _module_available(name: str) -> bool:
    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ModuleNotFoundError, ValueError):
        return False


def _antigravity_worker_available(python_executable: str) -> bool:
    if not python_executable or not Path(python_executable).is_file():
        return False
    try:
        result = subprocess.run(
            [python_executable, "-c", "import google.antigravity"],
            check=False,
            capture_output=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return result.returncode == 0
