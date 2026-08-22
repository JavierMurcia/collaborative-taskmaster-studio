"""Safe Cloud Run environment and secret declaration for H10-07."""

from __future__ import annotations

import json
import re
import subprocess
from collections.abc import Callable
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, cast

from infrastructure.cloud_run.identity import load_identity_definition

DEFINITION_PATH = Path(__file__).with_name("runtime-config.json")
PROJECT_ROOT = Path(__file__).resolve().parents[2]
PROJECT_ID_PATTERN = re.compile(r"^[a-z][a-z0-9-]{4,28}[a-z0-9]$")
VARIABLE_NAME_PATTERN = re.compile(r"^[A-Z][A-Z0-9_]{1,127}$")
SECRET_ID_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,254}$")
SECRET_VERSION_PATTERN = re.compile(r"^[1-9][0-9]*$")
SENSITIVE_NAME_PATTERN = re.compile(
    r"(?:^|_)(?:SECRET|PASSWORD|PASSWD|ACCESS_TOKEN|API_KEY|PRIVATE_KEY|CREDENTIALS?)(?:$|_)",
    re.IGNORECASE,
)
Runner = Callable[..., subprocess.CompletedProcess[str]]


@dataclass(frozen=True, slots=True)
class EnvironmentVariable:
    name: str
    value_template: str
    purpose: str

    def render(self, project_id: str) -> str:
        _validate_project_id(project_id)
        return self.value_template.format(project_id=project_id)


@dataclass(frozen=True, slots=True)
class SecretReference:
    environment_variable: str
    secret_id: str
    version: str
    purpose: str


@dataclass(frozen=True, slots=True)
class SecretPolicy:
    provider: str
    allow_plaintext_values: bool
    require_numeric_version: bool
    latest_alias_allowed: bool
    delivery: str


@dataclass(frozen=True, slots=True)
class RuntimeConfigurationDefinition:
    schema_version: str
    service_name: str
    region: str
    environment_variables: tuple[EnvironmentVariable, ...]
    secrets: tuple[SecretReference, ...]
    secret_policy: SecretPolicy
    forbidden_environment_variables: tuple[str, ...]
    cloud_run_reserved_variables: tuple[str, ...]
    runtime_secret_accessor_required: bool

    def rendered_environment(self, project_id: str) -> dict[str, str]:
        return {
            variable.name: variable.render(project_id)
            for variable in self.environment_variables
        }


@dataclass(frozen=True, slots=True)
class RuntimeConfigurationResult:
    status: str
    project_id: str
    service_name: str
    region: str
    runtime_email: str
    environment: dict[str, str]
    secret_references: tuple[SecretReference, ...]
    deployment_arguments: tuple[str, ...]
    verify_command: tuple[str, ...]
    repository_scan_verified: bool
    cloud_verified: bool
    configuration_applied: bool
    secret_payloads_created: bool

    def as_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "project_id": self.project_id,
            "service_name": self.service_name,
            "region": self.region,
            "runtime_email": self.runtime_email,
            "environment": self.environment,
            "secret_references": [asdict(secret) for secret in self.secret_references],
            "deployment_arguments": list(self.deployment_arguments),
            "verify_command": list(self.verify_command),
            "repository_scan_verified": self.repository_scan_verified,
            "cloud_verified": self.cloud_verified,
            "configuration_applied": self.configuration_applied,
            "secret_payloads_created": self.secret_payloads_created,
        }


def load_runtime_configuration(
    path: Path = DEFINITION_PATH,
) -> RuntimeConfigurationDefinition:
    payload = cast(dict[str, Any], json.loads(path.read_text(encoding="utf-8")))
    expected = {
        "schema_version",
        "service_name",
        "region",
        "environment_variables",
        "secrets",
        "secret_policy",
        "forbidden_environment_variables",
        "cloud_run_reserved_variables",
        "runtime_secret_accessor_required",
    }
    if set(payload) != expected:
        raise ValueError("La configuración runtime contiene campos desconocidos o ausentes.")
    definition = RuntimeConfigurationDefinition(
        schema_version=str(payload["schema_version"]),
        service_name=str(payload["service_name"]),
        region=str(payload["region"]),
        environment_variables=tuple(
            EnvironmentVariable(**item)
            for item in cast(list[dict[str, str]], payload["environment_variables"])
        ),
        secrets=tuple(
            SecretReference(**item)
            for item in cast(list[dict[str, str]], payload["secrets"])
        ),
        secret_policy=SecretPolicy(**cast(dict[str, Any], payload["secret_policy"])),
        forbidden_environment_variables=tuple(
            cast(list[str], payload["forbidden_environment_variables"])
        ),
        cloud_run_reserved_variables=tuple(
            cast(list[str], payload["cloud_run_reserved_variables"])
        ),
        runtime_secret_accessor_required=bool(
            payload["runtime_secret_accessor_required"]
        ),
    )
    _validate_definition(definition)
    return definition


def scan_repository_configuration(
    definition: RuntimeConfigurationDefinition,
    *,
    root: Path = PROJECT_ROOT,
) -> None:
    targets = (
        root / ".env.example",
        root / "cloudbuild.yaml",
        DEFINITION_PATH,
    )
    forbidden_assignments = tuple(
        re.compile(rf"(?m)^\s*{re.escape(name)}\s*[:=]\s*\S+")
        for name in definition.forbidden_environment_variables
    )
    for target in targets:
        if not target.is_file():
            raise RuntimeError(f"Falta el archivo de configuración inspeccionable: {target.name}")
        text = target.read_text(encoding="utf-8")
        if any(pattern.search(text) for pattern in forbidden_assignments):
            raise RuntimeError(f"Se detectó una credencial prohibida en {target.name}.")


def plan_runtime_configuration(
    project_id: str,
    *,
    gcloud: str = "gcloud",
) -> RuntimeConfigurationResult:
    definition = load_runtime_configuration()
    scan_repository_configuration(definition)
    return _result(
        "planned",
        project_id,
        definition,
        gcloud=gcloud,
        cloud_verified=False,
    )


def verify_runtime_configuration(
    project_id: str,
    *,
    gcloud: str = "gcloud",
    runner: Runner = subprocess.run,
) -> RuntimeConfigurationResult:
    definition = load_runtime_configuration()
    scan_repository_configuration(definition)
    result = _result(
        "verified",
        project_id,
        definition,
        gcloud=gcloud,
        cloud_verified=True,
    )
    response = runner(
        result.verify_command,
        check=True,
        capture_output=True,
        text=True,
    )
    service = cast(dict[str, Any], json.loads(response.stdout or "{}"))
    _assert_service_configuration(service, result, definition)
    return result


def _result(
    status: str,
    project_id: str,
    definition: RuntimeConfigurationDefinition,
    *,
    gcloud: str,
    cloud_verified: bool,
) -> RuntimeConfigurationResult:
    _validate_project_id(project_id)
    environment = definition.rendered_environment(project_id)
    runtime_email = load_identity_definition().email_for(project_id)
    env_argument = ",".join(f"{name}={value}" for name, value in environment.items())
    secret_arguments = tuple(
        f"{secret.environment_variable}={secret.secret_id}:{secret.version}"
        for secret in definition.secrets
    )
    deployment_arguments = (
        f"--set-env-vars={env_argument}",
        f"--service-account={runtime_email}",
    ) + (
        (f"--set-secrets={','.join(secret_arguments)}",)
        if secret_arguments
        else ()
    )
    return RuntimeConfigurationResult(
        status=status,
        project_id=project_id,
        service_name=definition.service_name,
        region=definition.region,
        runtime_email=runtime_email,
        environment=environment,
        secret_references=definition.secrets,
        deployment_arguments=deployment_arguments,
        verify_command=(
            gcloud,
            "run",
            "services",
            "describe",
            definition.service_name,
            f"--region={definition.region}",
            f"--project={project_id}",
            "--format=json",
        ),
        repository_scan_verified=True,
        cloud_verified=cloud_verified,
        configuration_applied=False,
        secret_payloads_created=False,
    )


def _assert_service_configuration(
    service: dict[str, Any],
    result: RuntimeConfigurationResult,
    definition: RuntimeConfigurationDefinition,
) -> None:
    template = cast(dict[str, Any], service.get("spec", {}).get("template", {}).get("spec", {}))
    if template.get("serviceAccountName") != result.runtime_email:
        raise RuntimeError("Cloud Run no utiliza la identidad runtime declarada.")
    containers = cast(list[dict[str, Any]], template.get("containers", []))
    if len(containers) != 1:
        raise RuntimeError("Cloud Run debe declarar exactamente un contenedor de ingreso.")
    actual_values: dict[str, str] = {}
    actual_secrets: dict[str, tuple[str, str]] = {}
    for item in cast(list[dict[str, Any]], containers[0].get("env", [])):
        name = str(item.get("name", ""))
        if "value" in item:
            actual_values[name] = str(item["value"])
        elif "valueFrom" in item:
            reference = cast(
                dict[str, Any], item.get("valueFrom", {}).get("secretKeyRef", {})
            )
            actual_secrets[name] = (
                str(reference.get("name", "")),
                str(reference.get("key", "")),
            )
    expected_secrets = {
        secret.environment_variable: (secret.secret_id, secret.version)
        for secret in definition.secrets
    }
    forbidden = (
        set(actual_values) | set(actual_secrets)
    ) & (
        set(definition.forbidden_environment_variables)
        | set(definition.cloud_run_reserved_variables)
    )
    if forbidden:
        raise RuntimeError(f"Cloud Run contiene variables prohibidas: {sorted(forbidden)}")
    if actual_values != result.environment:
        raise RuntimeError("Las variables Cloud Run no coinciden exactamente con la declaración.")
    if actual_secrets != expected_secrets:
        raise RuntimeError("Las referencias de secretos no coinciden con la declaración.")


def _validate_definition(definition: RuntimeConfigurationDefinition) -> None:
    if definition.schema_version != "1.0.0":
        raise ValueError("La versión de configuración runtime no está soportada.")
    if definition.service_name != "collaborative-taskmaster-studio":
        raise ValueError("El servicio Cloud Run declarado no es el esperado.")
    if definition.region != "us-central1":
        raise ValueError("La región runtime debe coincidir con la infraestructura.")
    variable_names = [variable.name for variable in definition.environment_variables]
    if len(variable_names) != len(set(variable_names)):
        raise ValueError("Las variables runtime no pueden estar duplicadas.")
    forbidden = set(definition.forbidden_environment_variables)
    reserved = set(definition.cloud_run_reserved_variables)
    if set(variable_names) & (forbidden | reserved):
        raise ValueError("La declaración contiene una variable prohibida o reservada.")
    for variable in definition.environment_variables:
        if (
            not VARIABLE_NAME_PATTERN.fullmatch(variable.name)
            or not variable.value_template
            or not variable.purpose.strip()
            or SENSITIVE_NAME_PATTERN.search(variable.name)
        ):
            raise ValueError("Una variable runtime no cumple el contrato seguro.")
        if set(re.findall(r"{([^{}]+)}", variable.value_template)) - {"project_id"}:
            raise ValueError("Una variable runtime contiene una plantilla desconocida.")
    policy = definition.secret_policy
    if (
        policy.provider != "google_secret_manager"
        or policy.allow_plaintext_values
        or not policy.require_numeric_version
        or policy.latest_alias_allowed
        or policy.delivery != "environment"
    ):
        raise ValueError("La política de secretos no cumple H10-07.")
    secret_names = [secret.environment_variable for secret in definition.secrets]
    if len(secret_names) != len(set(secret_names)) or set(secret_names) & set(variable_names):
        raise ValueError("Las referencias de secretos son ambiguas o duplicadas.")
    for secret in definition.secrets:
        if (
            not VARIABLE_NAME_PATTERN.fullmatch(secret.environment_variable)
            or not SECRET_ID_PATTERN.fullmatch(secret.secret_id)
            or not SECRET_VERSION_PATTERN.fullmatch(secret.version)
            or not secret.purpose.strip()
        ):
            raise ValueError("Una referencia de secreto no cumple el contrato seguro.")
    if definition.runtime_secret_accessor_required != bool(definition.secrets):
        raise ValueError("El permiso Secret Accessor debe coincidir con los secretos declarados.")
    required = {
        "GOOGLE_CLOUD_PROJECT",
        "GOOGLE_GENAI_USE_VERTEXAI",
        "STUDIO_ENABLE_VERTEX",
        "STUDIO_ENABLE_FIRESTORE",
        "STUDIO_GEMINI_MODEL",
    }
    if not required <= set(variable_names):
        raise ValueError("Faltan variables esenciales de producción.")


def _validate_project_id(project_id: str) -> None:
    if not PROJECT_ID_PATTERN.fullmatch(project_id):
        raise ValueError("El ID del proyecto Google Cloud no es válido.")
