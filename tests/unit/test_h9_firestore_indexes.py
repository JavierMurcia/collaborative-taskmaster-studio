from __future__ import annotations

import json
from pathlib import Path

import pytest

from infrastructure.firestore.indexes import (
    INDEX_MANIFEST,
    IndexField,
    QueryIndexRequirement,
    main,
    verify_index_manifest,
)
from studio.domain.errors import DomainError


def write_manifest(path: Path, payload: object) -> Path:
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def composite_requirement() -> QueryIndexRequirement:
    return QueryIndexRequirement(
        query_id="projects_by_owner_and_status",
        collection_group="projects",
        query_scope="COLLECTION",
        fields=(
            IndexField("owner_session_id", "ASCENDING"),
            IndexField("status", "ASCENDING"),
        ),
    )


def composite_index() -> dict[str, object]:
    return {
        "collectionGroup": "projects",
        "queryScope": "COLLECTION",
        "fields": [
            {"fieldPath": "owner_session_id", "order": "ASCENDING"},
            {"fieldPath": "status", "order": "ASCENDING"},
        ],
    }


def test_versioned_manifest_covers_all_current_queries_without_composites() -> None:
    result = verify_index_manifest()

    assert INDEX_MANIFEST.is_file()
    assert result.as_dict() == {
        "status": "ready",
        "manifest": "indexes.json",
        "required_queries": 1,
        "automatic_single_field_indexes": 1,
        "composite_indexes": 0,
        "cloud_applied": False,
    }


def test_compound_query_requires_exact_composite_index(tmp_path: Path) -> None:
    empty = write_manifest(
        tmp_path / "indexes.json", {"indexes": [], "fieldOverrides": []}
    )

    with pytest.raises(DomainError) as captured:
        verify_index_manifest(empty, requirements=(composite_requirement(),))

    assert captured.value.code == "FIRESTORE_INDEX_MISSING"


def test_exact_composite_index_covers_compound_query(tmp_path: Path) -> None:
    manifest = write_manifest(
        tmp_path / "indexes.json",
        {"indexes": [composite_index()], "fieldOverrides": []},
    )

    result = verify_index_manifest(
        manifest, requirements=(composite_requirement(),)
    )

    assert result.composite_indexes == 1
    assert result.automatic_single_field_indexes == 0


def test_duplicate_and_unnecessary_indexes_fail_closed(tmp_path: Path) -> None:
    duplicate = write_manifest(
        tmp_path / "duplicate.json",
        {
            "indexes": [composite_index(), composite_index()],
            "fieldOverrides": [],
        },
    )
    unnecessary = write_manifest(
        tmp_path / "unnecessary.json",
        {"indexes": [composite_index()], "fieldOverrides": []},
    )

    with pytest.raises(DomainError) as duplicate_error:
        verify_index_manifest(duplicate, requirements=(composite_requirement(),))
    with pytest.raises(DomainError) as unnecessary_error:
        verify_index_manifest(unnecessary)

    assert duplicate_error.value.code == "FIRESTORE_INDEX_DUPLICATE"
    assert unnecessary_error.value.code == "FIRESTORE_INDEX_UNNECESSARY"


def test_disabling_required_single_field_index_is_detected(tmp_path: Path) -> None:
    manifest = write_manifest(
        tmp_path / "indexes.json",
        {
            "indexes": [],
            "fieldOverrides": [
                {
                    "collectionGroup": "events",
                    "fieldPath": "sequence",
                    "indexes": [],
                }
            ],
        },
    )

    with pytest.raises(DomainError) as captured:
        verify_index_manifest(manifest)

    assert captured.value.code == "FIRESTORE_INDEX_MISSING"


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"indexes": "invalid", "fieldOverrides": []},
        {"indexes": [], "fieldOverrides": "invalid"},
    ],
)
def test_malformed_manifest_is_rejected(tmp_path: Path, payload: object) -> None:
    manifest = write_manifest(tmp_path / "indexes.json", payload)

    with pytest.raises(DomainError) as captured:
        verify_index_manifest(manifest)

    assert captured.value.code == "FIRESTORE_INDEX_MANIFEST_INVALID"


def test_index_check_command_is_offline_and_machine_readable(
    capsys: pytest.CaptureFixture[str],
) -> None:
    main()

    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "ready"
    assert payload["cloud_applied"] is False
