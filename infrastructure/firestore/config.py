"""Validated, fail-closed configuration and client initialization for Firestore."""

from __future__ import annotations

import os
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal, Protocol, cast

from pydantic import BaseModel, ConfigDict

from infrastructure.firestore.provisioning import load_database_definition
from studio.domain.errors import DomainError

_PROJECT_ID = re.compile(r"^[a-z][a-z0-9-]{4,28}[a-z0-9]$")
_TRUE = frozenset({"1", "true", "yes", "on"})
_FALSE = frozenset({"0", "false", "no", "off", ""})
_CLOUD_SCOPE = "https://www.googleapis.com/auth/cloud-platform"


class FirestoreModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class FirestoreSettings(FirestoreModel):
    enabled: bool = False
    project: str | None = None
    database_id: str = "collaborative-taskmaster"
    location: str = "us-central1"
    transaction_max_attempts: int = 5
    demo_retention_days: int = 7

    @classmethod
    def from_environment(
        cls,
        environment: Mapping[str, str] | None = None,
    ) -> FirestoreSettings:
        values = environment if environment is not None else os.environ
        definition = load_database_definition()
        enabled = _boolean(
            values.get("STUDIO_ENABLE_FIRESTORE", "false"),
            "STUDIO_ENABLE_FIRESTORE",
        )
        project = values.get("GOOGLE_CLOUD_PROJECT", "").strip() or None
        database_id = values.get(
            "STUDIO_FIRESTORE_DATABASE", definition.database_id
        ).strip()
        location = values.get("STUDIO_FIRESTORE_LOCATION", definition.location).strip()
        transaction_max_attempts = _bounded_integer(
            values.get("STUDIO_FIRESTORE_TRANSACTION_MAX_ATTEMPTS", "5"),
            "STUDIO_FIRESTORE_TRANSACTION_MAX_ATTEMPTS",
            minimum=1,
            maximum=10,
        )
        demo_retention_days = _bounded_integer(
            values.get("STUDIO_FIRESTORE_DEMO_RETENTION_DAYS", "7"),
            "STUDIO_FIRESTORE_DEMO_RETENTION_DAYS",
            minimum=1,
            maximum=30,
            error_code="FIRESTORE_RETENTION_DAYS_INVALID",
        )
        if database_id != definition.database_id or location != definition.location:
            raise DomainError(
                "FIRESTORE_DECLARATION_MISMATCH",
                "La configuración Firestore no coincide con la declaración versionada.",
                context={
                    "expected_database": definition.database_id,
                    "expected_location": definition.location,
                },
            )
        if enabled and (project is None or not _PROJECT_ID.fullmatch(project)):
            raise DomainError(
                "FIRESTORE_PROJECT_INVALID",
                "GOOGLE_CLOUD_PROJECT debe contener un ID de proyecto válido.",
            )
        return cls(
            enabled=enabled,
            project=project,
            database_id=database_id,
            location=location,
            transaction_max_attempts=transaction_max_attempts,
            demo_retention_days=demo_retention_days,
        )


class FirestoreReadiness(FirestoreModel):
    status: Literal[
        "disabled",
        "ready",
        "missing_dependency",
        "adc_unavailable",
        "client_initialization_failed",
    ]
    configured: bool
    adc_available: bool
    client_initialized: bool
    database_verified: bool = False
    repository_active: bool = False
    cloud_calls_enabled: bool = False
    project: str | None
    database_id: str
    location: str
    credentials_source: Literal["none", "application_default"]
    message: str


@dataclass(frozen=True, slots=True)
class FirestoreRuntime:
    settings: FirestoreSettings
    readiness: FirestoreReadiness
    client: object | None = None


class CredentialsLoader(Protocol):
    def __call__(
        self,
        *,
        scopes: tuple[str, ...],
        quota_project_id: str | None,
    ) -> tuple[object, str | None]: ...


class ClientFactory(Protocol):
    def __call__(
        self,
        *,
        project: str,
        database: str,
        credentials: object,
    ) -> object: ...


def initialize_firestore(
    settings: FirestoreSettings,
    *,
    credentials_loader: CredentialsLoader | None = None,
    client_factory: ClientFactory | None = None,
) -> FirestoreRuntime:
    """Build a Firestore client without issuing a database RPC."""
    if not settings.enabled:
        return _runtime(
            settings,
            status="disabled",
            adc_available=False,
            client_initialized=False,
            credentials_source="none",
            message="Firestore está desactivado; el repositorio local permanece activo.",
        )

    loader = credentials_loader
    factory = client_factory
    if loader is None:
        try:
            import google.auth
        except ModuleNotFoundError:
            return _missing_dependency(settings)
        loader = cast(CredentialsLoader, google.auth.default)
    if factory is None:
        try:
            from google.cloud import firestore
        except (ImportError, ModuleNotFoundError):
            return _missing_dependency(settings)
        factory = cast(ClientFactory, firestore.Client)

    try:
        credentials, _ = loader(
            scopes=(_CLOUD_SCOPE,),
            quota_project_id=settings.project,
        )
    except Exception:
        return _runtime(
            settings,
            status="adc_unavailable",
            adc_available=False,
            client_initialized=False,
            credentials_source="none",
            message="ADC no está disponible; autentica gcloud sin desactivar TLS.",
        )
    if credentials is None:
        return _runtime(
            settings,
            status="adc_unavailable",
            adc_available=False,
            client_initialized=False,
            credentials_source="none",
            message="ADC no devolvió credenciales utilizables.",
        )

    assert settings.project is not None
    try:
        client = factory(
            project=settings.project,
            database=settings.database_id,
            credentials=credentials,
        )
    except Exception:
        return _runtime(
            settings,
            status="client_initialization_failed",
            adc_available=True,
            client_initialized=False,
            credentials_source="application_default",
            message="El cliente Firestore no pudo inicializarse; no se realizó ninguna consulta.",
        )
    return _runtime(
        settings,
        status="ready",
        adc_available=True,
        client_initialized=True,
        credentials_source="application_default",
        message="Cliente Firestore inicializado localmente; la base aún no fue consultada.",
        client=client,
    )


def _missing_dependency(settings: FirestoreSettings) -> FirestoreRuntime:
    return _runtime(
        settings,
        status="missing_dependency",
        adc_available=False,
        client_initialized=False,
        credentials_source="none",
        message='Instala el extra ".[firestore]" antes de activar Firestore.',
    )


def _runtime(
    settings: FirestoreSettings,
    *,
    status: Literal[
        "disabled",
        "ready",
        "missing_dependency",
        "adc_unavailable",
        "client_initialization_failed",
    ],
    adc_available: bool,
    client_initialized: bool,
    credentials_source: Literal["none", "application_default"],
    message: str,
    client: object | None = None,
) -> FirestoreRuntime:
    return FirestoreRuntime(
        settings=settings,
        readiness=FirestoreReadiness(
            status=status,
            configured=settings.enabled,
            adc_available=adc_available,
            client_initialized=client_initialized,
            project=settings.project,
            database_id=settings.database_id,
            location=settings.location,
            credentials_source=credentials_source,
            message=message,
        ),
        client=client,
    )


def _boolean(value: str, name: str) -> bool:
    normalized = value.strip().casefold()
    if normalized in _TRUE:
        return True
    if normalized in _FALSE:
        return False
    raise DomainError(
        "FIRESTORE_BOOLEAN_INVALID",
        f"{name} debe ser true o false.",
        context={"variable": name},
    )


def _bounded_integer(
    value: str,
    name: str,
    *,
    minimum: int,
    maximum: int,
    error_code: str = "FIRESTORE_TRANSACTION_ATTEMPTS_INVALID",
) -> int:
    try:
        parsed = int(value.strip())
    except ValueError as error:
        raise DomainError(
            error_code,
            f"{name} debe ser un número entero entre {minimum} y {maximum}.",
            context={"variable": name, "min": minimum, "max": maximum},
        ) from error
    if not minimum <= parsed <= maximum:
        raise DomainError(
            error_code,
            f"{name} debe estar entre {minimum} y {maximum}.",
            context={"variable": name, "min": minimum, "max": maximum},
        )
    return parsed
