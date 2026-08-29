"""Declarative Cloud Tasks queue and worker-identity provisioning plan."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

DEFINITION_PATH = Path(__file__).with_name("queue.json")
_PROJECT = re.compile(r"^[a-z][a-z0-9-]{4,28}[a-z0-9]$")


@dataclass(frozen=True, slots=True)
class BuildQueueDefinition:
    schema_version: str
    required_service: str
    location: str
    queue_id: str
    worker_account_id: str
    runtime_account_id: str
    cloud_run_service: str
    max_dispatches_per_second: int
    max_concurrent_dispatches: int
    max_attempts: int
    min_backoff: str
    max_backoff: str
    max_doublings: int


@dataclass(frozen=True, slots=True)
class BuildQueuePlan:
    project_id: str
    definition: BuildQueueDefinition
    worker_email: str
    runtime_email: str
    setup_commands: tuple[tuple[str, ...], ...]
    project_number_command: tuple[str, ...]

    def token_creator_command(self, project_number: str) -> tuple[str, ...]:
        if not project_number.isdigit():
            raise ValueError("El número de proyecto no es válido.")
        return (
            "gcloud",
            "iam",
            "service-accounts",
            "add-iam-policy-binding",
            self.worker_email,
            f"--project={self.project_id}",
            f"--member=serviceAccount:service-{project_number}@gcp-sa-cloudtasks.iam.gserviceaccount.com",
            "--role=roles/iam.serviceAccountTokenCreator",
            "--condition=None",
            "--quiet",
        )


def load_queue_definition(path: Path = DEFINITION_PATH) -> BuildQueueDefinition:
    payload = cast(dict[str, Any], json.loads(path.read_text(encoding="utf-8")))
    definition = BuildQueueDefinition(**payload)
    if (
        definition.schema_version != "1.0.0"
        or definition.required_service != "cloudtasks.googleapis.com"
        or definition.location != "us-central1"
        or definition.queue_id != "taskmaster-builds"
        or definition.worker_account_id != "taskmaster-build-worker"
        or definition.runtime_account_id != "taskmaster-studio-runtime"
        or definition.cloud_run_service != "collaborative-taskmaster-studio"
        or definition.max_dispatches_per_second != 1
        or definition.max_concurrent_dispatches != 1
        or definition.max_attempts != 2
        or definition.min_backoff != "10s"
        or definition.max_backoff != "60s"
        or definition.max_doublings != 2
    ):
        raise ValueError("La cola de construcción no cumple el contrato seguro.")
    return definition


def plan_build_queue(project_id: str) -> BuildQueuePlan:
    if not _PROJECT.fullmatch(project_id):
        raise ValueError("El proyecto Google Cloud no es válido.")
    definition = load_queue_definition()
    worker = f"{definition.worker_account_id}@{project_id}.iam.gserviceaccount.com"
    runtime = f"{definition.runtime_account_id}@{project_id}.iam.gserviceaccount.com"
    queue_flags = (
        f"--location={definition.location}",
        f"--project={project_id}",
        f"--max-dispatches-per-second={definition.max_dispatches_per_second}",
        f"--max-concurrent-dispatches={definition.max_concurrent_dispatches}",
        f"--max-attempts={definition.max_attempts}",
        f"--min-backoff={definition.min_backoff}",
        f"--max-backoff={definition.max_backoff}",
        f"--max-doublings={definition.max_doublings}",
        "--quiet",
    )
    commands = (
        (
            "gcloud",
            "services",
            "enable",
            definition.required_service,
            f"--project={project_id}",
            "--quiet",
        ),
        (
            "gcloud",
            "iam",
            "service-accounts",
            "create",
            definition.worker_account_id,
            f"--project={project_id}",
            "--display-name=Taskmaster build worker",
            "--quiet",
        ),
        (
            "gcloud",
            "tasks",
            "queues",
            "create",
            definition.queue_id,
            *queue_flags,
        ),
        (
            "gcloud",
            "iam",
            "service-accounts",
            "add-iam-policy-binding",
            worker,
            f"--project={project_id}",
            f"--member=serviceAccount:{runtime}",
            "--role=roles/iam.serviceAccountUser",
            "--condition=None",
            "--quiet",
        ),
        (
            "gcloud",
            "run",
            "services",
            "add-iam-policy-binding",
            definition.cloud_run_service,
            f"--region={definition.location}",
            f"--project={project_id}",
            f"--member=serviceAccount:{worker}",
            "--role=roles/run.invoker",
            "--condition=None",
            "--quiet",
        ),
    )
    return BuildQueuePlan(
        project_id=project_id,
        definition=definition,
        worker_email=worker,
        runtime_email=runtime,
        setup_commands=commands,
        project_number_command=(
            "gcloud",
            "projects",
            "describe",
            project_id,
            "--format=value(projectNumber)",
        ),
    )
