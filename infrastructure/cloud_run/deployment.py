"""Declarative scale-to-zero Cloud Run deployment for H10-08."""

from __future__ import annotations

import json
import re
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

from infrastructure.cloud_run.configuration import plan_runtime_configuration
from infrastructure.cloud_run.identity import load_identity_definition

DEFINITION_PATH = Path(__file__).with_name("deployment.json")
PROJECT_ID_PATTERN = re.compile(r"^[a-z][a-z0-9-]{4,28}[a-z0-9]$")
IMAGE_DIGEST_PATTERN = re.compile(r"^[0-9a-f]{64}$")
SERVICE_NAME_PATTERN = re.compile(r"^[a-z][a-z0-9-]{0,47}[a-z0-9]$")
Runner = Callable[..., subprocess.CompletedProcess[str]]


@dataclass(frozen=True, slots=True)
class DeploymentDefinition:
    schema_version: str
    service_name: str
    region: str
    required_service: str
    platform: str
    repository_id: str
    image_name: str
    require_image_digest: bool
    service_min_instances: int
    service_max_instances: int
    revision_min_instances: int | None
    scaling_scope: str
    execution_environment: str
    ingress: str
    allow_unauthenticated: bool
    container_port: int
    container_concurrency: int
    traffic_percent: int
    labels: dict[str, str]
    description: str


@dataclass(frozen=True, slots=True)
class DeploymentResult:
    status: str
    project_id: str
    image_digest: str
    image_uri: str
    runtime_email: str
    definition: DeploymentDefinition
    prerequisite_commands: tuple[tuple[str, ...], ...]
    deploy_command: tuple[str, ...]
    verify_commands: tuple[tuple[str, ...], ...]
    cloud_verified: bool
    deployment_executed: bool
    service_ready: bool
    public_url: str | None

    def as_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "project_id": self.project_id,
            "image_digest": self.image_digest,
            "image_uri": self.image_uri,
            "runtime_email": self.runtime_email,
            "definition": {
                "schema_version": self.definition.schema_version,
                "service_name": self.definition.service_name,
                "region": self.definition.region,
                "required_service": self.definition.required_service,
                "platform": self.definition.platform,
                "repository_id": self.definition.repository_id,
                "image_name": self.definition.image_name,
                "require_image_digest": self.definition.require_image_digest,
                "service_min_instances": self.definition.service_min_instances,
                "service_max_instances": self.definition.service_max_instances,
                "revision_min_instances": self.definition.revision_min_instances,
                "scaling_scope": self.definition.scaling_scope,
                "execution_environment": self.definition.execution_environment,
                "ingress": self.definition.ingress,
                "allow_unauthenticated": self.definition.allow_unauthenticated,
                "container_port": self.definition.container_port,
                "container_concurrency": self.definition.container_concurrency,
                "traffic_percent": self.definition.traffic_percent,
                "labels": self.definition.labels,
                "description": self.definition.description,
            },
            "prerequisite_commands": [
                list(command) for command in self.prerequisite_commands
            ],
            "deploy_command": list(self.deploy_command),
            "verify_commands": [list(command) for command in self.verify_commands],
            "cloud_verified": self.cloud_verified,
            "deployment_executed": self.deployment_executed,
            "service_ready": self.service_ready,
            "public_url": self.public_url,
        }


def load_deployment_definition(
    path: Path = DEFINITION_PATH,
) -> DeploymentDefinition:
    payload = cast(dict[str, Any], json.loads(path.read_text(encoding="utf-8")))
    expected = {
        "schema_version",
        "service_name",
        "region",
        "required_service",
        "platform",
        "repository_id",
        "image_name",
        "require_image_digest",
        "service_min_instances",
        "service_max_instances",
        "revision_min_instances",
        "scaling_scope",
        "execution_environment",
        "ingress",
        "allow_unauthenticated",
        "container_port",
        "container_concurrency",
        "traffic_percent",
        "labels",
        "description",
    }
    if set(payload) != expected:
        raise ValueError("La declaración de despliegue contiene campos desconocidos o ausentes.")
    definition = DeploymentDefinition(**payload)
    _validate_definition(definition)
    return definition


def plan_deployment(
    project_id: str,
    image_digest: str,
    *,
    gcloud: str = "gcloud",
) -> DeploymentResult:
    definition = load_deployment_definition()
    return _result(
        "planned",
        project_id,
        image_digest,
        definition,
        gcloud=gcloud,
        cloud_verified=False,
        service_ready=False,
        public_url=None,
    )


def verify_deployment(
    project_id: str,
    image_digest: str,
    *,
    gcloud: str = "gcloud",
    runner: Runner = subprocess.run,
) -> DeploymentResult:
    definition = load_deployment_definition()
    planned = _result(
        "verified",
        project_id,
        image_digest,
        definition,
        gcloud=gcloud,
        cloud_verified=True,
        service_ready=False,
        public_url=None,
    )
    outputs = [
        runner(command, check=True, capture_output=True, text=True)
        for command in planned.verify_commands
    ]
    services = cast(list[dict[str, Any]], json.loads(outputs[0].stdout or "[]"))
    api = next(
        (
            item
            for item in services
            if item.get("config", {}).get("name") == definition.required_service
        ),
        {},
    )
    if api.get("state") != "ENABLED":
        raise RuntimeError("La API de Cloud Run no está habilitada.")
    service = cast(dict[str, Any], json.loads(outputs[1].stdout or "{}"))
    public_url = _assert_service(service, planned)
    policy = cast(dict[str, Any], json.loads(outputs[2].stdout or "{}"))
    _assert_public_invoker(policy)
    return DeploymentResult(
        status=planned.status,
        project_id=planned.project_id,
        image_digest=planned.image_digest,
        image_uri=planned.image_uri,
        runtime_email=planned.runtime_email,
        definition=planned.definition,
        prerequisite_commands=planned.prerequisite_commands,
        deploy_command=planned.deploy_command,
        verify_commands=planned.verify_commands,
        cloud_verified=True,
        deployment_executed=False,
        service_ready=True,
        public_url=public_url,
    )


def _result(
    status: str,
    project_id: str,
    image_digest: str,
    definition: DeploymentDefinition,
    *,
    gcloud: str,
    cloud_verified: bool,
    service_ready: bool,
    public_url: str | None,
) -> DeploymentResult:
    _validate_project_id(project_id)
    _validate_image_digest(image_digest)
    runtime_configuration = plan_runtime_configuration(project_id, gcloud=gcloud)
    runtime_email = load_identity_definition().email_for(project_id)
    image_uri = (
        f"{definition.region}-docker.pkg.dev/{project_id}/"
        f"{definition.repository_id}/{definition.image_name}@sha256:{image_digest}"
    )
    labels = ",".join(f"{key}={value}" for key, value in sorted(definition.labels.items()))
    deploy_command = (
        gcloud,
        "run",
        "deploy",
        definition.service_name,
        f"--image={image_uri}",
        f"--region={definition.region}",
        f"--platform={definition.platform}",
        f"--service-account={runtime_email}",
        f"--min={definition.service_min_instances}",
        f"--max={definition.service_max_instances}",
        "--allow-unauthenticated",
        f"--ingress={definition.ingress}",
        f"--execution-environment={definition.execution_environment}",
        f"--port={definition.container_port}",
        f"--concurrency={definition.container_concurrency}",
        f"--labels={labels}",
        f"--description={definition.description}",
        *runtime_configuration.deployment_arguments[:1],
        f"--project={project_id}",
        "--quiet",
    )
    return DeploymentResult(
        status=status,
        project_id=project_id,
        image_digest=image_digest,
        image_uri=image_uri,
        runtime_email=runtime_email,
        definition=definition,
        prerequisite_commands=(
            (
                gcloud,
                "services",
                "enable",
                definition.required_service,
                f"--project={project_id}",
                "--quiet",
            ),
        ),
        deploy_command=deploy_command,
        verify_commands=(
            (
                gcloud,
                "services",
                "list",
                "--enabled",
                f"--project={project_id}",
                f"--filter=config.name={definition.required_service}",
                "--format=json",
            ),
            (
                gcloud,
                "run",
                "services",
                "describe",
                definition.service_name,
                f"--region={definition.region}",
                f"--project={project_id}",
                "--format=json",
            ),
            (
                gcloud,
                "run",
                "services",
                "get-iam-policy",
                definition.service_name,
                f"--region={definition.region}",
                f"--project={project_id}",
                "--format=json",
            ),
        ),
        cloud_verified=cloud_verified,
        deployment_executed=False,
        service_ready=service_ready,
        public_url=public_url,
    )


def _assert_service(service: dict[str, Any], result: DeploymentResult) -> str:
    definition = result.definition
    metadata = cast(dict[str, Any], service.get("metadata", {}))
    spec = cast(dict[str, Any], service.get("spec", {}))
    template = cast(dict[str, Any], spec.get("template", {}))
    template_metadata = cast(dict[str, Any], template.get("metadata", {}))
    template_spec = cast(dict[str, Any], template.get("spec", {}))
    status = cast(dict[str, Any], service.get("status", {}))
    containers = cast(list[dict[str, Any]], template_spec.get("containers", []))
    if metadata.get("name") != definition.service_name:
        raise RuntimeError("El nombre del servicio Cloud Run no coincide.")
    labels = cast(dict[str, str], metadata.get("labels", {}))
    if any(labels.get(key) != value for key, value in definition.labels.items()):
        raise RuntimeError("Las etiquetas Cloud Run no coinciden con la declaración.")
    annotations = cast(dict[str, str], metadata.get("annotations", {}))
    # Cloud Run omits the service annotation when zero is selected because zero
    # is the platform default. An explicit non-zero value is still drift.
    if annotations.get("run.googleapis.com/minScale", "0") != "0":
        raise RuntimeError("Cloud Run no tiene min instances de servicio igual a cero.")
    if annotations.get("run.googleapis.com/maxScale") != str(
        definition.service_max_instances
    ):
        raise RuntimeError("Cloud Run no tiene el máximo de instancias declarado.")
    if annotations.get("run.googleapis.com/ingress") != definition.ingress:
        raise RuntimeError("El ingress Cloud Run no coincide.")
    revision_annotations = cast(dict[str, str], template_metadata.get("annotations", {}))
    if "autoscaling.knative.dev/minScale" in revision_annotations:
        raise RuntimeError("No debe mezclarse escalado mínimo de servicio y revisión.")
    if revision_annotations.get("run.googleapis.com/execution-environment") != definition.execution_environment:
        raise RuntimeError("El entorno de ejecución Cloud Run no coincide.")
    if template_spec.get("serviceAccountName") != result.runtime_email:
        raise RuntimeError("Cloud Run no utiliza la identidad runtime declarada.")
    if template_spec.get("containerConcurrency") != definition.container_concurrency:
        raise RuntimeError("La concurrencia del contenedor Cloud Run no coincide.")
    if len(containers) != 1 or containers[0].get("image") != result.image_uri:
        raise RuntimeError("Cloud Run no utiliza el digest de imagen declarado.")
    ports = cast(list[dict[str, Any]], containers[0].get("ports", []))
    if len(ports) != 1 or ports[0].get("containerPort") != definition.container_port:
        raise RuntimeError("El puerto del contenedor Cloud Run no coincide.")
    conditions = cast(list[dict[str, Any]], status.get("conditions", []))
    ready = any(
        condition.get("type") == "Ready" and condition.get("status") == "True"
        for condition in conditions
    )
    if not ready or not status.get("latestReadyRevisionName"):
        raise RuntimeError("La revisión Cloud Run no está lista.")
    traffic = cast(list[dict[str, Any]], status.get("traffic", []))
    if not any(
        item.get("latestRevision") is True
        and item.get("percent") == definition.traffic_percent
        for item in traffic
    ):
        raise RuntimeError("La revisión más reciente no recibe el tráfico declarado.")
    url = str(status.get("url", ""))
    if not url.startswith("https://"):
        raise RuntimeError("Cloud Run no informa una URL HTTPS pública.")
    return url


def _assert_public_invoker(policy: dict[str, Any]) -> None:
    bindings = cast(list[dict[str, Any]], policy.get("bindings", []))
    public = [
        binding
        for binding in bindings
        if binding.get("role") == "roles/run.invoker"
        and "allUsers" in cast(list[str], binding.get("members", []))
        and binding.get("condition") is None
    ]
    if len(public) != 1:
        raise RuntimeError("El servicio no permite la invocación pública declarada.")


def _validate_definition(definition: DeploymentDefinition) -> None:
    if definition.schema_version != "1.0.0":
        raise ValueError("La versión de despliegue no está soportada.")
    if not SERVICE_NAME_PATTERN.fullmatch(definition.service_name):
        raise ValueError("El nombre del servicio Cloud Run no es válido.")
    if (
        definition.region != "us-central1"
        or definition.required_service != "run.googleapis.com"
        or definition.platform != "managed"
        or definition.repository_id != "collaborative-taskmaster"
        or definition.image_name != "studio"
        or not definition.require_image_digest
    ):
        raise ValueError("El destino Cloud Run no coincide con la infraestructura declarada.")
    if (
        definition.service_min_instances != 0
        or definition.service_max_instances != 1
        or definition.revision_min_instances is not None
        or definition.scaling_scope != "service"
    ):
        raise ValueError("H10-08 exige scale-to-zero únicamente a nivel de servicio.")
    if (
        definition.execution_environment != "gen2"
        or definition.ingress != "all"
        or not definition.allow_unauthenticated
        or definition.container_port != 8080
        or definition.container_concurrency != 1
        or definition.traffic_percent != 100
    ):
        raise ValueError("La configuración pública inicial de Cloud Run no es válida.")
    if not definition.labels or any(
        not key or not value for key, value in definition.labels.items()
    ):
        raise ValueError("El despliegue requiere etiquetas trazables.")
    if not definition.description.strip():
        raise ValueError("El despliegue requiere una descripción.")


def _validate_project_id(project_id: str) -> None:
    if not PROJECT_ID_PATTERN.fullmatch(project_id):
        raise ValueError("El ID del proyecto Google Cloud no es válido.")


def _validate_image_digest(image_digest: str) -> None:
    if not IMAGE_DIGEST_PATTERN.fullmatch(image_digest):
        raise ValueError("El digest SHA-256 de la imagen no es válido.")
