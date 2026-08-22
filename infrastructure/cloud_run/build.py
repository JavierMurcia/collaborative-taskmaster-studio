"""Reproducible Artifact Registry and Cloud Build declaration."""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, cast

DEFINITION_PATH = Path(__file__).with_name("build-pipeline.json")
PROJECT_ROOT = Path(__file__).resolve().parents[2]
PROJECT_ID_PATTERN = re.compile(r"^[a-z][a-z0-9-]{4,28}[a-z0-9]$")
IMAGE_TAG_PATTERN = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.-]{6,127}$")
EXPECTED_SERVICES = frozenset(
    {"artifactregistry.googleapis.com", "cloudbuild.googleapis.com"}
)
EXPECTED_BINDINGS = frozenset(
    {
        ("repository", "roles/artifactregistry.writer"),
        ("project", "roles/logging.logWriter"),
        ("source_bucket", "roles/storage.objectViewer"),
    }
)
Runner = Callable[..., subprocess.CompletedProcess[str]]


@dataclass(frozen=True, slots=True)
class RepositoryDefinition:
    repository_id: str
    format: str
    mode: str
    description: str
    immutable_tags: bool
    vulnerability_scanning: bool


@dataclass(frozen=True, slots=True)
class BuilderIdentityDefinition:
    account_id: str
    display_name: str
    description: str
    user_managed_keys_allowed: bool

    def email_for(self, project_id: str) -> str:
        _validate_project_id(project_id)
        return f"{self.account_id}@{project_id}.iam.gserviceaccount.com"


@dataclass(frozen=True, slots=True)
class BuilderBinding:
    scope: str
    role: str


@dataclass(frozen=True, slots=True)
class BuildPipelineDefinition:
    schema_version: str
    region: str
    required_services: tuple[str, ...]
    repository: RepositoryDefinition
    builder_identity: BuilderIdentityDefinition
    builder_bindings: tuple[BuilderBinding, ...]
    cloudbuild_config: str
    cloudbuild_config_sha256: str
    logging: str
    require_explicit_image_tag: bool
    forbidden_image_tags: tuple[str, ...]

    def config_path(self) -> Path:
        return PROJECT_ROOT / self.cloudbuild_config


@dataclass(frozen=True, slots=True)
class BuildPipelineResult:
    status: str
    project_id: str
    image_tag: str
    image_uri: str
    builder_email: str
    definition: BuildPipelineDefinition
    provision_commands: tuple[tuple[str, ...], ...]
    submit_command: tuple[str, ...]
    verify_commands: tuple[tuple[str, ...], ...]
    local_config_verified: bool
    cloud_verified: bool
    resources_applied: bool
    build_submitted: bool

    def as_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "project_id": self.project_id,
            "image_tag": self.image_tag,
            "image_uri": self.image_uri,
            "builder_email": self.builder_email,
            "definition": {
                "schema_version": self.definition.schema_version,
                "region": self.definition.region,
                "required_services": list(self.definition.required_services),
                "repository": asdict(self.definition.repository),
                "builder_identity": asdict(self.definition.builder_identity),
                "builder_bindings": [
                    asdict(binding) for binding in self.definition.builder_bindings
                ],
                "cloudbuild_config": self.definition.cloudbuild_config,
                "cloudbuild_config_sha256": self.definition.cloudbuild_config_sha256,
                "logging": self.definition.logging,
                "require_explicit_image_tag": (
                    self.definition.require_explicit_image_tag
                ),
                "forbidden_image_tags": list(
                    self.definition.forbidden_image_tags
                ),
            },
            "provision_commands": [
                list(command) for command in self.provision_commands
            ],
            "submit_command": list(self.submit_command),
            "verify_commands": [list(command) for command in self.verify_commands],
            "local_config_verified": self.local_config_verified,
            "cloud_verified": self.cloud_verified,
            "resources_applied": self.resources_applied,
            "build_submitted": self.build_submitted,
        }


def load_build_definition(
    path: Path = DEFINITION_PATH,
) -> BuildPipelineDefinition:
    payload = cast(dict[str, Any], json.loads(path.read_text(encoding="utf-8")))
    expected = {
        "schema_version",
        "region",
        "required_services",
        "repository",
        "builder_identity",
        "builder_bindings",
        "cloudbuild_config",
        "cloudbuild_config_sha256",
        "logging",
        "require_explicit_image_tag",
        "forbidden_image_tags",
    }
    if set(payload) != expected:
        raise ValueError("La declaración de construcción tiene campos desconocidos o ausentes.")
    definition = BuildPipelineDefinition(
        schema_version=str(payload["schema_version"]),
        region=str(payload["region"]),
        required_services=tuple(cast(list[str], payload["required_services"])),
        repository=RepositoryDefinition(
            **cast(dict[str, Any], payload["repository"])
        ),
        builder_identity=BuilderIdentityDefinition(
            **cast(dict[str, Any], payload["builder_identity"])
        ),
        builder_bindings=tuple(
            BuilderBinding(**binding)
            for binding in cast(list[dict[str, str]], payload["builder_bindings"])
        ),
        cloudbuild_config=str(payload["cloudbuild_config"]),
        cloudbuild_config_sha256=str(payload["cloudbuild_config_sha256"]),
        logging=str(payload["logging"]),
        require_explicit_image_tag=bool(payload["require_explicit_image_tag"]),
        forbidden_image_tags=tuple(cast(list[str], payload["forbidden_image_tags"])),
    )
    _validate_definition(definition)
    return definition


def verify_local_build_config(definition: BuildPipelineDefinition) -> None:
    config_path = definition.config_path()
    if not config_path.is_file():
        raise RuntimeError("No existe la configuración declarada de Cloud Build.")
    digest = hashlib.sha256(_canonical_config_bytes(config_path.read_bytes())).hexdigest()
    if digest != definition.cloudbuild_config_sha256:
        raise RuntimeError("cloudbuild.yaml no coincide con su huella declarada.")
    text = config_path.read_text(encoding="utf-8")
    required_fragments = (
        "id: unit-tests",
        "python -m pytest -q",
        "id: build-image",
        "id: push-image",
        "${_IMAGE_TAG}",
        "logging: CLOUD_LOGGING_ONLY",
        "substitutionOption: MUST_MATCH",
        "taskmaster-studio-builder@$PROJECT_ID.iam.gserviceaccount.com",
    )
    if any(fragment not in text for fragment in required_fragments):
        raise RuntimeError("cloudbuild.yaml no cumple el contrato H10-06.")
    if ":latest" in text:
        raise RuntimeError("cloudbuild.yaml no puede publicar una etiqueta móvil latest.")


def _canonical_config_bytes(content: bytes) -> bytes:
    """Return stable bytes across Windows and Linux checkouts."""
    return content.replace(b"\r\n", b"\n")


def plan_build_pipeline(
    project_id: str,
    image_tag: str,
    *,
    gcloud: str = "gcloud",
) -> BuildPipelineResult:
    definition = load_build_definition()
    verify_local_build_config(definition)
    return _result(
        "planned",
        project_id,
        image_tag,
        definition,
        gcloud=gcloud,
        cloud_verified=False,
    )


def verify_build_pipeline(
    project_id: str,
    image_tag: str,
    *,
    gcloud: str = "gcloud",
    runner: Runner = subprocess.run,
) -> BuildPipelineResult:
    definition = load_build_definition()
    verify_local_build_config(definition)
    result = _result(
        "verified",
        project_id,
        image_tag,
        definition,
        gcloud=gcloud,
        cloud_verified=True,
    )
    outputs = [_run(runner, command) for command in result.verify_commands]
    _assert_services(outputs[:2], definition)
    _assert_builder(json.loads(outputs[2].stdout or "{}"), result)
    if json.loads(outputs[3].stdout or "[]"):
        raise RuntimeError("La cuenta de construcción tiene claves de usuario.")
    _assert_repository(json.loads(outputs[4].stdout or "{}"), result)
    _assert_exact_binding(
        json.loads(outputs[5].stdout or "{}"),
        result.builder_email,
        "roles/artifactregistry.writer",
    )
    _assert_exact_binding(
        json.loads(outputs[6].stdout or "{}"),
        result.builder_email,
        "roles/logging.logWriter",
    )
    _assert_exact_binding(
        json.loads(outputs[7].stdout or "{}"),
        result.builder_email,
        "roles/storage.objectViewer",
    )
    return result


def _result(
    status: str,
    project_id: str,
    image_tag: str,
    definition: BuildPipelineDefinition,
    *,
    gcloud: str,
    cloud_verified: bool,
) -> BuildPipelineResult:
    _validate_project_id(project_id)
    _validate_image_tag(image_tag, definition)
    email = definition.builder_identity.email_for(project_id)
    image_uri = (
        f"{definition.region}-docker.pkg.dev/{project_id}/"
        f"{definition.repository.repository_id}/studio:{image_tag}"
    )
    return BuildPipelineResult(
        status=status,
        project_id=project_id,
        image_tag=image_tag,
        image_uri=image_uri,
        builder_email=email,
        definition=definition,
        provision_commands=_provision_commands(
            project_id, definition, gcloud=gcloud
        ),
        submit_command=(
            gcloud,
            "builds",
            "submit",
            "--config=cloudbuild.yaml",
            f"--project={project_id}",
            f"--substitutions=_IMAGE_TAG={image_tag}",
            ".",
        ),
        verify_commands=_verify_commands(project_id, definition, gcloud=gcloud),
        local_config_verified=True,
        cloud_verified=cloud_verified,
        resources_applied=False,
        build_submitted=False,
    )


def _provision_commands(
    project_id: str,
    definition: BuildPipelineDefinition,
    *,
    gcloud: str,
) -> tuple[tuple[str, ...], ...]:
    repository = definition.repository
    email = definition.builder_identity.email_for(project_id)
    member = f"serviceAccount:{email}"
    return (
        (
            gcloud,
            "services",
            "enable",
            *definition.required_services,
            f"--project={project_id}",
            "--quiet",
        ),
        (
            gcloud,
            "iam",
            "service-accounts",
            "create",
            definition.builder_identity.account_id,
            f"--project={project_id}",
            f"--display-name={definition.builder_identity.display_name}",
            f"--description={definition.builder_identity.description}",
            "--quiet",
        ),
        (
            gcloud,
            "artifacts",
            "repositories",
            "create",
            repository.repository_id,
            "--repository-format=docker",
            f"--location={definition.region}",
            f"--description={repository.description}",
            "--immutable-tags",
            "--disable-vulnerability-scanning",
            f"--project={project_id}",
            "--quiet",
        ),
        (
            gcloud,
            "artifacts",
            "repositories",
            "add-iam-policy-binding",
            repository.repository_id,
            f"--location={definition.region}",
            f"--project={project_id}",
            f"--member={member}",
            "--role=roles/artifactregistry.writer",
            "--quiet",
        ),
        (
            gcloud,
            "projects",
            "add-iam-policy-binding",
            project_id,
            f"--member={member}",
            "--role=roles/logging.logWriter",
            "--condition=None",
            "--quiet",
        ),
        (
            gcloud,
            "storage",
            "buckets",
            "add-iam-policy-binding",
            f"gs://{project_id}_cloudbuild",
            f"--project={project_id}",
            f"--member={member}",
            "--role=roles/storage.objectViewer",
            "--quiet",
        ),
    )


def _verify_commands(
    project_id: str,
    definition: BuildPipelineDefinition,
    *,
    gcloud: str,
) -> tuple[tuple[str, ...], ...]:
    email = definition.builder_identity.email_for(project_id)
    service_commands = tuple(
        (
            gcloud,
            "services",
            "list",
            "--enabled",
            f"--project={project_id}",
            f"--filter=config.name={service}",
            "--format=json",
        )
        for service in definition.required_services
    )
    return service_commands + (
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
        (
            gcloud,
            "artifacts",
            "repositories",
            "describe",
            definition.repository.repository_id,
            f"--location={definition.region}",
            f"--project={project_id}",
            "--format=json",
        ),
        (
            gcloud,
            "artifacts",
            "repositories",
            "get-iam-policy",
            definition.repository.repository_id,
            f"--location={definition.region}",
            f"--project={project_id}",
            "--format=json",
        ),
        (
            gcloud,
            "projects",
            "get-iam-policy",
            project_id,
            "--format=json",
        ),
        (
            gcloud,
            "storage",
            "buckets",
            "get-iam-policy",
            f"gs://{project_id}_cloudbuild",
            f"--project={project_id}",
            "--format=json",
        ),
    )


def _assert_services(
    outputs: Sequence[subprocess.CompletedProcess[str]],
    definition: BuildPipelineDefinition,
) -> None:
    states: dict[str, Any] = {}
    for output in outputs:
        payload = cast(list[dict[str, Any]], json.loads(output.stdout or "[]"))
        states.update(
            {
                str(item.get("config", {}).get("name", "")): item.get("state")
                for item in payload
            }
        )
    drift = {
        service: states.get(service)
        for service in definition.required_services
        if states.get(service) != "ENABLED"
    }
    if drift:
        raise RuntimeError(f"APIs requeridas no habilitadas: {json.dumps(drift)}")


def _assert_builder(actual: dict[str, Any], result: BuildPipelineResult) -> None:
    expected = result.definition.builder_identity
    fields = {
        "email": result.builder_email,
        "displayName": expected.display_name,
        "description": expected.description,
        "disabled": False,
    }
    drift = {
        key: {"expected": value, "actual": actual.get(key)}
        for key, value in fields.items()
        if actual.get(key) != value
    }
    if drift:
        raise RuntimeError(f"Identidad de construcción divergente: {json.dumps(drift)}")


def _assert_repository(actual: dict[str, Any], result: BuildPipelineResult) -> None:
    expected = result.definition.repository
    fields = {
        "format": expected.format,
        "mode": expected.mode,
        "description": expected.description,
        "dockerConfig.immutableTags": expected.immutable_tags,
        "vulnerabilityScanningConfig.enablement": "DISABLED",
    }
    flattened = {
        "format": actual.get("format"),
        "mode": actual.get("mode"),
        "description": actual.get("description"),
        "dockerConfig.immutableTags": actual.get("dockerConfig", {}).get(
            "immutableTags"
        ),
        "vulnerabilityScanningConfig.enablement": actual.get(
            "vulnerabilityScanningConfig", {}
        ).get("enablement"),
    }
    drift = {
        key: {"expected": value, "actual": flattened.get(key)}
        for key, value in fields.items()
        if flattened.get(key) != value
    }
    if drift:
        raise RuntimeError(f"Repositorio Artifact Registry divergente: {json.dumps(drift)}")


def _assert_exact_binding(policy: dict[str, Any], member_email: str, role: str) -> None:
    member = f"serviceAccount:{member_email}"
    matching = [
        binding
        for binding in cast(list[dict[str, Any]], policy.get("bindings", []))
        if member in cast(list[str], binding.get("members", []))
    ]
    if (
        len(matching) != 1
        or matching[0].get("role") != role
        or matching[0].get("condition") is not None
    ):
        raise RuntimeError(f"Falta el binding mínimo exacto {role} para {member_email}.")


def _run(
    runner: Runner,
    command: Sequence[str],
) -> subprocess.CompletedProcess[str]:
    return runner(command, check=True, capture_output=True, text=True)


def _validate_definition(definition: BuildPipelineDefinition) -> None:
    if definition.schema_version != "1.0.0":
        raise ValueError("La versión del pipeline no está soportada.")
    if frozenset(definition.required_services) != EXPECTED_SERVICES:
        raise ValueError("Las APIs declaradas no son exactamente las requeridas.")
    repository = definition.repository
    if (
        definition.region != "us-central1"
        or repository.repository_id != "collaborative-taskmaster"
        or repository.format != "DOCKER"
        or repository.mode != "STANDARD_REPOSITORY"
        or not repository.immutable_tags
        or repository.vulnerability_scanning
    ):
        raise ValueError("El repositorio no cumple el contrato reproducible H10-06.")
    identity = definition.builder_identity
    if identity.account_id != "taskmaster-studio-builder":
        raise ValueError("La identidad de construcción no es la esperada.")
    if identity.user_managed_keys_allowed:
        raise ValueError("La identidad de construcción no puede usar claves de usuario.")
    bindings = [(binding.scope, binding.role) for binding in definition.builder_bindings]
    if len(bindings) != len(EXPECTED_BINDINGS) or frozenset(bindings) != EXPECTED_BINDINGS:
        raise ValueError("Los permisos de construcción no son exactamente los mínimos.")
    if definition.logging != "CLOUD_LOGGING_ONLY":
        raise ValueError("La cuenta personalizada requiere logs en Cloud Logging.")
    if not definition.require_explicit_image_tag:
        raise ValueError("La etiqueta de imagen debe ser explícita.")
    if "latest" not in definition.forbidden_image_tags:
        raise ValueError("La etiqueta latest debe estar prohibida.")
    if not re.fullmatch(r"[0-9a-f]{64}", definition.cloudbuild_config_sha256):
        raise ValueError("La huella de cloudbuild.yaml no es válida.")


def _validate_project_id(project_id: str) -> None:
    if not PROJECT_ID_PATTERN.fullmatch(project_id):
        raise ValueError("El ID del proyecto Google Cloud no es válido.")


def _validate_image_tag(tag: str, definition: BuildPipelineDefinition) -> None:
    if not IMAGE_TAG_PATTERN.fullmatch(tag):
        raise ValueError("La etiqueta de imagen no es válida ni suficientemente específica.")
    if tag.lower() in definition.forbidden_image_tags:
        raise ValueError("La etiqueta de imagen móvil está prohibida.")
