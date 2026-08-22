"""Deterministic H3 interview catalog shared by local and future model modes."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class InterviewQuestion:
    id: str
    prompt: str
    reason: str
    target_fields: tuple[str, ...]
    answer_type: str = "free_text"


QUESTION_CATALOG: tuple[InterviewQuestion, ...] = (
    InterviewQuestion(
        id="ask_deadline_and_hours",
        prompt="¿Cuándo debe estar listo el resultado y cuánto tiempo puedes dedicar al proceso?",
        reason="Necesito ajustar el flujo al plazo y al tiempo realmente disponible.",
        target_fields=("deadline", "available_hours"),
    ),
    InterviewQuestion(
        id="ask_input_and_result",
        prompt="¿Qué información recibirá el agente y qué resultado exacto debe preparar?",
        reason="Esto define las entradas y los entregables verificables.",
        target_fields=("input_format", "success_criteria"),
    ),
    InterviewQuestion(
        id="ask_autonomy_and_approval",
        prompt="¿Puede enviar información o modificar otras aplicaciones? ¿Quién aprueba el resultado final?",
        reason=(
            "Estas decisiones establecen los límites de autonomía y las acciones que deben "
            "esperar confirmación."
        ),
        target_fields=("external_actions", "approval_owner"),
    ),
)


QUESTION_BY_ID = {question.id: question for question in QUESTION_CATALOG}
REQUIRED_BRIEFING_FIELDS: tuple[str, ...] = (
    "deadline",
    "available_hours",
    "input_format",
    "external_actions",
    "approval_owner",
    "success_criteria",
)
