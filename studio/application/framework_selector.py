"""Deterministic framework selection for generated Taskmaster agents."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

FrameworkId = Literal["google_adk", "genai_sdk", "antigravity", "genkit"]


class FrameworkRecommendation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    framework: FrameworkId
    label: str
    language: Literal["python", "typescript"]
    reason: str
    confidence: int = Field(ge=0, le=100)


_LABELS: dict[FrameworkId, str] = {
    "google_adk": "Google ADK",
    "genai_sdk": "Google Gen AI SDK",
    "antigravity": "Antigravity SDK",
    "genkit": "Genkit",
}


def select_framework(
    *,
    purpose: str = "",
    workflow: Iterable[str] = (),
    external_actions: Iterable[str] = (),
    inputs: Iterable[str] = (),
    outputs: Iterable[str] = (),
    constraints: Iterable[str] = (),
) -> FrameworkRecommendation:
    """Choose a framework from explicit project signals, never from model preference alone."""

    text = " ".join(
        [purpose, *workflow, *external_actions, *inputs, *outputs, *constraints]
    ).casefold()
    steps = tuple(workflow)
    actions = tuple(external_actions)

    workspace_read_terms = {
        "leer archivo", "lectura de archivo", "inspeccionar directorio", "carpeta del agente",
        "directorio del agente", "workspace read", "workspace.read", "documentos locales",
    }

    antigravity_terms = {
        "repositorio", "repository", "código", "codigo", "terminal", "shell", "archivo",
        "filesystem", "navegador", "browser", "workspace", "mcp", "subagente", "subagent",
        "desarrollo de software", "software development",
    }
    genkit_terms = {
        "firebase", "web", "móvil", "movil", "mobile", "saas", "api", "rag", "búsqueda",
        "busqueda", "search", "recomendación", "recomendacion", "recommendation", "streaming",
        "typescript", "full-stack", "fullstack",
    }
    genai_terms = {
        "clasificar", "classification", "extraer", "extraction", "resumir", "summarize",
        "transformar", "transformation", "analizar texto", "structured output", "json",
    }

    if any(term in text for term in workspace_read_terms) and not any(
        term in text
        for term in {"terminal", "shell", "ejecutar código", "ejecutar codigo", "desarrollo de software"}
    ):
        return _result(
            "google_adk", "python",
            "Necesita herramientas de lectura con alcance, políticas y trazabilidad explícitos.",
            90,
        )
    if any(term in text for term in antigravity_terms):
        return _result(
            "antigravity", "python",
            "Necesita operar sobre un entorno de trabajo, archivos o herramientas con políticas y control humano.",
            92,
        )
    if any(term in text for term in genkit_terms):
        return _result(
            "genkit", "typescript",
            "Encaja en una aplicación full-stack o API con flujos, observabilidad e integración web/Firebase.",
            88,
        )
    if len(steps) <= 2 and not actions and any(term in text for term in genai_terms):
        return _result(
            "genai_sdk", "python",
            "Es una capacidad de IA focalizada que puede implementarse con una integración ligera del modelo.",
            84,
        )
    return _result(
        "google_adk", "python",
        "Es un flujo de agente de varios pasos que se beneficia de herramientas, estado y orquestación explícita.",
        80,
    )


def _result(
    framework: FrameworkId,
    language: Literal["python", "typescript"],
    reason: str,
    confidence: int,
) -> FrameworkRecommendation:
    return FrameworkRecommendation(
        framework=framework,
        label=_LABELS[framework],
        language=language,
        reason=reason,
        confidence=confidence,
    )
