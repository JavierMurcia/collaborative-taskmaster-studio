"""Deterministic capability selection from an approved conversational draft."""

from __future__ import annotations

from collections.abc import Iterable

_WORKSPACE_READ_TERMS = (
    "leer archivo",
    "leer archivos",
    "lectura de archivo",
    "inspeccionar archivo",
    "inspeccionar directorio",
    "directorio del agente",
    "carpeta del agente",
    "espacio de trabajo",
    "workspace read",
    "workspace.read",
    "read files",
    "inspect files",
    "local documents",
    "documentos locales",
    "fuentes locales",
    "hoja de cálculo",
    "hojas de cálculo",
    "hoja de calculo",
    "hojas de calculo",
    "excel",
    "xlsx",
    "csv",
    "dataset",
    "conjunto de datos",
    "procesar documentos",
    "analizar documentos",
    "leer documentos",
)


def requires_workspace_read(*values: str | Iterable[str]) -> bool:
    """Return true only for an explicit read/inspection signal, never for generic documents."""

    parts: list[str] = []
    for value in values:
        if isinstance(value, str):
            parts.append(value)
        else:
            parts.extend(value)
    text = " ".join(parts).casefold()
    return any(term in text for term in _WORKSPACE_READ_TERMS)
