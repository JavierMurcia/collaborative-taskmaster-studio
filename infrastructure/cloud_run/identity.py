"""Fail-closed declaration and verification of the Cloud Run runtime identity."""

from __future__ import annotations

import json
import re
import subprocess
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, cast

DEFINITION_PATH = Path(__file__).with_name("service-account.json")
PROJECT_ID_PATTERN = re.compile(r"^[a-z][a-z0-9-]{4,28}[a-z0-9]$")
ACCOUNT_ID_PATTERN = re.compile(r"^[a-z][a-z0-9-]{4,28}[a-z0-9]$")
Runner = Callable[..., subprocess.CompletedProcess[str]]


@dataclass(frozen=True, slots=True)
class RuntimeIdentityDefinition:
    schema_version: str
    account_id: str
    display_name: str
    description: str
    purpose: str
    user_managed_keys_allowed: bool

    def email_for(self, project_id: str) -> str:
        _validate_project_id(project_id)
        return f"{self.account_id}@{project_id}.iam.gserviceaccount.com"


@dataclass(frozen=True, slots=True)
class RuntimeIdentityResult:
    status: str
    project_id: str
    email: str
    definition: RuntimeIdentityDefinition
    create_command: tuple[str, ...]
    verify_commands: tuple[tuple[str, ...], ...]
    cloud_verified: bool
    roles_assigned: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "project_id": self.project_id,
            "email": self.email,
            "definition": asdict(self.definition),
            "create_command": list(self.create_command),
            "verify_commands": [list(command) for command in self.verify_commands],
            "cloud_verified": self.cloud_verified,
            "roles_assigned": self.roles_assigned,
        }


def load_identity_definition(
    path: Path = DEFINITION_PATH,
) -> RuntimeIdentityDefinition:
    payload = cast(dict[str, Any], json.loads(path.read_text(encoding="utf-8")))
    expected = {
        "schema_version",
        "account_id",
        "display_name",
        "description",
        "purpose",
        "user_managed_keys_allowed",
    }
    if set(payload) != expected:
        raise ValueError("La declaración de identidad contiene campos desconocidos o ausentes.")
    definition = RuntimeIdentityDefinition(**payload)
    if definition.schema_version != "1.0.0":
        raise ValueError("La versión de la declaración de identidad no está soportada.")
    if not ACCOUNT_ID_PATTERN.fullmatch(definition.account_id):
        raise ValueError("El ID de la cuenta de servicio no es válido.")
    if not definition.display_name.strip() or len(definition.display_name) > 100:
        raise ValueError("El nombre visible de la cuenta de servicio no es válido.")
    if not definition.description.strip() or len(definition.description) > 256:
        raise ValueError("La descripción de la cuenta de servicio no es válida.")
    if definition.purpose != "cloud_run_runtime":
        raise ValueError("La identidad declarada no corresponde al runtime de Cloud Run.")
    if definition.user_managed_keys_allowed:
        raise ValueError("H10-04 prohíbe claves administradas por el usuario.")
    return definition


def create_command(
    project_id: str,
    definition: RuntimeIdentityDefinition,
    *,
    gcloud: str = "gcloud",
) -> tuple[str, ...]:
    _validate_project_id(project_id)
    return (
        gcloud,
        "iam",
        "service-accounts",
        "create",
        definition.account_id,
        f"--project={project_id}",
        f"--display-name={definition.display_name}",
        f"--description={definition.description}",
        "--quiet",
    )


def verification_commands(
    project_id: str,
    definition: RuntimeIdentityDefinition,
    *,
    gcloud: str = "gcloud",
) -> tuple[tuple[str, ...], ...]:
    email = definition.email_for(project_id)
    return (
        (
            gcloud,
            "iam",
            "service-accounts",
            "describe",
            email,
            f"--project={project_id}",
            "--format=json",
        ),
        (
            gcloud,
            "iam",
            "service-accounts",
            "keys",
            "list",
            f"--iam-account={email}",
            f"--project={project_id}",
            "--managed-by=user",
            "--format=json",
        ),
    )


def plan_runtime_identity(
    project_id: str,
    *,
    gcloud: str = "gcloud",
) -> RuntimeIdentityResult:
    definition = load_identity_definition()
    return _result(
        "planned",
        project_id,
        definition,
        gcloud=gcloud,
        cloud_verified=False,
    )


def verify_runtime_identity(
    project_id: str,
    *,
    gcloud: str = "gcloud",
    runner: Runner = subprocess.run,
) -> RuntimeIdentityResult:
    definition = load_identity_definition()
    result = _result(
        "verified",
        project_id,
        definition,
        gcloud=gcloud,
        cloud_verified=True,
    )
    described = _run(runner, result.verify_commands[0])
    actual = cast(dict[str, Any], json.loads(described.stdout or "{}"))
    _assert_identity_matches(actual, result.email, definition)
    keys = _run(runner, result.verify_commands[1])
    user_managed_keys = cast(list[dict[str, Any]], json.loads(keys.stdout or "[]"))
    if user_managed_keys:
        raise RuntimeError("La identidad de ejecución tiene claves administradas por el usuario.")
    return result


def _result(
    status: str,
    project_id: str,
    definition: RuntimeIdentityDefinition,
    *,
    gcloud: str,
    cloud_verified: bool,
) -> RuntimeIdentityResult:
    _validate_project_id(project_id)
    return RuntimeIdentityResult(
        status=status,
        project_id=project_id,
        email=definition.email_for(project_id),
        definition=definition,
        create_command=create_command(project_id, definition, gcloud=gcloud),
        verify_commands=verification_commands(project_id, definition, gcloud=gcloud),
        cloud_verified=cloud_verified,
    )


def _run(
    runner: Runner,
    command: Sequence[str],
) -> subprocess.CompletedProcess[str]:
    return runner(command, check=True, capture_output=True, text=True)


def _assert_identity_matches(
    actual: dict[str, Any],
    expected_email: str,
    expected: RuntimeIdentityDefinition,
) -> None:
    fields = {
        "email": expected_email,
        "displayName": expected.display_name,
        "description": expected.description,
        "disabled": False,
    }
    drift = {
        field: {"expected": value, "actual": actual.get(field)}
        for field, value in fields.items()
        if actual.get(field) != value
    }
    if drift:
        raise RuntimeError(
            "La cuenta de servicio existente no coincide con la declaración: "
            f"{json.dumps(drift, ensure_ascii=False)}"
        )


def _validate_project_id(project_id: str) -> None:
    if not PROJECT_ID_PATTERN.fullmatch(project_id):
        raise ValueError("El ID del proyecto de Google Cloud no es válido.")

