"""Fail-closed Cloud Storage configuration without startup network calls."""

from __future__ import annotations

import os
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal, Protocol, cast

from pydantic import BaseModel, ConfigDict, Field

from studio.domain.errors import DomainError

_BUCKET = re.compile(r"^[a-z0-9][a-z0-9._-]{1,220}[a-z0-9]$")
_PREFIX = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9/_-]{0,127}$")
_TRUE = frozenset({"1", "true", "yes", "on"})
_FALSE = frozenset({"0", "false", "no", "off", ""})
_CLOUD_SCOPE = "https://www.googleapis.com/auth/cloud-platform"


class CloudStorageModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class CloudStorageSettings(CloudStorageModel):
    enabled: bool = False
    project: str | None = None
    bucket: str | None = None
    prefix: str = "taskmaster-projects"
    max_files: int = Field(default=500, ge=1, le=5000)
    max_total_bytes: int = Field(default=50_000_000, ge=1, le=500_000_000)

    @classmethod
    def from_environment(
        cls, environment: Mapping[str, str] | None = None
    ) -> CloudStorageSettings:
        values = environment if environment is not None else os.environ
        enabled = _boolean(values.get("STUDIO_ENABLE_CLOUD_STORAGE", "false"))
        project = values.get("GOOGLE_CLOUD_PROJECT", "").strip() or None
        bucket = values.get("STUDIO_PROJECTS_BUCKET", "").strip() or None
        prefix = values.get("STUDIO_PROJECTS_BUCKET_PREFIX", "taskmaster-projects").strip().strip("/")
        if enabled and (bucket is None or not _BUCKET.fullmatch(bucket)):
            raise DomainError(
                "CLOUD_STORAGE_BUCKET_INVALID",
                "STUDIO_PROJECTS_BUCKET debe identificar un bucket válido.",
            )
        if not _PREFIX.fullmatch(prefix):
            raise DomainError(
                "CLOUD_STORAGE_PREFIX_INVALID",
                "STUDIO_PROJECTS_BUCKET_PREFIX contiene caracteres no permitidos.",
            )
        return cls(
            enabled=enabled,
            project=project,
            bucket=bucket,
            prefix=prefix,
            max_files=_integer(values.get("STUDIO_PROJECTS_MAX_FILES", "500"), 1, 5000),
            max_total_bytes=_integer(
                values.get("STUDIO_PROJECTS_MAX_TOTAL_BYTES", "50000000"),
                1,
                500_000_000,
            ),
        )


class CloudStorageReadiness(CloudStorageModel):
    status: Literal["disabled", "ready", "missing_dependency", "adc_unavailable", "client_initialization_failed"]
    configured: bool
    client_initialized: bool
    bucket: str | None
    prefix: str
    message: str


@dataclass(frozen=True, slots=True)
class CloudStorageRuntime:
    settings: CloudStorageSettings
    readiness: CloudStorageReadiness
    client: object | None = None


class CredentialsLoader(Protocol):
    def __call__(self, *, scopes: tuple[str, ...], quota_project_id: str | None) -> tuple[object, str | None]: ...


class ClientFactory(Protocol):
    def __call__(self, *, project: str | None, credentials: object) -> object: ...


def initialize_cloud_storage(
    settings: CloudStorageSettings,
    *,
    credentials_loader: CredentialsLoader | None = None,
    client_factory: ClientFactory | None = None,
) -> CloudStorageRuntime:
    if not settings.enabled:
        return _runtime(settings, "disabled", False, "Cloud Storage está desactivado; projects/ permanece local.")
    loader = credentials_loader
    factory = client_factory
    if loader is None:
        try:
            import google.auth
        except ModuleNotFoundError:
            return _runtime(settings, "missing_dependency", False, "Falta instalar el extra cloud.")
        loader = cast(CredentialsLoader, google.auth.default)
    if factory is None:
        try:
            from google.cloud import storage
        except (ImportError, ModuleNotFoundError):
            return _runtime(settings, "missing_dependency", False, "Falta instalar google-cloud-storage.")
        factory = cast(ClientFactory, storage.Client)
    try:
        credentials, detected_project = loader(scopes=(_CLOUD_SCOPE,), quota_project_id=settings.project)
    except Exception:
        return _runtime(settings, "adc_unavailable", False, "ADC no está disponible para Cloud Storage.")
    try:
        client = factory(project=settings.project or detected_project, credentials=credentials)
    except Exception:
        return _runtime(settings, "client_initialization_failed", False, "No se pudo inicializar Cloud Storage.")
    return _runtime(settings, "ready", True, "Cliente Cloud Storage inicializado.", client)


def _runtime(
    settings: CloudStorageSettings,
    status: Literal["disabled", "ready", "missing_dependency", "adc_unavailable", "client_initialization_failed"],
    initialized: bool,
    message: str,
    client: object | None = None,
) -> CloudStorageRuntime:
    return CloudStorageRuntime(
        settings,
        CloudStorageReadiness(
            status=status,
            configured=settings.enabled,
            client_initialized=initialized,
            bucket=settings.bucket,
            prefix=settings.prefix,
            message=message,
        ),
        client,
    )


def _boolean(value: str) -> bool:
    normalized = value.strip().casefold()
    if normalized in _TRUE:
        return True
    if normalized in _FALSE:
        return False
    raise DomainError("CLOUD_STORAGE_BOOLEAN_INVALID", "STUDIO_ENABLE_CLOUD_STORAGE debe ser true o false.")


def _integer(value: str, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except ValueError as error:
        raise DomainError("CLOUD_STORAGE_LIMIT_INVALID", "El límite de almacenamiento debe ser entero.") from error
    if not minimum <= parsed <= maximum:
        raise DomainError("CLOUD_STORAGE_LIMIT_INVALID", "El límite de almacenamiento está fuera del rango permitido.")
    return parsed

