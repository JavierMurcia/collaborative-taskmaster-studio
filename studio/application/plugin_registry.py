"""Versioned plugin catalog and deterministic least-privilege selection."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

PluginAvailability = Literal["available", "connection_required", "unsupported"]
PluginAuth = Literal["none", "adc", "oauth", "api_key"]
PluginRisk = Literal["low", "medium", "high", "critical"]
PluginMode = Literal["read_only", "write"]


class PluginOperation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(pattern=r"^[a-z][a-z0-9_.-]{1,79}$")
    title: str = Field(min_length=1, max_length=100)
    mode: PluginMode
    risk: PluginRisk
    requires_approval: bool
    input_schema: dict[str, object] = Field(default_factory=dict)
    output_schema: dict[str, object] = Field(default_factory=dict)


class PluginManifest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(pattern=r"^[a-z][a-z0-9_.-]{1,79}$")
    version: str = Field(pattern=r"^[0-9]+\.[0-9]+\.[0-9]+$")
    title: str = Field(min_length=1, max_length=100)
    description: str = Field(min_length=1, max_length=500)
    provider: str = Field(min_length=1, max_length=100)
    auth: PluginAuth
    availability: PluginAvailability
    permissions: tuple[str, ...] = ()
    operations: tuple[PluginOperation, ...]
    keywords: tuple[str, ...] = ()


class PluginSelection(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    plugin_id: str
    title: str
    availability: PluginAvailability
    operations: tuple[str, ...]
    reason: str


class PluginRegistry:
    """Closed registry used by the selector, builder and policy gateway."""

    def __init__(self, manifests: Iterable[PluginManifest] = ()) -> None:
        catalog = tuple(manifests) or default_plugin_manifests()
        self._manifests = {manifest.id: manifest for manifest in catalog}
        if len(self._manifests) != len(catalog):
            raise ValueError("Los identificadores de plugins deben ser únicos.")

    def list(self) -> tuple[PluginManifest, ...]:
        return tuple(sorted(self._manifests.values(), key=lambda item: item.id))

    def get(self, plugin_id: str) -> PluginManifest | None:
        return self._manifests.get(plugin_id)

    def select(
        self,
        *,
        purpose: str,
        workflow: Iterable[str],
        inputs: Iterable[str],
        outputs: Iterable[str],
        external_actions: Iterable[str],
    ) -> tuple[PluginSelection, ...]:
        text = " ".join([purpose, *workflow, *inputs, *outputs, *external_actions]).casefold()
        explicit = {value.casefold().replace("_", ".") for value in external_actions}
        ranked: list[tuple[int, PluginManifest]] = []
        for manifest in self._manifests.values():
            keyword_hits = sum(1 for keyword in manifest.keywords if keyword.casefold() in text)
            explicit_hit = any(
                manifest.id in value or value in manifest.id for value in explicit if value
            )
            score = keyword_hits + (5 if explicit_hit else 0)
            if score:
                ranked.append((score, manifest))
        ranked.sort(key=lambda item: (-item[0], item[1].id))
        return tuple(
            PluginSelection(
                plugin_id=manifest.id,
                title=manifest.title,
                availability=manifest.availability,
                operations=tuple(operation.id for operation in manifest.operations),
                reason=(
                    "Solicitado explícitamente en el diseño."
                    if score >= 5
                    else "Seleccionado por las entradas y el flujo aprobados."
                ),
            )
            for score, manifest in ranked[:3]
        )


def _operation(
    identifier: str,
    title: str,
    *,
    mode: PluginMode = "read_only",
    risk: PluginRisk = "low",
    approval: bool = False,
) -> PluginOperation:
    return PluginOperation(
        id=identifier,
        title=title,
        mode=mode,
        risk=risk,
        requires_approval=approval,
        input_schema={"type": "object", "additionalProperties": False},
        output_schema={"type": "object"},
    )


def default_plugin_manifests() -> tuple[PluginManifest, ...]:
    return (
        PluginManifest(
            id="studio.web",
            version="1.0.0",
            title="Investigación web",
            description="Busca información pública con fuentes verificables.",
            provider="Studio / Vertex AI",
            auth="adc",
            availability="available",
            operations=(_operation("search", "Buscar en Internet"),),
            keywords=("internet", "web", "buscar", "investigar", "fuentes", "actual"),
        ),
        PluginManifest(
            id="studio.workspace",
            version="1.0.0",
            title="Workspace confinado",
            description="Lee archivos permitidos dentro de un directorio asignado.",
            provider="Studio",
            auth="none",
            availability="available",
            operations=(_operation("read", "Leer archivo"), _operation("list", "Listar carpeta")),
            keywords=("archivo", "directorio", "carpeta", "workspace", "repositorio", "código"),
        ),
        PluginManifest(
            id="studio.documents",
            version="1.0.0",
            title="Documentos adjuntos",
            description="Extrae y consulta documentos aportados por el usuario.",
            provider="Studio",
            auth="none",
            availability="available",
            operations=(_operation("read", "Leer documento"), _operation("search", "Buscar texto")),
            keywords=("documento", "pdf", "docx", "contrato", "manual", "informe"),
        ),
        PluginManifest(
            id="google.drive",
            version="1.0.0",
            title="Google Drive",
            description="Consulta archivos autorizados de Google Drive.",
            provider="Google",
            auth="oauth",
            availability="connection_required",
            permissions=("https://www.googleapis.com/auth/drive.readonly",),
            operations=(
                _operation("search_files", "Buscar archivos"),
                _operation("read_file", "Leer archivo"),
            ),
            keywords=("google drive", "drive", "documentos privados"),
        ),
        PluginManifest(
            id="github",
            version="1.0.0",
            title="GitHub",
            description="Consulta repositorios y propone cambios sujetos a aprobación.",
            provider="GitHub",
            auth="oauth",
            availability="connection_required",
            permissions=("contents:read",),
            operations=(
                _operation("read_repository", "Leer repositorio"),
                _operation(
                    "propose_change", "Proponer cambio", mode="write", risk="high", approval=True
                ),
            ),
            keywords=("github", "pull request", "repositorio remoto"),
        ),
        PluginManifest(
            id="google.gmail",
            version="1.0.0",
            title="Gmail",
            description="Busca y lee correo con una conexión individual de solo lectura.",
            provider="Google",
            auth="oauth",
            availability="connection_required",
            permissions=("https://www.googleapis.com/auth/gmail.readonly",),
            operations=(
                _operation("read_messages", "Leer mensajes", risk="medium"),
            ),
            keywords=("gmail", "correo", "email", "enviar mensaje"),
        ),
        PluginManifest(
            id="google.calendar",
            version="1.0.0",
            title="Google Calendar",
            description="Consulta próximos eventos con una conexión individual de solo lectura.",
            provider="Google",
            auth="oauth",
            availability="connection_required",
            permissions=("https://www.googleapis.com/auth/calendar.readonly",),
            operations=(
                _operation("list_events", "Consultar eventos"),
            ),
            keywords=("calendar", "calendario", "reunión", "agenda", "evento"),
        ),
    )
