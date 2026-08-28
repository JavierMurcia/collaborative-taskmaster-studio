"""Multi-user Firestore catalog for durable generated agents."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from studio.application.agent_catalog import AgentIcon, CatalogAgent, _suggest_icon
from studio.application.plugin_registry import PluginSelection
from studio.domain.errors import DomainError

_COLLECTION = "agent_catalog"


def _owner_key(owner_session_id: str) -> str:
    return hashlib.sha256(owner_session_id.encode("utf-8")).hexdigest()


def _agent_id(owner_session_id: str, build_id: str) -> str:
    digest = hashlib.sha256(f"{_owner_key(owner_session_id)}:{build_id}".encode()).hexdigest()
    return f"catalog_{digest[:24]}"


class FirestoreAgentCatalog:
    """Catalog whose records are owner-scoped and idempotent by build."""

    def __init__(self, client: Any) -> None:
        self._client = client

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
        identifier = _agent_id(owner_session_id, build_id)
        document = self._client.collection(_COLLECTION).document(identifier)
        try:
            snapshot = document.get()
            if snapshot.exists:
                return self._decode(snapshot.to_dict(), owner_session_id)
            now = datetime.now(UTC)
            agent = CatalogAgent(
                id=identifier,
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
            document.create(_payload(agent))
            return agent
        except DomainError:
            raise
        except Exception as error:
            raise DomainError("AGENT_CATALOG_UNAVAILABLE", "Firestore no pudo guardar el agente.") from error

    def list(
        self, owner_session_id: str, *, include_archived: bool = False
    ) -> tuple[CatalogAgent, ...]:
        owner_hash = _owner_key(owner_session_id)
        try:
            query = self._client.collection(_COLLECTION).where("owner_hash", "==", owner_hash)
            agents = [
                self._decode(snapshot.to_dict(), owner_session_id)
                for snapshot in query.stream()
            ]
        except DomainError:
            raise
        except Exception as error:
            raise DomainError("AGENT_CATALOG_UNAVAILABLE", "Firestore no pudo consultar el catálogo.") from error
        return tuple(
            sorted(
                (item for item in agents if include_archived or item.status == "ready"),
                key=lambda item: item.updated_at,
                reverse=True,
            )[:100]
        )

    def get(self, agent_id: str, owner_session_id: str) -> CatalogAgent:
        try:
            snapshot = self._client.collection(_COLLECTION).document(agent_id).get()
            if not snapshot.exists:
                raise self._not_found()
            return self._decode(snapshot.to_dict(), owner_session_id)
        except DomainError:
            raise
        except Exception as error:
            raise DomainError("AGENT_CATALOG_UNAVAILABLE", "Firestore no pudo consultar el agente.") from error

    def update(
        self,
        agent_id: str,
        owner_session_id: str,
        *,
        name: str | None = None,
        icon: AgentIcon | None = None,
    ) -> CatalogAgent:
        current = self.get(agent_id, owner_session_id)
        updated = current.model_copy(
            update={
                "name": name or current.name,
                "icon": icon or current.icon,
                "updated_at": datetime.now(UTC),
            }
        )
        try:
            self._client.collection(_COLLECTION).document(agent_id).set(_payload(updated))
        except Exception as error:
            raise DomainError("AGENT_CATALOG_UNAVAILABLE", "Firestore no pudo actualizar el agente.") from error
        return updated

    def archive(self, agent_id: str, owner_session_id: str) -> None:
        current = self.get(agent_id, owner_session_id)
        archived = current.model_copy(
            update={"status": "archived", "updated_at": datetime.now(UTC)}
        )
        try:
            self._client.collection(_COLLECTION).document(agent_id).set(_payload(archived))
        except Exception as error:
            raise DomainError("AGENT_CATALOG_UNAVAILABLE", "Firestore no pudo archivar el agente.") from error

    def _decode(self, payload: dict[str, Any] | None, owner_session_id: str) -> CatalogAgent:
        if not payload or payload.get("owner_hash") != _owner_key(owner_session_id):
            raise self._not_found()
        try:
            return CatalogAgent.model_validate(payload.get("agent", {}))
        except ValueError as error:
            raise DomainError("AGENT_CATALOG_INVALID", "El catálogo durable contiene un registro inválido.") from error

    @staticmethod
    def _not_found() -> DomainError:
        return DomainError("CATALOG_AGENT_NOT_FOUND", "No existe un agente accesible con ese identificador.")


def _payload(agent: CatalogAgent) -> dict[str, object]:
    return {
        "owner_hash": _owner_key(agent.owner_session_id),
        "status": agent.status,
        "updated_at": agent.updated_at,
        "agent": agent.model_dump(mode="json"),
    }
