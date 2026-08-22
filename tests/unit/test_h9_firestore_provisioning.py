from __future__ import annotations

import json
import subprocess
from collections.abc import Sequence

import pytest

from infrastructure.firestore.provisioning import (
    create_command,
    load_database_definition,
    provision_database,
)


class FakeGcloud:
    def __init__(self, databases: list[dict[str, object]]) -> None:
        self.databases = databases
        self.calls: list[tuple[str, ...]] = []

    def __call__(
        self,
        command: Sequence[str],
        *,
        check: bool,
        capture_output: bool,
        text: bool,
    ) -> subprocess.CompletedProcess[str]:
        call = tuple(command)
        self.calls.append(call)
        assert check and capture_output and text
        if "list" in call:
            output = json.dumps(self.databases)
        elif "create" in call:
            output = ""
        elif "describe" in call:
            output = json.dumps(_database())
        else:
            output = ""
        return subprocess.CompletedProcess(call, 0, stdout=output, stderr="")


def _database(**updates: object) -> dict[str, object]:
    database: dict[str, object] = {
        "name": "projects/collaborative-taskmaster-dev/databases/collaborative-taskmaster",
        "locationId": "us-central1",
        "type": "FIRESTORE_NATIVE",
        "edition": "STANDARD",
        "concurrencyMode": "PESSIMISTIC",
        "deleteProtectionState": "DELETE_PROTECTION_ENABLED",
    }
    database.update(updates)
    return database


def test_database_definition_is_native_standard_and_protected() -> None:
    definition = load_database_definition()

    assert definition.database_id == "collaborative-taskmaster"
    assert definition.location == "us-central1"
    assert definition.type == "FIRESTORE_NATIVE"
    assert definition.edition == "STANDARD"
    assert definition.delete_protection == "DELETE_PROTECTION_ENABLED"


def test_plan_is_closed_by_default_and_never_runs_gcloud() -> None:
    fake = FakeGcloud([])
    result = provision_database("collaborative-taskmaster-dev", runner=fake)

    assert result.status == "planned"
    assert fake.calls == []
    assert "--delete-protection" in result.command


def test_create_command_is_explicit_and_quiet() -> None:
    command = create_command("collaborative-taskmaster-dev", load_database_definition())

    assert command[:4] == ("gcloud", "firestore", "databases", "create")
    assert "--type=firestore-native" in command
    assert "--edition=standard" in command
    assert "--concurrency-mode=pessimistic" in command
    assert command[-1] == "--quiet"


def test_apply_is_idempotent_when_database_already_matches() -> None:
    fake = FakeGcloud([_database()])
    result = provision_database(
        "collaborative-taskmaster-dev", apply=True, runner=fake
    )

    assert result.status == "existing"
    assert not any("create" in call for call in fake.calls)


def test_apply_creates_and_verifies_missing_database() -> None:
    fake = FakeGcloud([])
    result = provision_database(
        "collaborative-taskmaster-dev", apply=True, runner=fake
    )

    assert result.status == "created"
    assert any("create" in call for call in fake.calls)
    assert any("describe" in call for call in fake.calls)


def test_apply_rejects_existing_database_drift() -> None:
    fake = FakeGcloud([_database(locationId="nam5")])

    with pytest.raises(RuntimeError, match="locationId"):
        provision_database("collaborative-taskmaster-dev", apply=True, runner=fake)


@pytest.mark.parametrize("project_id", ["", "INVALID", "too_short", "-invalid"])
def test_project_id_is_validated_before_any_cloud_action(project_id: str) -> None:
    fake = FakeGcloud([])

    with pytest.raises(ValueError, match="proyecto"):
        provision_database(project_id, apply=True, runner=fake)

    assert fake.calls == []
