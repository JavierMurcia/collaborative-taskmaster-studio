"""H10-04 runtime service-account declaration and verification."""

from __future__ import annotations

import json
import subprocess
from collections.abc import Sequence

import pytest

from infrastructure.cloud_run.identity import (
    create_command,
    load_identity_definition,
    plan_runtime_identity,
    verification_commands,
    verify_runtime_identity,
)
from infrastructure.cloud_run.identity_check import main

PROJECT_ID = "collaborative-taskmaster-dev"


class FakeGcloud:
    def __init__(
        self,
        *,
        identity: dict[str, object] | None = None,
        keys: list[dict[str, object]] | None = None,
    ) -> None:
        self.identity = identity or _identity()
        self.keys = keys or []
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
        output = self.keys if "keys" in call else self.identity
        return subprocess.CompletedProcess(
            call,
            0,
            stdout=json.dumps(output),
            stderr="",
        )


def _identity(**updates: object) -> dict[str, object]:
    identity: dict[str, object] = {
        "email": (
            "taskmaster-studio-runtime@"
            "collaborative-taskmaster-dev.iam.gserviceaccount.com"
        ),
        "displayName": "Collaborative Taskmaster Studio Runtime",
        "description": "Runtime identity for Cloud Run; no user-managed keys.",
        "disabled": False,
    }
    identity.update(updates)
    return identity


def test_definition_names_a_dedicated_keyless_runtime_identity() -> None:
    definition = load_identity_definition()

    assert definition.account_id == "taskmaster-studio-runtime"
    assert definition.purpose == "cloud_run_runtime"
    assert definition.user_managed_keys_allowed is False
    assert definition.email_for(PROJECT_ID) == (
        "taskmaster-studio-runtime@"
        "collaborative-taskmaster-dev.iam.gserviceaccount.com"
    )


def test_plan_is_offline_and_contains_explicit_create_and_verify_commands() -> None:
    result = plan_runtime_identity(PROJECT_ID)

    assert result.status == "planned"
    assert result.cloud_verified is False
    assert result.roles_assigned is False
    assert result.create_command == create_command(PROJECT_ID, result.definition)
    assert result.verify_commands == verification_commands(PROJECT_ID, result.definition)
    assert "--managed-by=user" in result.verify_commands[1]


def test_offline_cli_emits_machine_readable_plan(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["--project", PROJECT_ID]) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "planned"
    assert payload["cloud_verified"] is False
    assert payload["roles_assigned"] is False
    assert payload["email"].startswith("taskmaster-studio-runtime@")


def test_create_command_does_not_create_keys_or_assign_roles() -> None:
    command = create_command(PROJECT_ID, load_identity_definition())

    assert command[:4] == ("gcloud", "iam", "service-accounts", "create")
    assert command[-1] == "--quiet"
    assert "keys" not in command
    assert "projects" not in command
    assert "add-iam-policy-binding" not in command


def test_verify_accepts_matching_enabled_identity_without_user_keys() -> None:
    fake = FakeGcloud()
    result = verify_runtime_identity(PROJECT_ID, runner=fake)

    assert result.status == "verified"
    assert result.cloud_verified is True
    assert result.roles_assigned is False
    assert len(fake.calls) == 2
    assert "describe" in fake.calls[0]
    assert "keys" in fake.calls[1]


@pytest.mark.parametrize(
    "identity",
    [
        _identity(displayName="Different runtime"),
        _identity(disabled=True),
        _identity(email="other@example.iam.gserviceaccount.com"),
    ],
)
def test_verify_rejects_identity_drift(identity: dict[str, object]) -> None:
    fake = FakeGcloud(identity=identity)

    with pytest.raises(RuntimeError, match="no coincide"):
        verify_runtime_identity(PROJECT_ID, runner=fake)

    assert len(fake.calls) == 1


def test_verify_rejects_user_managed_keys() -> None:
    fake = FakeGcloud(keys=[{"name": "projects/redacted/keys/redacted"}])

    with pytest.raises(RuntimeError, match="claves administradas"):
        verify_runtime_identity(PROJECT_ID, runner=fake)


@pytest.mark.parametrize("project_id", ["", "INVALID", "short", "-invalid"])
def test_project_id_is_rejected_before_any_cloud_call(project_id: str) -> None:
    fake = FakeGcloud()

    with pytest.raises(ValueError, match="proyecto"):
        verify_runtime_identity(project_id, runner=fake)

    assert fake.calls == []
