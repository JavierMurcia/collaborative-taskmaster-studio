"""H10-06 reproducible Artifact Registry and Cloud Build declaration."""

from __future__ import annotations

import json
import subprocess
from collections.abc import Sequence
from dataclasses import replace
from pathlib import Path

import pytest

from infrastructure.cloud_run.build import (
    _canonical_config_bytes,
    load_build_definition,
    plan_build_pipeline,
    verify_build_pipeline,
    verify_local_build_config,
)
from infrastructure.cloud_run.build_check import main

PROJECT_ID = "collaborative-taskmaster-dev"
IMAGE_TAG = "git-a1b2c3d"
BUILDER_EMAIL = f"taskmaster-studio-builder@{PROJECT_ID}.iam.gserviceaccount.com"
MEMBER = f"serviceAccount:{BUILDER_EMAIL}"


class FakeGcloud:
    def __init__(
        self,
        *,
        service_states: tuple[str, str] = ("ENABLED", "ENABLED"),
        builder_updates: dict[str, object] | None = None,
        keys: list[dict[str, object]] | None = None,
        repository_updates: dict[str, object] | None = None,
        repository_policy: dict[str, object] | None = None,
        project_policy: dict[str, object] | None = None,
        source_bucket_policy: dict[str, object] | None = None,
    ) -> None:
        self.service_states = service_states
        self.builder = _builder()
        self.builder.update(builder_updates or {})
        self.keys = keys or []
        self.repository = _repository()
        self.repository.update(repository_updates or {})
        self.repository_policy = repository_policy or _policy(
            "roles/artifactregistry.writer"
        )
        self.project_policy = project_policy or _policy("roles/logging.logWriter")
        self.source_bucket_policy = source_bucket_policy or _policy(
            "roles/storage.objectViewer"
        )
        self.calls: list[tuple[str, ...]] = []
        self.service_index = 0

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
        if call[1:3] == ("services", "list"):
            service = next(
                item.removeprefix("--filter=config.name=")
                for item in call
                if item.startswith("--filter=config.name=")
            )
            payload: object = [{
                "config": {"name": service},
                "state": self.service_states[self.service_index],
            }]
            self.service_index += 1
        elif "service-accounts" in call and "describe" in call:
            payload = self.builder
        elif "keys" in call:
            payload = self.keys
        elif "repositories" in call and "describe" in call:
            payload = self.repository
        elif "repositories" in call and "get-iam-policy" in call:
            payload = self.repository_policy
        elif "storage" in call and "get-iam-policy" in call:
            payload = self.source_bucket_policy
        else:
            payload = self.project_policy
        return subprocess.CompletedProcess(call, 0, stdout=json.dumps(payload), stderr="")


def _builder() -> dict[str, object]:
    return {
        "email": BUILDER_EMAIL,
        "displayName": "Collaborative Taskmaster Studio Builder",
        "description": "Cloud Build identity for tests and Artifact Registry publication.",
        "disabled": False,
    }


def _repository() -> dict[str, object]:
    return {
        "format": "DOCKER",
        "mode": "STANDARD_REPOSITORY",
        "description": "Immutable container images for Collaborative Taskmaster Studio.",
        "dockerConfig": {"immutableTags": True},
        "vulnerabilityScanningConfig": {"enablement": "DISABLED"},
    }


def _policy(role: str) -> dict[str, object]:
    return {"bindings": [{"role": role, "members": [MEMBER]}]}


def test_definition_is_regional_immutable_and_least_privilege() -> None:
    definition = load_build_definition()

    assert definition.region == "us-central1"
    assert definition.repository.repository_id == "collaborative-taskmaster"
    assert definition.repository.format == "DOCKER"
    assert definition.repository.immutable_tags is True
    assert definition.repository.vulnerability_scanning is False
    assert definition.builder_identity.user_managed_keys_allowed is False
    assert {(item.scope, item.role) for item in definition.builder_bindings} == {
        ("repository", "roles/artifactregistry.writer"),
        ("project", "roles/logging.logWriter"),
        ("source_bucket", "roles/storage.objectViewer"),
    }


def test_cloudbuild_recipe_matches_declared_digest_and_contract() -> None:
    verify_local_build_config(load_build_definition())


def test_cloudbuild_digest_is_stable_across_line_endings() -> None:
    linux = b"steps:\n  - id: unit-tests\n"
    windows = linux.replace(b"\n", b"\r\n")

    assert _canonical_config_bytes(windows) == linux


def test_plan_is_offline_traceable_and_does_not_submit() -> None:
    result = plan_build_pipeline(PROJECT_ID, IMAGE_TAG)

    assert result.status == "planned"
    assert result.local_config_verified is True
    assert result.cloud_verified is False
    assert result.resources_applied is False
    assert result.build_submitted is False
    assert result.image_uri.endswith(f"/studio:{IMAGE_TAG}")
    assert result.submit_command[1:3] == ("builds", "submit")
    assert f"--substitutions=_IMAGE_TAG={IMAGE_TAG}" in result.submit_command


def test_plan_declares_services_repository_builder_and_minimum_bindings() -> None:
    result = plan_build_pipeline(PROJECT_ID, IMAGE_TAG)
    serialized = json.dumps(result.as_dict())

    assert "artifactregistry.googleapis.com" in serialized
    assert "cloudbuild.googleapis.com" in serialized
    assert "--immutable-tags" in serialized
    assert "--disable-vulnerability-scanning" in serialized
    assert "roles/artifactregistry.writer" in serialized
    assert "roles/logging.logWriter" in serialized
    assert "roles/storage.objectViewer" in serialized
    assert "roles/owner" not in serialized
    assert "roles/editor" not in serialized


def test_offline_cli_emits_machine_readable_plan(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(
        ["--project", PROJECT_ID, "--image-tag", IMAGE_TAG]
    ) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "planned"
    assert payload["local_config_verified"] is True
    assert payload["cloud_verified"] is False
    assert payload["build_submitted"] is False


@pytest.mark.parametrize("tag", ["latest", "dev", "main", "bad tag", "tiny"])
def test_mobile_or_invalid_image_tags_are_rejected(tag: str) -> None:
    with pytest.raises(ValueError, match="etiqueta"):
        plan_build_pipeline(PROJECT_ID, tag)


def test_verify_accepts_matching_cloud_resources() -> None:
    fake = FakeGcloud()
    result = verify_build_pipeline(PROJECT_ID, IMAGE_TAG, runner=fake)

    assert result.status == "verified"
    assert result.cloud_verified is True
    assert result.resources_applied is False
    assert result.build_submitted is False
    assert len(fake.calls) == 8


def test_verify_rejects_disabled_api() -> None:
    with pytest.raises(RuntimeError, match="APIs requeridas"):
        verify_build_pipeline(
            PROJECT_ID,
            IMAGE_TAG,
            runner=FakeGcloud(service_states=("DISABLED", "ENABLED")),
        )


def test_verify_rejects_builder_drift() -> None:
    with pytest.raises(RuntimeError, match="Identidad"):
        verify_build_pipeline(
            PROJECT_ID,
            IMAGE_TAG,
            runner=FakeGcloud(builder_updates={"disabled": True}),
        )


def test_verify_rejects_user_managed_builder_keys() -> None:
    with pytest.raises(RuntimeError, match="claves"):
        verify_build_pipeline(
            PROJECT_ID,
            IMAGE_TAG,
            runner=FakeGcloud(keys=[{"name": "redacted"}]),
        )


def test_verify_rejects_repository_drift() -> None:
    with pytest.raises(RuntimeError, match="Repositorio"):
        verify_build_pipeline(
            PROJECT_ID,
            IMAGE_TAG,
            runner=FakeGcloud(repository_updates={"format": "MAVEN"}),
        )


@pytest.mark.parametrize(
    ("repository_policy", "project_policy", "source_bucket_policy"),
    [
        (
            {"bindings": []},
            _policy("roles/logging.logWriter"),
            _policy("roles/storage.objectViewer"),
        ),
        (
            _policy("roles/artifactregistry.writer"),
            {"bindings": []},
            _policy("roles/storage.objectViewer"),
        ),
        (
            _policy("roles/artifactregistry.writer"),
            _policy("roles/logging.logWriter"),
            {"bindings": []},
        ),
    ],
)
def test_verify_rejects_missing_minimum_binding(
    repository_policy: dict[str, object],
    project_policy: dict[str, object],
    source_bucket_policy: dict[str, object],
) -> None:
    with pytest.raises(RuntimeError, match="binding mínimo"):
        verify_build_pipeline(
            PROJECT_ID,
            IMAGE_TAG,
            runner=FakeGcloud(
                repository_policy=repository_policy,
                project_policy=project_policy,
                source_bucket_policy=source_bucket_policy,
            ),
        )


def test_verify_rejects_an_extra_builder_role() -> None:
    project_policy = {
        "bindings": [
            {"role": "roles/logging.logWriter", "members": [MEMBER]},
            {"role": "roles/editor", "members": [MEMBER]},
        ]
    }

    with pytest.raises(RuntimeError, match="binding mínimo"):
        verify_build_pipeline(
            PROJECT_ID,
            IMAGE_TAG,
            runner=FakeGcloud(project_policy=project_policy),
        )


def test_local_config_digest_drift_is_rejected(tmp_path: Path) -> None:
    definition = load_build_definition()
    changed = tmp_path / "cloudbuild.yaml"
    changed.write_text("steps: []\n", encoding="utf-8")
    drifted = replace(definition, cloudbuild_config=str(changed))

    with pytest.raises(RuntimeError, match="huella"):
        verify_local_build_config(drifted)


@pytest.mark.parametrize("project_id", ["", "INVALID", "short", "-invalid"])
def test_invalid_project_is_rejected_before_cloud_calls(project_id: str) -> None:
    fake = FakeGcloud()
    with pytest.raises(ValueError, match="proyecto"):
        verify_build_pipeline(project_id, IMAGE_TAG, runner=fake)
    assert fake.calls == []
