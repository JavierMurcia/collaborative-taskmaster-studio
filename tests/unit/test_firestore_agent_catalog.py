from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from infrastructure.firestore.agent_catalog import FirestoreAgentCatalog
from studio.domain.errors import DomainError


class Snapshot:
    def __init__(self, payload: dict[str, Any] | None) -> None:
        self._payload = payload
        self.exists = payload is not None

    def to_dict(self) -> dict[str, Any] | None:
        return self._payload


class Document:
    def __init__(self, records: dict[str, dict[str, Any]], identifier: str) -> None:
        self._records = records
        self._identifier = identifier

    def get(self) -> Snapshot:
        return Snapshot(self._records.get(self._identifier))

    def create(self, payload: dict[str, Any]) -> None:
        if self._identifier in self._records:
            raise RuntimeError("AlreadyExists")
        self._records[self._identifier] = payload

    def set(self, payload: dict[str, Any]) -> None:
        self._records[self._identifier] = payload


class Query:
    def __init__(self, records: dict[str, dict[str, Any]], field: str, value: object) -> None:
        self._records = records
        self._field = field
        self._value = value

    def stream(self) -> list[Snapshot]:
        return [Snapshot(item) for item in self._records.values() if item.get(self._field) == self._value]


class Collection:
    def __init__(self, records: dict[str, dict[str, Any]]) -> None:
        self._records = records

    def document(self, identifier: str) -> Document:
        return Document(self._records, identifier)

    def where(self, field: str, operator: str, value: object) -> Query:
        assert operator == "=="
        return Query(self._records, field, value)


class Client:
    def __init__(self) -> None:
        self.records: dict[str, dict[str, Any]] = {}

    def collection(self, name: str) -> Collection:
        assert name == "agent_catalog"
        return Collection(self.records)


def test_firestore_catalog_is_idempotent_and_owner_scoped(tmp_path: Path) -> None:
    client = Client()
    catalog = FirestoreAgentCatalog(client)
    values = dict(
        build_id="build_12345678",
        project_id="agent_12345678",
        owner_session_id="owner_one",
        name="Research Agent",
        purpose="Investigar fuentes verificadas",
        framework="google_adk",
        framework_label="Google ADK",
        builder_runtime="controlled_adk",
        contract_digest="a" * 64,
        plugins=(),
        artifact_directory=tmp_path / "research-agent",
        artifact_uri="gs://studio-projects-test/prefix",
        artifact_digest="b" * 64,
        artifact_file_count=4,
        artifact_total_bytes=1200,
    )

    first = catalog.register(**values)
    second = catalog.register(**values)

    assert first == second
    assert catalog.list("owner_one") == (first,)
    assert catalog.list("owner_two") == ()
    assert first.artifact_uri == "gs://studio-projects-test/prefix"


def test_firestore_catalog_rejects_cross_owner_access(tmp_path: Path) -> None:
    catalog = FirestoreAgentCatalog(Client())
    agent = catalog.register(
        build_id="build_12345678",
        project_id="agent_12345678",
        owner_session_id="owner_one",
        name="Agent",
        purpose="Automatizar un proceso",
        framework="google_adk",
        framework_label="Google ADK",
        builder_runtime="controlled_adk",
        contract_digest="a" * 64,
        plugins=(),
        artifact_directory=tmp_path / "agent",
    )

    with pytest.raises(DomainError, match="No existe"):
        catalog.get(agent.id, "owner_two")


def test_firestore_catalog_updates_and_archives(tmp_path: Path) -> None:
    catalog = FirestoreAgentCatalog(Client())
    agent = catalog.register(
        build_id="build_12345678",
        project_id="agent_12345678",
        owner_session_id="owner_one",
        name="Agent",
        purpose="Automatizar un proceso",
        framework="google_adk",
        framework_label="Google ADK",
        builder_runtime="controlled_adk",
        contract_digest="a" * 64,
        plugins=(),
        artifact_directory=tmp_path / "agent",
    )

    updated = catalog.update(agent.id, "owner_one", name="Agent V2", icon="workflow")
    assert updated.name == "Agent V2"
    catalog.archive(agent.id, "owner_one")
    assert catalog.list("owner_one") == ()
    assert catalog.list("owner_one", include_archived=True)[0].status == "archived"

