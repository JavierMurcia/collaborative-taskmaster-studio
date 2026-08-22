"""H10-05 exact least-privilege IAM declaration and verification."""

from __future__ import annotations

import json
import subprocess
from collections.abc import Sequence

import pytest

from infrastructure.cloud_run.iam import (
    binding_commands,
    load_iam_definition,
    plan_runtime_iam,
    verify_runtime_iam,
)
from infrastructure.cloud_run.iam_check import main

PROJECT_ID = "collaborative-taskmaster-dev"
EMAIL = "taskmaster-studio-runtime@collaborative-taskmaster-dev.iam.gserviceaccount.com"
MEMBER = f"serviceAccount:{EMAIL}"


def _condition(**updates: str) -> dict[str, str]:
    condition = {
        "title": "firestore_collaborative_taskmaster_only",
        "description": (
            "Restrict runtime data access to the Collaborative Taskmaster database."
        ),
        "expression": (
            'resource.name == "projects/collaborative-taskmaster-dev/'
            'databases/collaborative-taskmaster"'
        ),
    }
    condition.update(updates)
    return condition


def _secret_condition() -> dict[str, str]:
    definition = load_iam_definition()
    binding = next(
        item for item in definition.bindings
        if item.role == "roles/secretmanager.secretAccessor"
    )
    assert binding.condition is not None
    return binding.condition.render(PROJECT_ID)


def _policy(*bindings: dict[str, object]) -> dict[str, object]:
    default = (
        {"role": "roles/aiplatform.user", "members": [MEMBER]},
        {
            "role": "roles/datastore.user",
            "members": [MEMBER],
            "condition": _condition(),
        },
        {
            "role": "roles/secretmanager.secretAccessor",
            "members": [MEMBER],
            "condition": _secret_condition(),
        },
        {"role": "roles/viewer", "members": ["user:someone@example.com"]},
    )
    return {"bindings": list(bindings or default), "version": 3}


class FakeGcloud:
    def __init__(self, policy: dict[str, object]) -> None:
        self.policy = policy
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
        return subprocess.CompletedProcess(
            call,
            0,
            stdout=json.dumps(self.policy),
            stderr="",
        )


def test_definition_contains_only_runtime_roles_with_scoped_data_access() -> None:
    definition = load_iam_definition()

    assert {binding.role for binding in definition.bindings} == {
        "roles/aiplatform.user",
        "roles/datastore.user",
        "roles/secretmanager.secretAccessor",
    }
    assert definition.exact_project_roles is True
    firestore = next(
        binding for binding in definition.bindings if binding.role == "roles/datastore.user"
    )
    assert firestore.condition is not None
    assert firestore.condition.render(PROJECT_ID)["expression"] == _condition()["expression"]
    assert "roles/owner" in definition.forbidden_roles
    assert "roles/editor" in definition.forbidden_roles


def test_plan_is_offline_and_never_marks_bindings_as_applied() -> None:
    result = plan_runtime_iam(PROJECT_ID)

    assert result.status == "planned"
    assert result.member == MEMBER
    assert result.cloud_verified is False
    assert result.bindings_applied is False
    assert len(result.binding_commands) == 3


def test_binding_commands_are_explicit_and_contain_no_admin_roles() -> None:
    definition = load_iam_definition()
    commands = binding_commands(PROJECT_ID, definition)
    serialized = " ".join(part for command in commands for part in command)

    assert all(command[:3] == ("gcloud", "projects", "add-iam-policy-binding") for command in commands)
    assert all(f"--member={MEMBER}" in command for command in commands)
    assert "roles/aiplatform.user" in serialized
    assert "roles/datastore.user" in serialized
    assert "roles/secretmanager.secretAccessor" in serialized
    assert serialized.count(f"projects/{PROJECT_ID}/secrets/studio-") == 6
    assert "databases/collaborative-taskmaster" in serialized
    assert "roles/owner" not in serialized
    assert "roles/editor" not in serialized


def test_offline_cli_emits_machine_readable_plan(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["--project", PROJECT_ID]) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "planned"
    assert payload["cloud_verified"] is False
    assert payload["bindings_applied"] is False
    assert len(payload["binding_commands"]) == 3


def test_verify_accepts_exact_roles_and_ignores_other_principals() -> None:
    fake = FakeGcloud(_policy())
    result = verify_runtime_iam(PROJECT_ID, runner=fake)

    assert result.status == "verified"
    assert result.cloud_verified is True
    assert result.bindings_applied is True
    assert fake.calls == [result.verify_command]


def test_verify_rejects_a_missing_role() -> None:
    fake = FakeGcloud(
        _policy({"role": "roles/aiplatform.user", "members": [MEMBER]})
    )

    with pytest.raises(RuntimeError, match="missing"):
        verify_runtime_iam(PROJECT_ID, runner=fake)


def test_verify_rejects_an_unexpected_or_forbidden_role() -> None:
    policy = _policy()
    bindings = list(policy["bindings"])
    bindings.append({"role": "roles/editor", "members": [MEMBER]})
    fake = FakeGcloud({"bindings": bindings, "version": 3})

    with pytest.raises(RuntimeError, match="forbidden"):
        verify_runtime_iam(PROJECT_ID, runner=fake)


def test_verify_rejects_condition_drift() -> None:
    fake = FakeGcloud(
        _policy(
            {"role": "roles/aiplatform.user", "members": [MEMBER]},
            {
                "role": "roles/datastore.user",
                "members": [MEMBER],
                "condition": _condition(expression="resource.name.startsWith('projects/')"),
            },
            {
                "role": "roles/secretmanager.secretAccessor",
                "members": [MEMBER],
                "condition": _secret_condition(),
            },
        )
    )

    with pytest.raises(RuntimeError, match="mismatched"):
        verify_runtime_iam(PROJECT_ID, runner=fake)


@pytest.mark.parametrize("project_id", ["", "INVALID", "short", "-invalid"])
def test_invalid_project_is_rejected_before_gcloud(project_id: str) -> None:
    fake = FakeGcloud(_policy())

    with pytest.raises(ValueError, match="proyecto"):
        verify_runtime_iam(project_id, runner=fake)

    assert fake.calls == []
