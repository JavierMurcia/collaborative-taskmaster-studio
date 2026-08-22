"""Offline Firestore index declaration and query coverage verification."""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from studio.domain.errors import DomainError

Direction = Literal["ASCENDING", "DESCENDING", "CONTAINS"]
QueryScope = Literal["COLLECTION", "COLLECTION_GROUP"]

INDEX_MANIFEST = Path(__file__).with_name("indexes.json")
EVENT_SEQUENCE_FIELD = "sequence"


@dataclass(frozen=True, slots=True)
class IndexField:
    field_path: str
    direction: Direction


@dataclass(frozen=True, slots=True)
class QueryIndexRequirement:
    query_id: str
    collection_group: str
    query_scope: QueryScope
    fields: tuple[IndexField, ...]


@dataclass(frozen=True, slots=True)
class IndexVerification:
    status: Literal["ready"]
    manifest: str
    required_queries: int
    automatic_single_field_indexes: int
    composite_indexes: int
    cloud_applied: bool = False

    def as_dict(self) -> dict[str, object]:
        return {
            "status": self.status,
            "manifest": self.manifest,
            "required_queries": self.required_queries,
            "automatic_single_field_indexes": self.automatic_single_field_indexes,
            "composite_indexes": self.composite_indexes,
            "cloud_applied": self.cloud_applied,
        }


PROJECT_QUERY_REQUIREMENTS = (
    QueryIndexRequirement(
        query_id="project_events_by_sequence",
        collection_group="events",
        query_scope="COLLECTION",
        fields=(IndexField(EVENT_SEQUENCE_FIELD, "ASCENDING"),),
    ),
)


def verify_index_manifest(
    path: Path = INDEX_MANIFEST,
    *,
    requirements: Sequence[QueryIndexRequirement] = PROJECT_QUERY_REQUIREMENTS,
) -> IndexVerification:
    """Validate the versioned manifest against every repository query, without RPCs."""
    manifest = _load_manifest(path)
    indexes = _index_entries(manifest)
    overrides = _field_overrides(manifest)
    signatures = tuple(_signature(index) for index in indexes)
    if len(signatures) != len(set(signatures)):
        raise DomainError(
            "FIRESTORE_INDEX_DUPLICATE",
            "La declaración contiene índices Firestore duplicados.",
        )

    required_composites = {
        _requirement_signature(requirement)
        for requirement in requirements
        if not _is_automatic(requirement, overrides)
    }
    declared_composites = set(signatures)
    missing = required_composites - declared_composites
    if missing:
        raise DomainError(
            "FIRESTORE_INDEX_MISSING",
            "Falta un índice Firestore requerido por una consulta del repositorio.",
            context={"count": len(missing)},
        )
    unnecessary = declared_composites - required_composites
    if unnecessary:
        raise DomainError(
            "FIRESTORE_INDEX_UNNECESSARY",
            "La declaración contiene un índice compuesto que ninguna consulta utiliza.",
            context={"count": len(unnecessary)},
        )

    automatic = sum(
        _is_automatic(requirement, overrides) for requirement in requirements
    )
    return IndexVerification(
        status="ready",
        manifest=path.name,
        required_queries=len(requirements),
        automatic_single_field_indexes=automatic,
        composite_indexes=len(indexes),
    )


def main() -> None:
    print(json.dumps(verify_index_manifest().as_dict(), sort_keys=True))


def _load_manifest(path: Path) -> Mapping[str, Any]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise DomainError(
            "FIRESTORE_INDEX_MANIFEST_INVALID",
            "La declaración de índices Firestore no pudo leerse.",
            context={"manifest": path.name},
        ) from error
    if not isinstance(raw, dict) or set(raw) != {"indexes", "fieldOverrides"}:
        raise DomainError(
            "FIRESTORE_INDEX_MANIFEST_INVALID",
            "La declaración de índices Firestore tiene una estructura inválida.",
            context={"manifest": path.name},
        )
    return raw


def _index_entries(manifest: Mapping[str, Any]) -> tuple[Mapping[str, Any], ...]:
    return _mapping_entries(manifest.get("indexes"), "indexes")


def _field_overrides(manifest: Mapping[str, Any]) -> tuple[Mapping[str, Any], ...]:
    return _mapping_entries(manifest.get("fieldOverrides"), "fieldOverrides")


def _mapping_entries(value: object, field: str) -> tuple[Mapping[str, Any], ...]:
    if not isinstance(value, list) or any(not isinstance(item, dict) for item in value):
        raise DomainError(
            "FIRESTORE_INDEX_MANIFEST_INVALID",
            f"{field} debe ser una lista de objetos.",
            context={"field": field},
        )
    return tuple(value)


def _signature(index: Mapping[str, Any]) -> tuple[object, ...]:
    collection_group = index.get("collectionGroup")
    query_scope = index.get("queryScope")
    fields = index.get("fields")
    if (
        not isinstance(collection_group, str)
        or query_scope not in {"COLLECTION", "COLLECTION_GROUP"}
        or not isinstance(fields, list)
        or len(fields) < 2
    ):
        raise _invalid_index()
    return (
        collection_group,
        query_scope,
        tuple(_field_signature(field) for field in fields),
    )


def _field_signature(field: object) -> tuple[str, str]:
    if not isinstance(field, dict) or not isinstance(field.get("fieldPath"), str):
        raise _invalid_index()
    direction_keys = set(field) & {"order", "arrayConfig"}
    if len(direction_keys) != 1:
        raise _invalid_index()
    key = direction_keys.pop()
    direction = field.get(key)
    allowed = {"ASCENDING", "DESCENDING"} if key == "order" else {"CONTAINS"}
    if direction not in allowed or set(field) != {"fieldPath", key}:
        raise _invalid_index()
    return field["fieldPath"], direction


def _requirement_signature(requirement: QueryIndexRequirement) -> tuple[object, ...]:
    return (
        requirement.collection_group,
        requirement.query_scope,
        tuple((field.field_path, field.direction) for field in requirement.fields),
    )


def _is_automatic(
    requirement: QueryIndexRequirement,
    overrides: Iterable[Mapping[str, Any]],
) -> bool:
    if requirement.query_scope != "COLLECTION" or len(requirement.fields) != 1:
        return False
    field = requirement.fields[0]
    if field.direction not in {"ASCENDING", "DESCENDING"}:
        return False
    for override in overrides:
        if (
            override.get("collectionGroup") == requirement.collection_group
            and override.get("fieldPath") == field.field_path
        ):
            indexes = override.get("indexes")
            if not isinstance(indexes, list):
                raise _invalid_index()
            return any(
                isinstance(index, dict)
                and index.get("queryScope") == "COLLECTION"
                and index.get("order") == field.direction
                for index in indexes
            )
    return True


def _invalid_index() -> DomainError:
    return DomainError(
        "FIRESTORE_INDEX_MANIFEST_INVALID",
        "Una definición de índice Firestore no cumple el contrato esperado.",
    )


if __name__ == "__main__":
    main()
