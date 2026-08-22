"""Validated, fail-closed configuration for Vertex AI and ADC."""

from __future__ import annotations

import os
import re
from collections.abc import Mapping
from typing import Literal, Protocol, cast

from pydantic import BaseModel, ConfigDict, Field

from studio.domain.errors import DomainError

_PROJECT_ID = re.compile(r"^[a-z][a-z0-9-]{4,28}[a-z0-9]$")
_LOCATION = re.compile(r"^[a-z][a-z0-9-]{1,31}$")
_TRUE = frozenset({"1", "true", "yes", "on"})
_FALSE = frozenset({"0", "false", "no", "off", ""})
_CLOUD_SCOPE = "https://www.googleapis.com/auth/cloud-platform"


class VertexModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class VertexSettings(VertexModel):
    enabled: bool = False
    model_questions_enabled: bool = False
    model_briefing_enabled: bool = False
    model_specification_enabled: bool = False
    model_revision_enabled: bool = False
    use_vertex_ai: bool = False
    project: str | None = None
    location: str = "global"
    model: str = "gemini-3.7-flash"
    api_version: Literal["v1"] = "v1"
    max_model_output_tokens: int = Field(default=8_192, ge=64, le=8_192)
    max_model_questions_per_project: int = Field(default=3, ge=0, le=20)

    @classmethod
    def from_environment(cls, environment: Mapping[str, str] | None = None) -> VertexSettings:
        values = environment if environment is not None else os.environ
        enabled = _boolean(values.get("STUDIO_ENABLE_VERTEX", "false"), "STUDIO_ENABLE_VERTEX")
        model_questions_enabled = _boolean(
            values.get("STUDIO_ENABLE_MODEL_QUESTIONS", "false"),
            "STUDIO_ENABLE_MODEL_QUESTIONS",
        )
        model_briefing_enabled = _boolean(
            values.get("STUDIO_ENABLE_MODEL_BRIEFING", "false"),
            "STUDIO_ENABLE_MODEL_BRIEFING",
        )
        model_specification_enabled = _boolean(
            values.get("STUDIO_ENABLE_MODEL_SPECIFICATION", "false"),
            "STUDIO_ENABLE_MODEL_SPECIFICATION",
        )
        model_revision_enabled = _boolean(
            values.get("STUDIO_ENABLE_MODEL_REVISION", "false"),
            "STUDIO_ENABLE_MODEL_REVISION",
        )
        use_vertex_ai = _boolean(
            values.get("GOOGLE_GENAI_USE_VERTEXAI", "false"),
            "GOOGLE_GENAI_USE_VERTEXAI",
        )
        project = values.get("GOOGLE_CLOUD_PROJECT", "").strip() or None
        location = values.get("GOOGLE_CLOUD_LOCATION", "global").strip()
        model = values.get("STUDIO_GEMINI_MODEL", "gemini-3.7-flash").strip()
        api_version = values.get("STUDIO_VERTEX_API_VERSION", "v1").strip()
        max_model_output_tokens = _integer(
            values.get("STUDIO_MAX_MODEL_OUTPUT_TOKENS", "8192"),
            "STUDIO_MAX_MODEL_OUTPUT_TOKENS",
            minimum=64,
            maximum=8_192,
        )
        max_model_questions_per_project = _integer(
            values.get("STUDIO_MAX_MODEL_QUESTIONS_PER_PROJECT", "3"),
            "STUDIO_MAX_MODEL_QUESTIONS_PER_PROJECT",
            minimum=0,
            maximum=20,
        )
        if enabled:
            if not use_vertex_ai:
                raise DomainError(
                    "VERTEX_MODE_NOT_CONFIRMED",
                    "GOOGLE_GENAI_USE_VERTEXAI debe ser true para activar Vertex AI.",
                )
            if project is None or not _PROJECT_ID.fullmatch(project):
                raise DomainError(
                    "VERTEX_PROJECT_INVALID",
                    "GOOGLE_CLOUD_PROJECT debe contener un ID de proyecto válido.",
                )
            if any(values.get(key, "").strip() for key in ("GOOGLE_API_KEY", "GEMINI_API_KEY")):
                raise DomainError(
                    "VERTEX_API_KEY_FORBIDDEN",
                    "El modo Vertex utiliza ADC; elimina las API keys del entorno.",
                )
        if model_questions_enabled and not enabled:
            raise DomainError(
                "MODEL_QUESTIONS_REQUIRE_VERTEX",
                "STUDIO_ENABLE_MODEL_QUESTIONS requiere STUDIO_ENABLE_VERTEX=true.",
            )
        if model_briefing_enabled and not enabled:
            raise DomainError(
                "MODEL_BRIEFING_REQUIRES_VERTEX",
                "STUDIO_ENABLE_MODEL_BRIEFING requiere STUDIO_ENABLE_VERTEX=true.",
            )
        if model_specification_enabled and not enabled:
            raise DomainError(
                "MODEL_SPECIFICATION_REQUIRES_VERTEX",
                "STUDIO_ENABLE_MODEL_SPECIFICATION requiere STUDIO_ENABLE_VERTEX=true.",
            )
        if model_revision_enabled and not enabled:
            raise DomainError(
                "MODEL_REVISION_REQUIRES_VERTEX",
                "STUDIO_ENABLE_MODEL_REVISION requiere STUDIO_ENABLE_VERTEX=true.",
            )
        if not _LOCATION.fullmatch(location):
            raise DomainError("VERTEX_LOCATION_INVALID", "GOOGLE_CLOUD_LOCATION no es válida.")
        if not model or len(model) > 120:
            raise DomainError("VERTEX_MODEL_INVALID", "STUDIO_GEMINI_MODEL no es válido.")
        if api_version != "v1":
            raise DomainError(
                "VERTEX_API_VERSION_INVALID",
                "H8 requiere el endpoint estable v1 de Vertex AI.",
            )
        return cls(
            enabled=enabled,
            model_questions_enabled=model_questions_enabled,
            model_briefing_enabled=model_briefing_enabled,
            model_specification_enabled=model_specification_enabled,
            model_revision_enabled=model_revision_enabled,
            use_vertex_ai=use_vertex_ai,
            project=project,
            location=location,
            model=model,
            api_version="v1",
            max_model_output_tokens=max_model_output_tokens,
            max_model_questions_per_project=max_model_questions_per_project,
        )


class VertexReadiness(VertexModel):
    status: Literal["disabled", "ready", "missing_dependency", "adc_unavailable"]
    configured: bool
    adc_available: bool
    project: str | None
    location: str
    model: str
    api_version: Literal["v1"]
    credentials_source: Literal["none", "application_default"]
    cloud_calls_enabled: bool = False
    message: str


class CredentialsLoader(Protocol):
    def __call__(
        self, *, scopes: tuple[str, ...], quota_project_id: str | None
    ) -> tuple[object, str | None]: ...


def inspect_vertex_readiness(
    settings: VertexSettings,
    *,
    credentials_loader: CredentialsLoader | None = None,
) -> VertexReadiness:
    """Inspect local ADC without refreshing tokens or contacting Google Cloud."""
    if not settings.enabled:
        return _readiness(
            settings,
            status="disabled",
            adc_available=False,
            credentials_source="none",
            message="Vertex AI está desactivado; se utilizará el fallback local.",
        )
    loader = credentials_loader
    if loader is None:
        try:
            import google.auth
        except ModuleNotFoundError:
            return _readiness(
                settings,
                status="missing_dependency",
                adc_available=False,
                credentials_source="none",
                message='Instala el extra ".[vertex]" antes de activar Vertex AI.',
            )
        loader = cast(CredentialsLoader, google.auth.default)
    try:
        credentials, _ = loader(
            scopes=(_CLOUD_SCOPE,),
            quota_project_id=settings.project,
        )
    except Exception:
        return _readiness(
            settings,
            status="adc_unavailable",
            adc_available=False,
            credentials_source="none",
            message="ADC no está disponible; ejecuta gcloud auth application-default login.",
        )
    if credentials is None:
        return _readiness(
            settings,
            status="adc_unavailable",
            adc_available=False,
            credentials_source="none",
            message="ADC no devolvió credenciales utilizables.",
        )
    return _readiness(
        settings,
        status="ready",
        adc_available=True,
        credentials_source="application_default",
        message="ADC y la configuración de Vertex AI están listos; no se realizó ninguna llamada.",
    )


def _readiness(
    settings: VertexSettings,
    *,
    status: Literal["disabled", "ready", "missing_dependency", "adc_unavailable"],
    adc_available: bool,
    credentials_source: Literal["none", "application_default"],
    message: str,
) -> VertexReadiness:
    return VertexReadiness(
        status=status,
        configured=settings.enabled,
        adc_available=adc_available,
        project=settings.project,
        location=settings.location,
        model=settings.model,
        api_version=settings.api_version,
        credentials_source=credentials_source,
        message=message,
    )


def _boolean(value: str, name: str) -> bool:
    normalized = value.strip().casefold()
    if normalized in _TRUE:
        return True
    if normalized in _FALSE:
        return False
    raise DomainError(
        "VERTEX_BOOLEAN_INVALID",
        f"{name} debe ser true o false.",
        context={"variable": name},
    )


def _integer(value: str, name: str, *, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value.strip())
    except ValueError as error:
        raise DomainError(
            "MODEL_LIMIT_INVALID",
            f"{name} debe ser un número entero entre {minimum} y {maximum}.",
            context={"variable": name},
        ) from error
    if not minimum <= parsed <= maximum:
        raise DomainError(
            "MODEL_LIMIT_INVALID",
            f"{name} debe estar entre {minimum} y {maximum}.",
            context={"variable": name, "minimum": minimum, "maximum": maximum},
        )
    return parsed
