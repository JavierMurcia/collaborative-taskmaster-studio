from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from infrastructure.cloud_tasks.config import CloudTasksSettings, initialize_cloud_tasks
from infrastructure.cloud_tasks.dispatcher import CloudTasksBuildDispatcher
from infrastructure.cloud_tasks.provisioning import load_queue_definition, plan_build_queue
from studio.domain.errors import DomainError
from studio.security.worker_identity import WorkerIdentitySettings, WorkerTokenVerifier


class FakeTasksClient:
    def __init__(self) -> None:
        self.requests: list[dict[str, object]] = []

    @staticmethod
    def queue_path(project: str, location: str, queue: str) -> str:
        return f"projects/{project}/locations/{location}/queues/{queue}"

    def create_task(self, *, request):
        self.requests.append(request)
        return SimpleNamespace(name=request["task"]["name"])


def settings() -> CloudTasksSettings:
    return CloudTasksSettings(
        enabled=True,
        project="sentinel-taskmaster-dev",
        location="us-central1",
        queue="taskmaster-builds",
        target_url="https://studio.example.run.app/api/v1/internal/build-worker",
        audience="https://studio.example.run.app",
        worker_service_account=(
            "taskmaster-build-worker@sentinel-taskmaster-dev.iam.gserviceaccount.com"
        ),
    )


def test_environment_builds_the_exact_internal_api_target(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("STUDIO_ENABLE_CLOUD_TASKS", "true")
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "collaborative-taskmaster-dev")
    monkeypatch.setenv("STUDIO_PUBLIC_BASE_URL", "https://studio.example.test/")

    configured = CloudTasksSettings.from_environment()

    assert configured.target_url == (
        "https://studio.example.test/api/v1/internal/build-worker"
    )
    assert configured.audience == "https://studio.example.test"


def test_dispatcher_creates_minimal_oidc_task() -> None:
    client = FakeTasksClient()
    dispatcher = CloudTasksBuildDispatcher(settings(), client)

    name = dispatcher.dispatch("build_1234567890abcdef", "construct", 0)

    assert name.endswith("/tasks/build-1234567890abcdef-construct-a0")
    task = client.requests[0]["task"]
    request = task["http_request"]  # type: ignore[index]
    assert request["oidc_token"]["audience"] == "https://studio.example.run.app"  # type: ignore[index]
    assert json.loads(request["body"]) == {  # type: ignore[arg-type,index]
        "schema_version": "1.0.0",
        "build_id": "build_1234567890abcdef",
        "operation": "construct",
    }
    assert "owner" not in request["body"].decode()  # type: ignore[index,union-attr]


def test_runtime_initialization_is_fail_closed_when_enabled() -> None:
    runtime = initialize_cloud_tasks(
        CloudTasksSettings(enabled=True, project="invalid"),
        client=FakeTasksClient(),
    )
    assert runtime.ready is False
    assert runtime.dispatcher is None
    assert runtime.status == "error"


def test_worker_token_requires_exact_identity_and_queue() -> None:
    verifier = WorkerTokenVerifier(
        WorkerIdentitySettings(
            enabled=True,
            audience="https://studio.example.run.app",
            service_account_email=(
                "taskmaster-build-worker@sentinel-taskmaster-dev.iam.gserviceaccount.com"
            ),
            queue_name="taskmaster-builds",
        ),
        token_verifier=lambda token, audience: {
            "iss": "https://accounts.google.com",
            "email": "taskmaster-build-worker@sentinel-taskmaster-dev.iam.gserviceaccount.com",
            "email_verified": True,
            "aud": audience,
            "token": token,
        },
    )
    claims = verifier.verify("Bearer signed", "task-name", "taskmaster-builds")
    assert claims["token"] == "signed"
    with pytest.raises(DomainError, match="cola declarada"):
        verifier.verify("Bearer signed", "task-name", "another-queue")


def test_declarative_queue_plan_is_bounded_and_uses_dedicated_identity() -> None:
    definition = load_queue_definition()
    plan = plan_build_queue("sentinel-taskmaster-dev")
    assert definition.max_concurrent_dispatches == 1
    assert definition.max_attempts == 2
    assert plan.worker_email.startswith("taskmaster-build-worker@")
    serialized = " ".join(part for command in plan.setup_commands for part in command)
    assert "roles/iam.serviceAccountUser" in serialized
    assert "roles/run.invoker" in serialized
    assert "roles/iam.serviceAccountTokenCreator" not in serialized
