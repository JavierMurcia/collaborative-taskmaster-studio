"""Exact least-privilege IAM declaration for the Cloud Run runtime identity."""

from __future__ import annotations

import json
import re
import subprocess
from collections.abc import Callable
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, cast

from infrastructure.cloud_run.identity import load_identity_definition

DEFINITION_PATH = Path(__file__).with_name("iam-policy.json")
PROJECT_ID_PATTERN = re.compile(r"^[a-z][a-z0-9-]{4,28}[a-z0-9]$")
EXPECTED_ROLES = frozenset(
    {
        "roles/aiplatform.user",
        "roles/datastore.user",
        "roles/secretmanager.secretAccessor",
    }
)
Runner = Callable[..., subprocess.CompletedProcess[str]]


@dataclass(frozen=True, slots=True)
class IamCondition:
    title: str
    description: str
    expression_template: str

    def render(self, project_id: str) -> dict[str, str]:
        _validate_project_id(project_id)
        return {
            "title": self.title,
            "description": self.description,
            "expression": self.expression_template.format(project_id=project_id),
        }


@dataclass(frozen=True, slots=True)
class IamBinding:
    role: str
    purpose: str
    condition: IamCondition | None


@dataclass(frozen=True, slots=True)
class RuntimeIamDefinition:
    schema_version: str
    exact_project_roles: bool
    bindings: tuple[IamBinding, ...]
    forbidden_roles: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class RuntimeIamResult:
    status: str
    project_id: str
    member: str
    definition: RuntimeIamDefinition
    binding_commands: tuple[tuple[str, ...], ...]
    verify_command: tuple[str, ...]
    cloud_verified: bool
    bindings_applied: bool

    def as_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "project_id": self.project_id,
            "member": self.member,
            "definition": {
                "schema_version": self.definition.schema_version,
                "exact_project_roles": self.definition.exact_project_roles,
                "bindings": [
                    {
                        "role": binding.role,
                        "purpose": binding.purpose,
                        "condition": (
                            asdict(binding.condition)
                            if binding.condition is not None
                            else None
                        ),
                    }
                    for binding in self.definition.bindings
                ],
                "forbidden_roles": list(self.definition.forbidden_roles),
            },
            "binding_commands": [list(command) for command in self.binding_commands],
            "verify_command": list(self.verify_command),
            "cloud_verified": self.cloud_verified,
            "bindings_applied": self.bindings_applied,
        }


def load_iam_definition(path: Path = DEFINITION_PATH) -> RuntimeIamDefinition:
    payload = cast(dict[str, Any], json.loads(path.read_text(encoding="utf-8")))
    if set(payload) != {
        "schema_version",
        "exact_project_roles",
        "bindings",
        "forbidden_roles",
    }:
        raise ValueError("La declaración IAM contiene campos desconocidos o ausentes.")
    raw_bindings = cast(list[dict[str, Any]], payload["bindings"])
    bindings: list[IamBinding] = []
    for raw in raw_bindings:
        if set(raw) != {"role", "purpose", "condition"}:
            raise ValueError("Un binding IAM contiene campos desconocidos o ausentes.")
        raw_condition = raw["condition"]
        condition: IamCondition | None = None
        if raw_condition is not None:
            condition_payload = cast(dict[str, str], raw_condition)
            if set(condition_payload) != {
                "title",
                "description",
                "expression_template",
            }:
                raise ValueError("Una condición IAM no cumple el contrato esperado.")
            condition = IamCondition(**condition_payload)
        bindings.append(
            IamBinding(
                role=str(raw["role"]),
                purpose=str(raw["purpose"]),
                condition=condition,
            )
        )
    definition = RuntimeIamDefinition(
        schema_version=str(payload["schema_version"]),
        exact_project_roles=bool(payload["exact_project_roles"]),
        bindings=tuple(bindings),
        forbidden_roles=tuple(cast(list[str], payload["forbidden_roles"])),
    )
    _validate_definition(definition)
    return definition


def binding_commands(
    project_id: str,
    definition: RuntimeIamDefinition,
    *,
    gcloud: str = "gcloud",
) -> tuple[tuple[str, ...], ...]:
    _validate_project_id(project_id)
    member = _member(project_id)
    commands: list[tuple[str, ...]] = []
    for binding in definition.bindings:
        condition = (
            "None"
            if binding.condition is None
            else _condition_argument(binding.condition.render(project_id))
        )
        commands.append(
            (
                gcloud,
                "projects",
                "add-iam-policy-binding",
                project_id,
                f"--member={member}",
                f"--role={binding.role}",
                f"--condition={condition}",
                "--quiet",
            )
        )
    return tuple(commands)


def policy_command(project_id: str, *, gcloud: str = "gcloud") -> tuple[str, ...]:
    _validate_project_id(project_id)
    return (
        gcloud,
        "projects",
        "get-iam-policy",
        project_id,
        "--format=json",
    )


def plan_runtime_iam(
    project_id: str,
    *,
    gcloud: str = "gcloud",
) -> RuntimeIamResult:
    definition = load_iam_definition()
    return _result(
        "planned",
        project_id,
        definition,
        gcloud=gcloud,
        cloud_verified=False,
        bindings_applied=False,
    )


def verify_runtime_iam(
    project_id: str,
    *,
    gcloud: str = "gcloud",
    runner: Runner = subprocess.run,
) -> RuntimeIamResult:
    definition = load_iam_definition()
    result = _result(
        "verified",
        project_id,
        definition,
        gcloud=gcloud,
        cloud_verified=True,
        bindings_applied=True,
    )
    response = runner(
        result.verify_command,
        check=True,
        capture_output=True,
        text=True,
    )
    policy = cast(dict[str, Any], json.loads(response.stdout or "{}"))
    _assert_policy_matches(policy, project_id, definition)
    return result


def _result(
    status: str,
    project_id: str,
    definition: RuntimeIamDefinition,
    *,
    gcloud: str,
    cloud_verified: bool,
    bindings_applied: bool,
) -> RuntimeIamResult:
    _validate_project_id(project_id)
    return RuntimeIamResult(
        status=status,
        project_id=project_id,
        member=_member(project_id),
        definition=definition,
        binding_commands=binding_commands(project_id, definition, gcloud=gcloud),
        verify_command=policy_command(project_id, gcloud=gcloud),
        cloud_verified=cloud_verified,
        bindings_applied=bindings_applied,
    )


def _validate_definition(definition: RuntimeIamDefinition) -> None:
    if definition.schema_version != "1.0.0":
        raise ValueError("La versión de la declaración IAM no está soportada.")
    if not definition.exact_project_roles:
        raise ValueError("H10-05 requiere verificación exacta de roles del runtime.")
    roles = [binding.role for binding in definition.bindings]
    if len(roles) != len(set(roles)) or frozenset(roles) != EXPECTED_ROLES:
        raise ValueError("La declaración IAM no contiene exactamente los roles mínimos.")
    if any(not binding.purpose.strip() for binding in definition.bindings):
        raise ValueError("Cada binding IAM debe explicar su propósito.")
    forbidden = frozenset(definition.forbidden_roles)
    if not forbidden or EXPECTED_ROLES & forbidden:
        raise ValueError("La lista de roles IAM prohibidos no es válida.")
    firestore = next(
        binding for binding in definition.bindings if binding.role == "roles/datastore.user"
    )
    if firestore.condition is None or "{project_id}" not in firestore.condition.expression_template:
        raise ValueError("El acceso Firestore debe limitarse a la base declarada.")
    vertex = next(
        binding for binding in definition.bindings if binding.role == "roles/aiplatform.user"
    )
    if vertex.condition is not None:
        raise ValueError("El binding Vertex AI no admite una condición en esta declaración.")
    secrets = next(
        binding
        for binding in definition.bindings
        if binding.role == "roles/secretmanager.secretAccessor"
    )
    if (
        secrets.condition is None
        or secrets.condition.expression_template.count("projects/{project_id}/secrets/studio-") != 6
    ):
        raise ValueError("El acceso a secretos debe limitarse a los seis secretos declarados.")


def _assert_policy_matches(
    policy: dict[str, Any],
    project_id: str,
    definition: RuntimeIamDefinition,
) -> None:
    member = _member(project_id)
    actual: dict[str, list[dict[str, str] | None]] = {}
    for raw in cast(list[dict[str, Any]], policy.get("bindings", [])):
        if member not in cast(list[str], raw.get("members", [])):
            continue
        condition = raw.get("condition")
        normalized = (
            _normalized_condition(cast(dict[str, Any], condition))
            if condition is not None
            else None
        )
        actual.setdefault(str(raw.get("role", "")), []).append(normalized)

    expected = {
        binding.role: (
            binding.condition.render(project_id)
            if binding.condition is not None
            else None
        )
        for binding in definition.bindings
    }
    missing = sorted(set(expected) - set(actual))
    unexpected = sorted(set(actual) - set(expected))
    mismatched = sorted(
        role
        for role in set(expected) & set(actual)
        if actual[role] != [expected[role]]
    )
    forbidden = sorted(set(actual) & set(definition.forbidden_roles))
    if missing or unexpected or mismatched or forbidden:
        raise RuntimeError(
            "Los permisos de la identidad de ejecución no coinciden con el contrato mínimo: "
            f"{json.dumps({'missing': missing, 'unexpected': unexpected, 'mismatched': mismatched, 'forbidden': forbidden})}"
        )


def _normalized_condition(condition: dict[str, Any]) -> dict[str, str]:
    return {
        "title": str(condition.get("title", "")),
        "description": str(condition.get("description", "")),
        "expression": str(condition.get("expression", "")),
    }


def _condition_argument(condition: dict[str, str]) -> str:
    return ",".join(
        (
            f"expression={condition['expression']}",
            f"title={condition['title']}",
            f"description={condition['description']}",
        )
    )


def _member(project_id: str) -> str:
    identity = load_identity_definition()
    return f"serviceAccount:{identity.email_for(project_id)}"


def _validate_project_id(project_id: str) -> None:
    if not PROJECT_ID_PATTERN.fullmatch(project_id):
        raise ValueError("El ID del proyecto de Google Cloud no es válido.")
