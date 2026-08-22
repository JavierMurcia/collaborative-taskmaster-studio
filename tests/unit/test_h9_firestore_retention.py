from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from infrastructure.firestore.retention import (
    TTL_COLLECTION_GROUPS,
    DemoRetentionPolicy,
    verify_retention_manifest,
)
from infrastructure.firestore.retention_check import main
from studio.domain.errors import DomainError

NOW = datetime(2026, 8, 14, 15, 0, tzinfo=UTC)


def manifest_payload() -> dict[str, object]:
    return {
        "indexes": [],
        "fieldOverrides": [
            {
                "collectionGroup": group,
                "fieldPath": "expires_at",
                "ttl": True,
                "indexes": [],
            }
            for group in sorted(TTL_COLLECTION_GROUPS)
        ],
    }


def write_manifest(path: Path, payload: object) -> Path:
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_demo_policy_uses_fixed_aware_deadline() -> None:
    policy = DemoRetentionPolicy(retention_days=7)

    assert policy.expires_at(NOW) == NOW + timedelta(days=7)

    with pytest.raises(DomainError) as captured:
        policy.expires_at(datetime(2026, 8, 14, 15, 0))
    assert captured.value.code == "FIRESTORE_RETENTION_TIMESTAMP_INVALID"


@pytest.mark.parametrize("days", [0, 31])
def test_demo_policy_rejects_unbounded_retention(days: int) -> None:
    with pytest.raises(DomainError) as captured:
        DemoRetentionPolicy(retention_days=days)

    assert captured.value.code == "FIRESTORE_RETENTION_DAYS_INVALID"


def test_versioned_manifest_covers_root_and_every_subcollection() -> None:
    result = verify_retention_manifest(DemoRetentionPolicy(7))

    assert result.collection_groups == tuple(sorted(TTL_COLLECTION_GROUPS))
    assert result.cascade_assumed is False
    assert result.cloud_applied is False


def test_missing_and_duplicate_ttl_groups_fail_closed(tmp_path: Path) -> None:
    missing_payload = manifest_payload()
    overrides = missing_payload["fieldOverrides"]
    assert isinstance(overrides, list)
    overrides.pop()
    missing = write_manifest(tmp_path / "missing.json", missing_payload)

    duplicate_payload = manifest_payload()
    duplicate_overrides = duplicate_payload["fieldOverrides"]
    assert isinstance(duplicate_overrides, list)
    duplicate_overrides.append(dict(duplicate_overrides[0]))
    duplicate = write_manifest(tmp_path / "duplicate.json", duplicate_payload)

    with pytest.raises(DomainError) as missing_error:
        verify_retention_manifest(DemoRetentionPolicy(7), missing)
    with pytest.raises(DomainError) as duplicate_error:
        verify_retention_manifest(DemoRetentionPolicy(7), duplicate)

    assert missing_error.value.code == "FIRESTORE_RETENTION_POLICY_INCOMPLETE"
    assert duplicate_error.value.code == "FIRESTORE_RETENTION_POLICY_DUPLICATE"


def test_ttl_field_must_be_unindexed_and_exact(tmp_path: Path) -> None:
    payload = manifest_payload()
    overrides = payload["fieldOverrides"]
    assert isinstance(overrides, list)
    overrides[0]["indexes"] = [
        {"order": "ASCENDING", "queryScope": "COLLECTION"}
    ]
    manifest = write_manifest(tmp_path / "indexed-ttl.json", payload)

    with pytest.raises(DomainError) as captured:
        verify_retention_manifest(DemoRetentionPolicy(7), manifest)

    assert captured.value.code == "FIRESTORE_RETENTION_MANIFEST_INVALID"


def test_retention_check_is_offline_and_machine_readable(
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("STUDIO_FIRESTORE_DEMO_RETENTION_DAYS", "5")
    main()

    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "ready"
    assert payload["retention_days"] == 5
    assert payload["cloud_applied"] is False
    assert payload["cascade_assumed"] is False
