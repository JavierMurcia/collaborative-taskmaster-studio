"""Validated runtime configuration for Cloud Tasks build dispatch."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import Any

from studio.ports.build_dispatcher import BuildDispatcher

_IDENTIFIER = re.compile(r"^[a-z][a-z0-9-]{0,62}$")
_SERVICE_ACCOUNT = re.compile(
    r"^[a-z][a-z0-9-]{4,28}[a-z0-9]@[a-z][a-z0-9-]{4,28}[a-z0-9]\.iam\.gserviceaccount\.com$"
)


@dataclass(frozen=True, slots=True)
class CloudTasksSettings:
    enabled: bool = False
    project: str = ""
    location: str = "us-central1"
    queue: str = "taskmaster-builds"
    target_url: str = ""
    audience: str = ""
    worker_service_account: str = ""

    @classmethod
    def from_environment(cls) -> CloudTasksSettings:
        project = os.getenv("GOOGLE_CLOUD_PROJECT", "").strip()
        base_url = os.getenv("STUDIO_PUBLIC_BASE_URL", "").strip().rstrip("/")
        worker = os.getenv(
            "STUDIO_BUILD_WORKER_SERVICE_ACCOUNT",
            f"taskmaster-build-worker@{project}.iam.gserviceaccount.com" if project else "",
        ).strip()
        return cls(
            enabled=os.getenv("STUDIO_ENABLE_CLOUD_TASKS", "false").strip().casefold()
            == "true",
            project=project,
            location=os.getenv("STUDIO_CLOUD_TASKS_LOCATION", "us-central1").strip(),
            queue=os.getenv("STUDIO_CLOUD_TASKS_QUEUE", "taskmaster-builds").strip(),
            target_url=f"{base_url}/api/v1/internal/build-worker" if base_url else "",
            audience=base_url,
            worker_service_account=worker,
        )

    def validate(self) -> None:
        if not self.enabled:
            return
        if not _IDENTIFIER.fullmatch(self.project):
            raise ValueError("Cloud Tasks requiere un proyecto Google Cloud válido.")
        if not _IDENTIFIER.fullmatch(self.location) or not _IDENTIFIER.fullmatch(self.queue):
            raise ValueError("La región o la cola Cloud Tasks no son válidas.")
        if not self.target_url.startswith("https://") or not self.audience.startswith("https://"):
            raise ValueError("Cloud Tasks requiere una URL HTTPS pública y estable.")
        if not _SERVICE_ACCOUNT.fullmatch(self.worker_service_account):
            raise ValueError("La identidad del worker Cloud Tasks no es válida.")


@dataclass(frozen=True, slots=True)
class CloudTasksRuntime:
    settings: CloudTasksSettings
    dispatcher: BuildDispatcher | None
    status: str
    detail: str

    @property
    def ready(self) -> bool:
        return not self.settings.enabled or self.dispatcher is not None


def initialize_cloud_tasks(
    settings: CloudTasksSettings,
    *,
    client: Any | None = None,
) -> CloudTasksRuntime:
    if not settings.enabled:
        return CloudTasksRuntime(settings, None, "disabled", "Despacho local habilitado.")
    try:
        settings.validate()
        if client is None:
            from google.cloud import tasks_v2

            client = tasks_v2.CloudTasksClient()
        from .dispatcher import CloudTasksBuildDispatcher

        dispatcher = CloudTasksBuildDispatcher(settings, client)
        return CloudTasksRuntime(
            settings,
            dispatcher,
            "ready",
            "Cloud Tasks entregará las construcciones al worker autenticado.",
        )
    except Exception as error:
        return CloudTasksRuntime(
            settings,
            None,
            "error",
            f"Cloud Tasks no está disponible: {type(error).__name__}.",
        )
