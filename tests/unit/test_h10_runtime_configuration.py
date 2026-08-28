"""H10-07 safe environment and zero-secret runtime configuration."""

from __future__ import annotations

import json
import subprocess
from collections.abc import Sequence
from pathlib import Path

import pytest

from infrastructure.cloud_run.configuration import (
    load_runtime_configuration,
    plan_runtime_configuration,
    scan_repository_configuration,
    verify_runtime_configuration,
)
from infrastructure.cloud_run.configuration_check import main

PROJECT_ID = "collaborative-taskmaster-dev"
RUNTIME_EMAIL = f"taskmaster-studio-runtime@{PROJECT_ID}.iam.gserviceaccount.com"


class FakeGcloud:
    def __init__(self, service: dict[str, object] | None = None) -> None:
        self.service = service or _service()
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
            call, 0, stdout=json.dumps(self.service), stderr=""
        )


def _service(
    *,
    environment: dict[str, str] | None = None,
    runtime_email: str = RUNTIME_EMAIL,
    containers: int = 1,
) -> dict[str, object]:
    values = environment or load_runtime_configuration().rendered_environment(PROJECT_ID)
    definition = load_runtime_configuration()
    environment_items = [{"name": name, "value": value} for name, value in values.items()]
    environment_items.extend(
        {
            "name": secret.environment_variable,
            "valueFrom": {"secretKeyRef": {"name": secret.secret_id, "key": secret.version}},
        }
        for secret in definition.secrets
    )
    container = {"env": environment_items}
    return {
        "spec": {
            "template": {
                "spec": {
                    "serviceAccountName": runtime_email,
                    "containers": [container for _ in range(containers)],
                }
            }
        }
    }


def test_definition_declares_production_identity_and_oauth_secrets() -> None:
    definition = load_runtime_configuration()
    environment = definition.rendered_environment(PROJECT_ID)

    assert len(environment) == 31
    assert environment["STUDIO_ENABLE_CLOUD_STORAGE"] == "true"
    assert environment["STUDIO_PROJECTS_BUCKET"] == f"{PROJECT_ID}-taskmaster-projects"
    assert environment["STUDIO_PROJECTS_ROOT"] == "/tmp/projects"
    assert environment["GOOGLE_CLOUD_PROJECT"] == PROJECT_ID
    assert environment["GOOGLE_GENAI_USE_VERTEXAI"] == "true"
    assert environment["STUDIO_ENABLE_VERTEX"] == "true"
    assert environment["STUDIO_ENABLE_FIRESTORE"] == "true"
    assert environment["STUDIO_GEMINI_MODEL"] == "gemini-3.7-flash"
    assert {item.environment_variable for item in definition.secrets} == {
        "STUDIO_FIREBASE_API_KEY",
        "STUDIO_FIREBASE_APP_ID",
        "STUDIO_GOOGLE_OAUTH_CLIENT_ID",
        "STUDIO_GOOGLE_OAUTH_CLIENT_SECRET",
        "STUDIO_GITHUB_OAUTH_CLIENT_ID",
        "STUDIO_GITHUB_OAUTH_CLIENT_SECRET",
        "STUDIO_OAUTH_STATE_SECRET",
        "STUDIO_OAUTH_ENCRYPTION_KEY",
    }
    assert definition.runtime_secret_accessor_required is True


def test_secret_policy_forbids_plaintext_latest_and_downloaded_credentials() -> None:
    definition = load_runtime_configuration()

    assert definition.secret_policy.provider == "google_secret_manager"
    assert definition.secret_policy.allow_plaintext_values is False
    assert definition.secret_policy.require_numeric_version is True
    assert definition.secret_policy.latest_alias_allowed is False
    assert "GOOGLE_APPLICATION_CREDENTIALS" in definition.forbidden_environment_variables
    assert "GOOGLE_API_KEY" in definition.forbidden_environment_variables
    assert "GEMINI_API_KEY" in definition.forbidden_environment_variables
    assert "PORT" in definition.cloud_run_reserved_variables


def test_repository_configuration_scan_passes_without_secret_values() -> None:
    scan_repository_configuration(load_runtime_configuration())


def test_plan_is_offline_and_contains_only_secret_references() -> None:
    result = plan_runtime_configuration(PROJECT_ID)
    serialized = json.dumps(result.as_dict())

    assert result.status == "planned"
    assert result.repository_scan_verified is True
    assert result.cloud_verified is False
    assert result.configuration_applied is False
    assert result.secret_payloads_created is False
    assert any(argument.startswith("--set-secrets") for argument in result.deployment_arguments)
    assert "GOOGLE_APPLICATION_CREDENTIALS" not in serialized
    assert "GEMINI_API_KEY" not in serialized
    assert "secret_payloads_created" in serialized


def test_plan_uses_runtime_identity_and_non_secret_environment() -> None:
    result = plan_runtime_configuration(PROJECT_ID)

    assert f"--service-account={RUNTIME_EMAIL}" in result.deployment_arguments
    env_argument = next(
        argument for argument in result.deployment_arguments if argument.startswith("--set-env-vars")
    )
    assert f"GOOGLE_CLOUD_PROJECT={PROJECT_ID}" in env_argument
    assert "STUDIO_ENABLE_VERTEX=true" in env_argument
    assert "STUDIO_ENABLE_FIRESTORE=true" in env_argument
    assert "PORT=" not in env_argument


def test_offline_cli_emits_machine_readable_plan(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["--project", PROJECT_ID]) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "planned"
    assert len(payload["secret_references"]) == 8
    assert payload["cloud_verified"] is False
    assert payload["configuration_applied"] is False


def test_verify_accepts_exact_cloud_run_configuration() -> None:
    fake = FakeGcloud()
    result = verify_runtime_configuration(PROJECT_ID, runner=fake)

    assert result.status == "verified"
    assert result.cloud_verified is True
    assert result.configuration_applied is False
    assert len(fake.calls) == 1
    assert fake.calls[0][1:4] == ("run", "services", "describe")


def test_verify_rejects_wrong_runtime_identity() -> None:
    fake = FakeGcloud(_service(runtime_email="other@example.iam.gserviceaccount.com"))
    with pytest.raises(RuntimeError, match="identidad runtime"):
        verify_runtime_configuration(PROJECT_ID, runner=fake)


def test_verify_rejects_multiple_containers() -> None:
    fake = FakeGcloud(_service(containers=2))
    with pytest.raises(RuntimeError, match="exactamente un contenedor"):
        verify_runtime_configuration(PROJECT_ID, runner=fake)


def test_verify_rejects_missing_or_unexpected_environment() -> None:
    environment = load_runtime_configuration().rendered_environment(PROJECT_ID)
    environment.pop("STUDIO_ENV")
    environment["UNDECLARED_VALUE"] = "unsafe"

    with pytest.raises(RuntimeError, match="no coinciden exactamente"):
        verify_runtime_configuration(
            PROJECT_ID, runner=FakeGcloud(_service(environment=environment))
        )


@pytest.mark.parametrize(
    "name",
    ["GOOGLE_APPLICATION_CREDENTIALS", "GOOGLE_API_KEY", "GEMINI_API_KEY", "PORT"],
)
def test_verify_rejects_forbidden_or_reserved_environment(name: str) -> None:
    environment = load_runtime_configuration().rendered_environment(PROJECT_ID)
    environment[name] = "redacted"

    with pytest.raises(RuntimeError, match="variables prohibidas"):
        verify_runtime_configuration(
            PROJECT_ID, runner=FakeGcloud(_service(environment=environment))
        )


def test_repository_scan_rejects_a_nonempty_api_key(tmp_path: Path) -> None:
    (tmp_path / ".env.example").write_text("GEMINI_API_KEY=redacted\n", encoding="utf-8")
    (tmp_path / "cloudbuild.yaml").write_text("steps: []\n", encoding="utf-8")

    with pytest.raises(RuntimeError, match="credencial prohibida"):
        scan_repository_configuration(load_runtime_configuration(), root=tmp_path)


def test_latest_secret_version_is_rejected(tmp_path: Path) -> None:
    payload = json.loads(
        Path("infrastructure/cloud_run/runtime-config.json").read_text(encoding="utf-8")
    )
    payload["secrets"] = [
        {
            "environment_variable": "EXTERNAL_SERVICE_TOKEN",
            "secret_id": "external-service-token",
            "version": "latest",
            "purpose": "Authenticate a future external service.",
        }
    ]
    payload["runtime_secret_accessor_required"] = True
    declaration = tmp_path / "runtime-config.json"
    declaration.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match="referencia de secreto"):
        load_runtime_configuration(declaration)


@pytest.mark.parametrize("project_id", ["", "INVALID", "short", "-invalid"])
def test_invalid_project_is_rejected_before_cloud_calls(project_id: str) -> None:
    fake = FakeGcloud()
    with pytest.raises(ValueError, match="proyecto"):
        verify_runtime_configuration(project_id, runner=fake)
    assert fake.calls == []
