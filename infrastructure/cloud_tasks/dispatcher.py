"""OIDC-authenticated Cloud Tasks dispatcher."""

from __future__ import annotations

import json
import re
from typing import Any

from studio.ports.build_dispatcher import BuildOperation

from .config import CloudTasksSettings

_BUILD_ID = re.compile(r"^build_[a-f0-9]{16}$")


class CloudTasksBuildDispatcher:
    """Create idempotent HTTP tasks without placing user data in their payload."""

    external = True

    def __init__(self, settings: CloudTasksSettings, client: Any) -> None:
        settings.validate()
        self._settings = settings
        self._client = client
        self._parent = client.queue_path(settings.project, settings.location, settings.queue)

    def dispatch(self, build_id: str, operation: BuildOperation, attempt: int) -> str:
        if not _BUILD_ID.fullmatch(build_id) or operation not in {"construct", "test"}:
            raise ValueError("El trabajo solicitado no cumple el contrato Cloud Tasks.")
        bounded_attempt = min(3, max(0, attempt))
        task_id = f"{build_id.replace('_', '-')}-{operation}-a{bounded_attempt}"
        task_name = f"{self._parent}/tasks/{task_id}"
        task = {
            "name": task_name,
            "http_request": {
                "http_method": 1,
                "url": self._settings.target_url,
                "headers": {"Content-Type": "application/json"},
                "body": json.dumps(
                    {
                        "schema_version": "1.0.0",
                        "build_id": build_id,
                        "operation": operation,
                    },
                    separators=(",", ":"),
                ).encode("utf-8"),
                "oidc_token": {
                    "service_account_email": self._settings.worker_service_account,
                    "audience": self._settings.audience,
                },
            },
            "dispatch_deadline": {"seconds": 300},
        }
        try:
            created = self._client.create_task(request={"parent": self._parent, "task": task})
        except Exception as error:
            if type(error).__name__ != "AlreadyExists":
                raise
            return task_name
        return str(getattr(created, "name", task_name))
