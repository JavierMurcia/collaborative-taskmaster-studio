"""Fixed demo-session retention shared by every Firestore aggregate document."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Literal

from infrastructure.firestore.indexes import INDEX_MANIFEST
from studio.domain.errors import DomainError

TTL_FIELD = "expires_at"
TTL_COLLECTION_GROUPS = frozenset(
    {"projects", "briefings", "revisions", "approvals", "events", "artifacts"}
)


@dataclass(frozen=True, slots=True)
class DemoRetentionPolicy:
    retention_days: int = 7

    def __post_init__(self) -> None:
        if not 1 <= self.retention_days <= 30:
            raise DomainError(
                "FIRESTORE_RETENTION_DAYS_INVALID",
                "La retención de demostración debe estar entre 1 y 30 días.",
                context={"min": 1, "max": 30},
            )

    def expires_at(self, created_at: datetime) -> datetime:
        if created_at.tzinfo is None or created_at.utcoffset() is None:
            raise DomainError(
                "FIRESTORE_RETENTION_TIMESTAMP_INVALID",
                "La retención requiere una fecha con zona horaria.",
            )
        return created_at + timedelta(days=self.retention_days)


@dataclass(frozen=True, slots=True)
class RetentionVerification:
    status: Literal["ready"]
    field: str
    retention_days: int
    collection_groups: tuple[str, ...]
    deletion_window: str = "typically_within_24_hours"
    cascade_assumed: bool = False
    cloud_applied: bool = False

    def as_dict(self) -> dict[str, object]:
        return {
            "status": self.status,
            "field": self.field,
            "retention_days": self.retention_days,
            "collection_groups": list(self.collection_groups),
            "deletion_window": self.deletion_window,
            "cascade_assumed": self.cascade_assumed,
            "cloud_applied": self.cloud_applied,
        }


def verify_retention_manifest(
    policy: DemoRetentionPolicy,
    path: Path = INDEX_MANIFEST,
) -> RetentionVerification:
    """Require TTL plus index exemption on root and every subcollection group."""
    manifest = _load_manifest(path)
    raw_overrides = manifest.get("fieldOverrides")
    if not isinstance(raw_overrides, list):
        raise _invalid_manifest(path)
    ttl_groups: list[str] = []
    for override in raw_overrides:
        if not isinstance(override, dict):
            raise _invalid_manifest(path)
        if override.get("ttl") is not True:
            continue
        if (
            override.get("fieldPath") != TTL_FIELD
            or override.get("indexes") != []
            or not isinstance(override.get("collectionGroup"), str)
            or set(override) != {"collectionGroup", "fieldPath", "ttl", "indexes"}
        ):
            raise _invalid_manifest(path)
        ttl_groups.append(override["collectionGroup"])
    if len(ttl_groups) != len(set(ttl_groups)):
        raise DomainError(
            "FIRESTORE_RETENTION_POLICY_DUPLICATE",
            "La política TTL contiene grupos de colección duplicados.",
        )
    actual = set(ttl_groups)
    missing = TTL_COLLECTION_GROUPS - actual
    unexpected = actual - TTL_COLLECTION_GROUPS
    if missing or unexpected:
        raise DomainError(
            "FIRESTORE_RETENTION_POLICY_INCOMPLETE",
            "La política TTL no cubre exactamente el agregado de demostración.",
            context={"missing": sorted(missing), "unexpected": sorted(unexpected)},
        )
    return RetentionVerification(
        status="ready",
        field=TTL_FIELD,
        retention_days=policy.retention_days,
        collection_groups=tuple(sorted(actual)),
    )


def _load_manifest(path: Path) -> Mapping[str, Any]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise _invalid_manifest(path) from error
    if not isinstance(raw, dict):
        raise _invalid_manifest(path)
    return raw


def _invalid_manifest(path: Path) -> DomainError:
    return DomainError(
        "FIRESTORE_RETENTION_MANIFEST_INVALID",
        "La declaración TTL de Firestore no cumple el contrato de retención.",
        context={"manifest": path.name},
    )
