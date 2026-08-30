"""Universal conversational profile and intent routing for built Taskmasters."""

from __future__ import annotations

from typing import Literal, NamedTuple

from pydantic import BaseModel, ConfigDict

from studio.domain.models import TaskmasterSpecification

ConversationIntent = Literal["conversation", "clarification", "execution", "approval"]

_EXECUTION_MARKERS = (
    "analiza",
    "compara",
    "crea",
    "ejecuta",
    "extrae",
    "genera",
    "haz ",
    "organiza",
    "prepara",
    "procesa",
    "resume",
    "revisa los",
)
_APPROVAL_MARKERS = (
    "apruebo",
    "aprobado",
    "autorizo",
    "confirmo la ejecución",
    "puedes ejecutar",
)
_CONTINUATION_MARKERS = {
    "adelante",
    "continua",
    "continúa",
    "hazlo",
    "procede",
    "si",
    "sí",
}
_CONVERSATION_MARKERS = (
    "hola",
    "buenas",
    "qué puedes hacer",
    "que puedes hacer",
    "cómo puedes ayudar",
    "como puedes ayudar",
    "explícame",
    "explicame",
)


class ConversationProfile(BaseModel):
    """User-facing identity derived from the approved contract, never invented at runtime."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str
    role: str
    capabilities: tuple[str, ...]
    limitations: tuple[str, ...]
    required_inputs: tuple[str, ...]
    suggested_prompts: tuple[str, ...]


class IntentDecision(NamedTuple):
    intent: ConversationIntent
    current_request: str
    reason: str


def build_conversation_profile(
    specification: TaskmasterSpecification,
) -> ConversationProfile:
    capabilities = _unique(
        [
            *(f"Preparar {item.name}: {item.description}" for item in specification.outputs),
            *(
                f"{tool.name}: {tool.description}"
                for tool in specification.tools
                if tool.mode in {"read_only", "simulated"}
            ),
        ],
        limit=8,
    )
    limitations = _unique(
        [
            *specification.mission.scope_out,
            *(
                f"{tool.name} requiere aprobación antes de producir efectos externos."
                for tool in specification.tools
                if tool.mode == "write"
            ),
        ],
        limit=8,
    )
    if not limitations:
        limitations = ("No realizará acciones fuera de su misión aprobada.",)
    required_inputs = tuple(item.name for item in specification.inputs if item.required)
    suggestions = _unique(
        [
            "Explícame qué puedes hacer y cuáles son tus límites.",
            *(f"Ayúdame a preparar {item.name}." for item in specification.outputs[:2]),
            *(
                f"{step.name}."
                for step in specification.workflow.steps
                if step.action_type in {"reason", "tool"}
            ),
        ],
        limit=4,
    )
    return ConversationProfile(
        name=specification.metadata.name,
        role=specification.mission.goal,
        capabilities=capabilities or ("Orientar sobre su misión aprobada.",),
        limitations=limitations,
        required_inputs=required_inputs,
        suggested_prompts=suggestions,
    )


def route_intent(
    specification: TaskmasterSpecification,
    message: str,
    *,
    evidence_available: bool = False,
) -> IntentDecision:
    current = current_request(message)
    normalized = " ".join(current.casefold().split())
    has_history = current != message.strip()

    if any(marker in normalized for marker in _APPROVAL_MARKERS):
        return IntentDecision("approval", current, "El usuario expresó una aprobación explícita.")

    if normalized in _CONTINUATION_MARKERS:
        if has_history and _history_requests_execution(message):
            return IntentDecision(
                "execution", current, "Continúa una tarea concreta presente en el historial."
            )
        return IntentDecision(
            "clarification",
            current,
            "No existe una tarea pendiente suficientemente concreta para continuar.",
        )

    execution_requested = any(marker in normalized for marker in _EXECUTION_MARKERS)
    if execution_requested:
        needs_files = any(
            item.required and item.data_type == "file" for item in specification.inputs
        )
        mentions_remote_source = "drive" in normalized or "archivo" in normalized
        if needs_files and not evidence_available and not mentions_remote_source:
            return IntentDecision(
                "clarification", current, "Faltan archivos obligatorios para ejecutar la tarea."
            )
        return IntentDecision("execution", current, "Solicita producir un resultado concreto.")

    if (
        any(marker in normalized for marker in _CONVERSATION_MARKERS)
        or "?" in current
        or "¿" in current
    ):
        return IntentDecision("conversation", current, "Es una consulta conversacional.")

    return IntentDecision(
        "conversation",
        current,
        "No contiene una orden inequívoca de ejecución.",
    )


def conversational_fallback(
    profile: ConversationProfile,
    decision: IntentDecision,
) -> str:
    if decision.intent == "clarification":
        inputs = ", ".join(profile.required_inputs) or "la información necesaria"
        return (
            f"Puedo ayudarte como {profile.name}, pero antes necesito confirmar {inputs}. "
            "Indícame qué resultado quieres obtener y adjunta o señala las entradas que debo usar."
        )
    if decision.intent == "approval":
        return (
            "Entiendo tu aprobación, pero no hay una acción externa pendiente identificada en "
            "este momento. Dime qué resultado deseas aprobar o ejecutar y verificaré sus límites."
        )
    if decision.intent == "execution":
        return (
            f"Entendí que quieres que {profile.name} ejecute esta tarea: “{decision.current_request}”. "
            "No pude completar el procesamiento inteligente en este intento. Conservé las entradas "
            "y no realicé ninguna acción externa; puedes intentarlo nuevamente sin volver a cargarlas."
        )

    capabilities = "\n".join(f"- {item}" for item in profile.capabilities[:4])
    limitations = "\n".join(f"- {item}" for item in profile.limitations[:3])
    suggestions = "\n".join(f"- {item}" for item in profile.suggested_prompts[:3])
    return (
        f"Soy {profile.name}. Mi enfoque es {profile.role}\n\n"
        f"Puedo ayudarte a:\n{capabilities}\n\n"
        f"Límites actuales:\n{limitations}\n\n"
        f"Puedes comenzar con algo como:\n{suggestions}"
    )


def current_request(message: str) -> str:
    marker = "\n\nSolicitud actual:\n"
    if marker in message:
        return message.rsplit(marker, 1)[-1].strip()
    return message.strip()


def _history_requests_execution(message: str) -> bool:
    history = message.split("\n\nSolicitud actual:\n", 1)[0].casefold()
    return any(marker in history for marker in _EXECUTION_MARKERS)


def _unique(values: list[str], *, limit: int) -> tuple[str, ...]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        cleaned = " ".join(str(value).split()).strip()
        key = cleaned.casefold()
        if not cleaned or key in seen:
            continue
        seen.add(key)
        result.append(cleaned)
        if len(result) >= limit:
            break
    return tuple(result)
