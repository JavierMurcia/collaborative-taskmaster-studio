"""Persistent catalog for agents that passed the controlled build laboratory."""

from __future__ import annotations

import builtins
import json
import threading
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field

from studio.application.plugin_registry import PluginSelection
from studio.domain.errors import DomainError

AgentIcon = Literal["spark", "workflow", "document", "research", "operations", "shield"]


class CatalogAgent(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str
    build_id: str
    project_id: str
    owner_session_id: str
    name: str = Field(min_length=1, max_length=100)
    purpose: str = Field(min_length=1, max_length=500)
    icon: AgentIcon = "spark"
    framework: str
    framework_label: str
    builder_runtime: str
    contract_digest: str
    plugins: tuple[PluginSelection, ...] = ()
    artifact_directory: str
    artifact_uri: str | None = None
    artifact_digest: str = ""
    artifact_file_count: int = Field(default=0, ge=0)
    artifact_total_bytes: int = Field(default=0, ge=0)
    version: int = Field(default=1, ge=1)
    status: Literal["ready", "archived"] = "ready"
    created_at: datetime
    updated_at: datetime


class AgentCatalogRepository(Protocol):
    def register(
        self,
        *,
        build_id: str,
        project_id: str,
        owner_session_id: str,
        name: str,
        purpose: str,
        framework: str,
        framework_label: str,
        builder_runtime: str,
        contract_digest: str,
        plugins: tuple[PluginSelection, ...],
        artifact_directory: Path,
        artifact_uri: str | None = None,
        artifact_digest: str = "",
        artifact_file_count: int = 0,
        artifact_total_bytes: int = 0,
    ) -> CatalogAgent: ...

    def list(
        self, owner_session_id: str, *, include_archived: bool = False
    ) -> tuple[CatalogAgent, ...]: ...

    def get(self, agent_id: str, owner_session_id: str) -> CatalogAgent: ...

    def update(
        self,
        agent_id: str,
        owner_session_id: str,
        *,
        name: str | None = None,
        icon: AgentIcon | None = None,
    ) -> CatalogAgent: ...

    def archive(self, agent_id: str, owner_session_id: str) -> None: ...


class AgentCatalog:
    """Small JSON-backed registry scoped by the Studio session owner."""

    def __init__(self, data_directory: Path) -> None:
        self._path = data_directory.resolve() / "agent-catalog.json"
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()

    def register(
        self,
        *,
        build_id: str,
        project_id: str,
        owner_session_id: str,
        name: str,
        purpose: str,
        framework: str,
        framework_label: str,
        builder_runtime: str,
        contract_digest: str,
        plugins: tuple[PluginSelection, ...],
        artifact_directory: Path,
        artifact_uri: str | None = None,
        artifact_digest: str = "",
        artifact_file_count: int = 0,
        artifact_total_bytes: int = 0,
    ) -> CatalogAgent:
        now = datetime.now(UTC)
        with self._lock:
            entries = self._load()
            existing = next((item for item in entries if item.build_id == build_id), None)
            if existing is not None:
                return existing
            entry = CatalogAgent(
                id=f"catalog_{uuid.uuid4().hex[:16]}",
                build_id=build_id,
                project_id=project_id,
                owner_session_id=owner_session_id,
                name=name,
                purpose=purpose,
                icon=_suggest_icon(purpose),
                framework=framework,
                framework_label=framework_label,
                builder_runtime=builder_runtime,
                contract_digest=contract_digest,
                plugins=plugins,
                artifact_directory=str(artifact_directory.resolve()),
                artifact_uri=artifact_uri,
                artifact_digest=artifact_digest,
                artifact_file_count=artifact_file_count,
                artifact_total_bytes=artifact_total_bytes,
                created_at=now,
                updated_at=now,
            )
            self._save([entry, *entries])
            return entry

    def list(
        self, owner_session_id: str, *, include_archived: bool = False
    ) -> tuple[CatalogAgent, ...]:
        with self._lock:
            return tuple(
                item
                for item in self._load()
                if item.owner_session_id == owner_session_id
                and (include_archived or item.status == "ready")
            )

    def get(self, agent_id: str, owner_session_id: str) -> CatalogAgent:
        with self._lock:
            item = next(
                (
                    entry
                    for entry in self._load()
                    if entry.id == agent_id and entry.owner_session_id == owner_session_id
                ),
                None,
            )
        if item is None:
            raise DomainError(
                "CATALOG_AGENT_NOT_FOUND", "No existe un agente accesible con ese identificador."
            )
        return item

    def update(
        self,
        agent_id: str,
        owner_session_id: str,
        *,
        name: str | None = None,
        icon: AgentIcon | None = None,
    ) -> CatalogAgent:
        with self._lock:
            entries = self._load()
            for index, entry in enumerate(entries):
                if entry.id == agent_id and entry.owner_session_id == owner_session_id:
                    updated = entry.model_copy(
                        update={
                            "name": name or entry.name,
                            "icon": icon or entry.icon,
                            "updated_at": datetime.now(UTC),
                        }
                    )
                    entries[index] = updated
                    self._save(entries)
                    return updated
        raise DomainError(
            "CATALOG_AGENT_NOT_FOUND", "No existe un agente accesible con ese identificador."
        )

    def archive(self, agent_id: str, owner_session_id: str) -> None:
        with self._lock:
            entries = self._load()
            for index, entry in enumerate(entries):
                if entry.id == agent_id and entry.owner_session_id == owner_session_id:
                    entries[index] = entry.model_copy(
                        update={"status": "archived", "updated_at": datetime.now(UTC)}
                    )
                    self._save(entries)
                    return
        raise DomainError(
            "CATALOG_AGENT_NOT_FOUND", "No existe un agente accesible con ese identificador."
        )

    def _load(self) -> builtins.list[CatalogAgent]:
        if not self._path.is_file():
            return []
        try:
            payload = json.loads(self._path.read_text(encoding="utf-8"))
            return [CatalogAgent.model_validate(item) for item in payload.get("agents", [])]
        except (OSError, ValueError, TypeError):
            raise DomainError(
                "AGENT_CATALOG_INVALID", "El catálogo local de agentes no es válido."
            ) from None

    def _save(self, entries: builtins.list[CatalogAgent]) -> None:
        temporary = self._path.with_suffix(".tmp")
        temporary.write_text(
            json.dumps(
                {
                    "schema_version": "1.0.0",
                    "agents": [item.model_dump(mode="json") for item in entries],
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
            newline="\n",
        )
        temporary.replace(self._path)


def _suggest_icon(purpose: str) -> AgentIcon:
    lowered = purpose.casefold()
    if any(token in lowered for token in ("document", "contrato", "informe", "redact")):
        return "document"
    if any(token in lowered for token in ("investig", "buscar", "research")):
        return "research"
    if any(token in lowered for token in ("seguridad", "cumplimiento", "riesgo")):
        return "shield"
    if any(token in lowered for token in ("operaci", "ticket", "soporte")):
        return "operations"
    if any(token in lowered for token in ("flujo", "proceso", "automat")):
        return "workflow"
    return "spark"
