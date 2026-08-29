"""H10-08 declarative scale-to-zero Cloud Run deployment."""

from __future__ import annotations

import json
import re
import subprocess
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pytest

from infrastructure.cloud_run.deployment import (
    load_deployment_definition,
    plan_deployment,
    verify_deployment,
)
from infrastructure.cloud_run.deployment_check import main

PROJECT = "collaborative-taskmaster-dev"
DIGEST = "a" * 64
IMAGE = (
    "us-central1-docker.pkg.dev/collaborative-taskmaster-dev/"
    f"collaborative-taskmaster/studio@sha256:{DIGEST}"
)
RUNTIME = "taskmaster-studio-runtime@collaborative-taskmaster-dev.iam.gserviceaccount.com"


def test_definition_uses_service_level_scale_to_zero_only() -> None:
    definition = load_deployment_definition()

    assert definition.service_name == "collaborative-taskmaster-studio"
    assert definition.service_min_instances == 0
    assert definition.service_max_instances == 1
    assert definition.revision_min_instances is None
    assert definition.scaling_scope == "service"
    assert definition.execution_environment == "gen2"
    assert definition.container_port == 8080
    assert definition.container_concurrency == 1


def test_plan_uses_an_exact_digest_runtime_identity_and_service_minimum() -> None:
    result = plan_deployment(PROJECT, DIGEST)
    command = result.deploy_command

    assert result.status == "planned"
    assert result.image_uri == IMAGE
    assert result.runtime_email == RUNTIME
    assert f"--image={IMAGE}" in command
    assert "--min=0" in command
    assert "--max=1" in command
    assert not any(item.startswith("--min-instances") for item in command)
    assert f"--service-account={RUNTIME}" in command
    assert "--allow-unauthenticated" in command
    assert "--execution-environment=gen2" in command
    assert "--port=8080" in command
    assert "--concurrency=1" in command
    assert any(item.startswith("--set-env-vars=") for item in command)
    assert any(item.startswith("--set-secrets=") for item in command)
    assert result.cloud_verified is False
    assert result.deployment_executed is False
    assert result.service_ready is False


def test_plan_declares_api_enablement_without_executing_it() -> None:
    result = plan_deployment(PROJECT, DIGEST)

    assert result.prerequisite_commands == (
        (
            "gcloud",
            "services",
            "enable",
            "run.googleapis.com",
            f"--project={PROJECT}",
            "--quiet",
        ),
    )
    assert len(result.verify_commands) == 3


@pytest.mark.parametrize(
    "digest",
    ["", "a" * 63, "A" * 64, "sha256:" + "a" * 64, "latest"],
)
def test_plan_rejects_non_exact_image_digests(digest: str) -> None:
    with pytest.raises(ValueError, match="digest"):
        plan_deployment(PROJECT, digest)


@pytest.mark.parametrize("project", ["", "INVALID", "four", "project_underscore"])
def test_plan_rejects_invalid_project_ids(project: str) -> None:
    with pytest.raises(ValueError, match="proyecto"):
        plan_deployment(project, DIGEST)


def test_cli_prints_machine_readable_plan(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["--project", PROJECT, "--image-digest", DIGEST]) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "planned"
    assert payload["definition"]["service_min_instances"] == 0
    assert payload["definition"]["revision_min_instances"] is None
    assert payload["deployment_executed"] is False


def test_verify_accepts_exact_ready_public_service() -> None:
    runner = FakeRunner(_valid_outputs())

    result = verify_deployment(PROJECT, DIGEST, runner=runner)

    assert len(runner.calls) == 3
    assert result.status == "verified"
    assert result.cloud_verified is True
    assert result.deployment_executed is False
    assert result.service_ready is True
    assert result.public_url == "https://collaborative-taskmaster-studio.example.run.app"


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (lambda service: service["metadata"].update(name="wrong"), "nombre"),
        (lambda service: service["metadata"]["labels"].pop("stage"), "etiquetas"),
        (
            lambda service: service["metadata"]["annotations"].update(
                {"run.googleapis.com/minScale": "1"}
            ),
            "min instances",
        ),
        (
            lambda service: service["metadata"]["annotations"].update(
                {"run.googleapis.com/ingress": "internal"}
            ),
            "ingress",
        ),
        (
            lambda service: service["metadata"]["annotations"].update(
                {"run.googleapis.com/maxScale": "20"}
            ),
            "máximo",
        ),
        (
            lambda service: service["spec"]["template"]["metadata"][
                "annotations"
            ].update({"autoscaling.knative.dev/minScale": "1"}),
            "mezclarse",
        ),
        (
            lambda service: service["spec"]["template"]["spec"].update(
                serviceAccountName="another@example.iam.gserviceaccount.com"
            ),
            "identidad",
        ),
        (
            lambda service: service["spec"]["template"]["spec"].update(
                containerConcurrency=80
            ),
            "concurrencia",
        ),
        (
            lambda service: service["spec"]["template"]["spec"]["containers"][
                0
            ].update(image="us-central1-docker.pkg.dev/example/repo/app:latest"),
            "digest",
        ),
        (
            lambda service: service["status"].update(conditions=[]),
            "no está lista",
        ),
        (
            lambda service: service["status"].update(traffic=[]),
            "tráfico",
        ),
    ],
)
def test_verify_rejects_service_drift(
    mutation: Any,
    message: str,
) -> None:
    outputs = _valid_outputs()
    service = json.loads(outputs[1])
    mutation(service)
    outputs[1] = json.dumps(service)

    with pytest.raises(RuntimeError, match=message):
        verify_deployment(PROJECT, DIGEST, runner=FakeRunner(outputs))


def test_verify_rejects_disabled_cloud_run_api() -> None:
    outputs = _valid_outputs()
    outputs[0] = json.dumps(
        [{"state": "DISABLED", "config": {"name": "run.googleapis.com"}}]
    )

    with pytest.raises(RuntimeError, match="API"):
        verify_deployment(PROJECT, DIGEST, runner=FakeRunner(outputs))


@pytest.mark.parametrize(
    "bindings",
    [
        [],
        [{"role": "roles/run.invoker", "members": ["user:a@example.com"]}],
        [
            {
                "role": "roles/run.invoker",
                "members": ["allUsers"],
                "condition": {"expression": "true"},
            }
        ],
    ],
)
def test_verify_rejects_missing_unconditional_public_invoker(
    bindings: list[dict[str, Any]],
) -> None:
    outputs = _valid_outputs()
    outputs[2] = json.dumps({"bindings": bindings})

    with pytest.raises(RuntimeError, match="pública"):
        verify_deployment(PROJECT, DIGEST, runner=FakeRunner(outputs))


class FakeRunner:
    def __init__(self, outputs: Sequence[str]) -> None:
        self.outputs = iter(outputs)
        self.calls: list[tuple[str, ...]] = []

    def __call__(
        self,
        command: Sequence[str],
        *,
        check: bool,
        capture_output: bool,
        text: bool,
    ) -> subprocess.CompletedProcess[str]:
        assert check is True
        assert capture_output is True
        assert text is True
        self.calls.append(tuple(command))
        return subprocess.CompletedProcess(command, 0, stdout=next(self.outputs))


def _valid_outputs() -> list[str]:
    service = {
        "metadata": {
            "name": "collaborative-taskmaster-studio",
            "labels": {
                "app": "collaborative-taskmaster-studio",
                "managed-by": "declarative-plan",
                "stage": "h10-10",
            },
            "annotations": {
                "run.googleapis.com/minScale": "0",
                "run.googleapis.com/maxScale": "1",
                "run.googleapis.com/ingress": "all",
            },
        },
        "spec": {
            "template": {
                "metadata": {
                    "annotations": {
                        "run.googleapis.com/execution-environment": "gen2"
                    }
                },
                "spec": {
                    "serviceAccountName": RUNTIME,
                    "containerConcurrency": 1,
                    "containers": [
                        {
                            "image": IMAGE,
                            "ports": [{"containerPort": 8080}],
                        }
                    ],
                },
            }
        },
        "status": {
            "conditions": [{"type": "Ready", "status": "True"}],
            "latestReadyRevisionName": "collaborative-taskmaster-studio-00001",
            "traffic": [{"latestRevision": True, "percent": 100}],
            "url": "https://collaborative-taskmaster-studio.example.run.app",
        },
    }
    return [
        json.dumps(
            [{"state": "ENABLED", "config": {"name": "run.googleapis.com"}}]
        ),
        json.dumps(service),
        json.dumps(
            {"bindings": [{"role": "roles/run.invoker", "members": ["allUsers"]}]}
        ),
    ]


def test_deployment_declaration_is_versioned() -> None:
    payload = json.loads(
        Path("infrastructure/cloud_run/deployment.json").read_text(encoding="utf-8")
    )
    assert payload["schema_version"] == "1.0.0"


def test_h10_10_evidence_records_the_verified_immutable_revision_and_journey() -> None:
    payload = json.loads(
        Path("infrastructure/cloud_run/deployment-evidence.json").read_text(
            encoding="utf-8"
        )
    )

    assert payload["milestone"] == "H10-10"
    assert payload["result"] == "passed"
    assert payload["revision"].startswith("collaborative-taskmaster-studio-")
    assert payload["service_min_instances"] == 0
    assert payload["service_max_instances"] == 1
    assert payload["container_concurrency"] == 1
    assert re.fullmatch(r"sha256:[a-f0-9]{64}", payload["image_digest"])
    assert payload["full_journey"]["evaluation_decision"] == "ready"
    assert payload["full_journey"]["approved_revision"] == 2
    assert payload["full_journey"]["model_completed_events"] >= 1
