"""Controlled HTTP smoke journey for an H10-09 Cloud Run deployment."""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import asdict, dataclass
from typing import Any, cast
from urllib.error import HTTPError
from urllib.parse import urljoin, urlparse
from urllib.request import Request, urlopen
from uuid import uuid4

Requester = Callable[[Request, float], tuple[int, dict[str, Any]]]


@dataclass(frozen=True, slots=True)
class SmokeCheck:
    name: str
    method: str
    path: str
    status_code: int


@dataclass(frozen=True, slots=True)
class SmokeResult:
    status: str
    base_url: str
    checks: tuple[SmokeCheck, ...]
    functional_write_executed: bool
    project_id: str | None

    def as_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "base_url": self.base_url,
            "checks": [asdict(check) for check in self.checks],
            "functional_write_executed": self.functional_write_executed,
            "project_id": self.project_id,
        }


def run_smoke(
    base_url: str,
    *,
    functional: bool = False,
    timeout_seconds: float = 15.0,
    requester: Requester | None = None,
) -> SmokeResult:
    """Verify probes/meta and optionally persist then read one isolated project."""
    normalized = _validate_url(base_url)
    send = requester or _request_json
    checks: list[SmokeCheck] = []

    expectations: tuple[tuple[str, str, str], ...] = (
        ("liveness", "/health/live", "alive"),
        ("startup", "/health/startup", "started"),
        ("readiness", "/health/ready", "ready"),
    )
    for name, path, expected_status in expectations:
        status_code, payload = send(
            Request(urljoin(normalized, path), method="GET"), timeout_seconds
        )
        if status_code != 200 or payload.get("status") != expected_status:
            raise RuntimeError(f"El smoke check {name} no está listo: {payload!r}")
        checks.append(SmokeCheck(name, "GET", path, status_code))

    meta_path = "/api/v1/meta"
    meta_code, meta = send(
        Request(urljoin(normalized, meta_path), method="GET"), timeout_seconds
    )
    firestore = cast(dict[str, Any], meta.get("firestore_database", {}))
    if (
        meta_code != 200
        or meta.get("name") != "Collaborative Taskmaster Studio"
        or firestore.get("status") != "ready"
        or firestore.get("repository_active") is not True
    ):
        raise RuntimeError("El metadato desplegado no confirma Firestore activo y listo.")
    checks.append(SmokeCheck("metadata", "GET", meta_path, meta_code))

    project_id: str | None = None
    if functional:
        token = uuid4().hex
        session = f"h10_smoke_{token[:12]}"
        create_path = "/api/v1/projects"
        body = json.dumps(
            {
                "name": "H10-09 controlled smoke journey",
                "description": (
                    "Verifies that the deployed service can persist and restore one "
                    "isolated Collaborative Taskmaster project in Firestore."
                ),
            }
        ).encode("utf-8")
        create_code, created = send(
            Request(
                urljoin(normalized, create_path),
                data=body,
                headers={
                    "Content-Type": "application/json",
                    "X-Studio-Session": session,
                    "Idempotency-Key": f"h10-smoke-{token}",
                },
                method="POST",
            ),
            timeout_seconds,
        )
        project_id = str(
            cast(dict[str, Any], cast(dict[str, Any], created.get("snapshot", {})).get("project", {})).get("id", "")
        )
        if create_code != 201 or not project_id:
            raise RuntimeError("El recorrido funcional no pudo crear el proyecto aislado.")
        checks.append(SmokeCheck("project_create", "POST", create_path, create_code))

        read_path = f"/api/v1/projects/{project_id}"
        read_code, restored = send(
            Request(
                urljoin(normalized, read_path),
                headers={"X-Studio-Session": session},
                method="GET",
            ),
            timeout_seconds,
        )
        restored_project = cast(
            dict[str, Any],
            cast(dict[str, Any], restored.get("snapshot", {})).get("project", {}),
        )
        if read_code != 200 or restored_project.get("id") != project_id:
            raise RuntimeError("El recorrido funcional no pudo restaurar el proyecto.")
        checks.append(SmokeCheck("project_read", "GET", read_path, read_code))

    return SmokeResult(
        status="passed",
        base_url=normalized.rstrip("/"),
        checks=tuple(checks),
        functional_write_executed=functional,
        project_id=project_id,
    )


def _request_json(request: Request, timeout_seconds: float) -> tuple[int, dict[str, Any]]:
    try:
        with urlopen(request, timeout=timeout_seconds) as response:  # noqa: S310
            return response.status, cast(dict[str, Any], json.load(response))
    except HTTPError as error:
        payload = json.loads(error.read().decode("utf-8") or "{}")
        return error.code, cast(dict[str, Any], payload)


def _validate_url(base_url: str) -> str:
    parsed = urlparse(base_url)
    if parsed.scheme != "https" or not parsed.netloc or parsed.query or parsed.fragment:
        raise ValueError("H10-09 exige una URL base HTTPS sin query ni fragmento.")
    return base_url.rstrip("/") + "/"
