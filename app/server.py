"""Validated server binding for local development and Cloud Run."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True, slots=True)
class ServerBinding:
    host: str
    port: int
    source: Literal["cloud_run", "studio", "default"]


def resolve_server_binding(
    environment: Mapping[str, str] | None = None,
) -> ServerBinding:
    """Resolve a safe bind address, giving Cloud Run's PORT absolute priority."""

    values = os.environ if environment is None else environment
    if "PORT" in values:
        return ServerBinding(
            host="0.0.0.0",  # noqa: S104 - required by the Cloud Run container contract
            port=_parse_port(values["PORT"], "PORT"),
            source="cloud_run",
        )

    has_studio_override = "STUDIO_HOST" in values or "STUDIO_PORT" in values
    host = values.get("STUDIO_HOST", "127.0.0.1").strip()
    if not host:
        raise ValueError("STUDIO_HOST no puede estar vacío.")
    port = _parse_port(values.get("STUDIO_PORT", "8080"), "STUDIO_PORT")
    return ServerBinding(
        host=host,
        port=port,
        source="studio" if has_studio_override else "default",
    )


def _parse_port(value: str, variable: str) -> int:
    normalized = value.strip()
    if not normalized:
        raise ValueError(f"{variable} no puede estar vacío.")
    try:
        port = int(normalized)
    except ValueError as error:
        raise ValueError(f"{variable} debe ser un puerto numérico válido.") from error
    if not 1 <= port <= 65_535:
        raise ValueError(f"{variable} debe estar entre 1 y 65535.")
    return port
