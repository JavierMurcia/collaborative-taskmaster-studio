"""Closed capability map for the Google ADK template set."""

from __future__ import annotations

from dataclasses import dataclass

from studio.domain.errors import DomainError
from studio.domain.models import TaskmasterSpecification


@dataclass(frozen=True, slots=True)
class GoogleAdkCapabilities:
    framework: str = "google_adk"
    language: str = "python"
    template_version: str = "1.0.0"
    supported_tool_modes: frozenset[str] = frozenset({"simulated", "read_only"})

    def validate(self, specification: TaskmasterSpecification) -> None:
        generation = specification.generation
        if generation.target_framework != self.framework or generation.language != self.language:
            raise DomainError(
                "GENERATOR_CAPABILITY_MISMATCH",
                "La revisión no es compatible con el generador Google ADK para Python.",
                context={
                    "framework": generation.target_framework,
                    "language": generation.language,
                },
            )
        if generation.template_version != self.template_version:
            raise DomainError(
                "GENERATOR_TEMPLATE_UNSUPPORTED",
                "La versión de plantilla solicitada no está disponible.",
                context={
                    "requested": generation.template_version,
                    "available": self.template_version,
                },
            )
        unsupported = sorted(
            tool.id for tool in specification.tools if tool.mode not in self.supported_tool_modes
        )
        if unsupported:
            raise DomainError(
                "GENERATOR_TOOL_MODE_UNSUPPORTED",
                "H6 solo genera herramientas simuladas o de solo lectura.",
                context={"tool_ids": unsupported},
            )
        secret_tools = sorted(tool.id for tool in specification.tools if tool.required_secret_refs)
        if secret_tools:
            raise DomainError(
                "GENERATOR_SECRETS_NOT_ALLOWED",
                "El generador local no incorpora herramientas que requieran secretos.",
                context={"tool_ids": secret_tools},
            )
