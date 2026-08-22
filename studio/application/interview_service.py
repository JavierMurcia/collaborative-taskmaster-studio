"""Deterministic collaborative interview use cases for milestone H3."""

from __future__ import annotations

import hashlib
import re
import unicodedata
from typing import Any, Literal

from pydantic import Field, ValidationError

from studio.application.briefing_generator import StructuredBriefingGenerator
from studio.application.fallback_policy import (
    decide_local_fallback,
    fallback_event_details,
)
from studio.application.interview_catalog import (
    QUESTION_BY_ID,
    QUESTION_CATALOG,
    REQUIRED_BRIEFING_FIELDS,
    InterviewQuestion,
)
from studio.application.interview_question_generator import (
    StructuredInterviewQuestionGenerator,
)
from studio.application.project_service import ProjectService
from studio.domain.enums import AuditEventType, ProjectState
from studio.domain.errors import (
    BriefingIncompleteError,
    DomainError,
    IdempotencyConflictError,
    InterviewAnswerError,
)
from studio.domain.models import (
    AuditEvent,
    Briefing,
    DomainModel,
    InterviewAnswerRecord,
    Project,
    ProjectSnapshot,
)
from studio.domain.transitions import transition_project
from studio.ports.clock import Clock
from studio.ports.model_gateway import model_metadata_details
from studio.ports.repositories import EventRepository, ProjectRepository


class BriefingNotes(DomainModel):
    objective: str
    deadline: str | None
    available_hours: int | None
    input_format: str | None
    outputs: list[str] = Field(default_factory=list)
    external_actions: Literal["none", "allowed", "requires_clarification"] | None
    approval_owner: str | None
    success_criteria: list[str] = Field(default_factory=list)
    missing_fields: list[str] = Field(default_factory=list)
    requires_clarification: list[str] = Field(default_factory=list)
    can_confirm: bool


class InterviewResult(DomainModel):
    snapshot: ProjectSnapshot
    notes: BriefingNotes
    next_question: dict[str, Any] | None


class InterviewService:
    def __init__(
        self,
        projects: ProjectRepository,
        events: EventRepository,
        clock: Clock,
        question_generator: StructuredInterviewQuestionGenerator | None = None,
        briefing_generator: StructuredBriefingGenerator | None = None,
        max_model_questions_per_project: int = 3,
    ) -> None:
        if not 0 <= max_model_questions_per_project <= 20:
            raise DomainError(
                "MODEL_QUESTION_LIMIT_INVALID",
                "El límite de preguntas del modelo debe estar entre 0 y 20.",
            )
        self._projects = projects
        self._events = events
        self._clock = clock
        self._question_generator = question_generator
        self._briefing_generator = briefing_generator
        self._max_model_questions_per_project = max_model_questions_per_project

    def start(
        self,
        project_id: str,
        *,
        owner_session_id: str,
        idempotency_key: str,
    ) -> InterviewResult:
        snapshot = self._authorized(project_id, owner_session_id)
        if snapshot.project.state is ProjectState.IDEA:
            changed = transition_project(snapshot.project, ProjectState.INTERVIEW)
            snapshot = self._projects.save(
                changed,
                expected_version=snapshot.version,
                idempotency_key=f"{idempotency_key}:start",
            )
            self._append_event(
                snapshot.project,
                AuditEventType.STATE_TRANSITIONED,
                owner_session_id,
                "Entrevista iniciada.",
                idempotency_key=f"{idempotency_key}:start_event",
                details={"from": ProjectState.IDEA.value, "to": ProjectState.INTERVIEW.value},
            )
        elif snapshot.project.state not in {
            ProjectState.INTERVIEW,
            ProjectState.BRIEFING_PENDING,
        }:
            raise InterviewAnswerError(
                "La entrevista no está disponible en el estado actual.",
                state=snapshot.project.state.value,
            )
        return self._result(snapshot, actor_id=owner_session_id)

    def record_answer(
        self,
        project_id: str,
        *,
        question_id: str,
        answer: str,
        owner_session_id: str,
        idempotency_key: str,
        values: dict[str, Any] | None = None,
    ) -> InterviewResult:
        snapshot = self._authorized(project_id, owner_session_id)
        if snapshot.project.state not in {
            ProjectState.INTERVIEW,
            ProjectState.BRIEFING_PENDING,
        }:
            raise InterviewAnswerError(
                "No se pueden registrar respuestas en el estado actual.",
                state=snapshot.project.state.value,
            )
        question = QUESTION_BY_ID.get(question_id)
        if question is None:
            raise InterviewAnswerError(
                "La pregunta no pertenece al catálogo.", question_id=question_id
            )
        clean_answer = answer.strip()
        if not clean_answer:
            raise InterviewAnswerError(
                "La respuesta no puede estar vacía.", question_id=question_id
            )

        replay = next(
            (
                item
                for item in snapshot.project.briefing.answer_history
                if item.operation_id == idempotency_key
            ),
            None,
        )
        if replay is not None:
            if replay.question_id != question_id or replay.answer != clean_answer:
                raise IdempotencyConflictError(idempotency_key)
            return self._result(snapshot, actor_id=owner_session_id)

        extraction_event: tuple[AuditEventType, str, dict[str, Any]] | None = None
        if values is not None:
            parsed = values
        else:
            parsed, extraction_event = self._extract_answer_values(
                snapshot.project.briefing,
                question,
                clean_answer,
            )
        parsed = {key: value for key, value in parsed.items() if not _is_missing(value)}
        _validate_target_values(question, parsed, allow_partial=True)
        briefing = _apply_values(snapshot.project.briefing, parsed)
        briefing.answer_history.append(
            InterviewAnswerRecord(
                operation_id=idempotency_key,
                question_id=question.id,
                target_fields=[
                    field for field in question.target_fields if field in parsed
                ],
                answer=clean_answer,
                values=parsed,
                recorded_at=self._clock.now(),
            )
        )
        project = snapshot.project.model_copy(update={"briefing": briefing}, deep=True)
        ready = not missing_fields(briefing)
        became_ready = ready and project.state is ProjectState.INTERVIEW
        if became_ready:
            project = transition_project(project, ProjectState.BRIEFING_PENDING)
        saved = self._projects.save(
            project,
            expected_version=snapshot.version,
            idempotency_key=f"{idempotency_key}:answer",
        )
        self._append_event(
            saved.project,
            AuditEventType.INTERVIEW_ANSWER_RECORDED,
            owner_session_id,
            "Respuesta registrada y notas del briefing actualizadas.",
            idempotency_key=f"{idempotency_key}:answer_event",
            details={"question_id": question.id, "fields": sorted(parsed)},
        )
        if extraction_event is not None:
            event_type, summary, details = extraction_event
            self._append_event(
                saved.project,
                event_type,
                owner_session_id,
                summary,
                idempotency_key=f"{idempotency_key}:briefing_model_event",
                details=details,
            )
        if became_ready:
            self._append_event(
                saved.project,
                AuditEventType.BRIEFING_READY,
                owner_session_id,
                "El briefing contiene los campos obligatorios y está listo para revisión.",
                idempotency_key=f"{idempotency_key}:ready_event",
                details={"missing_fields": []},
            )
        return self._result(saved, actor_id=owner_session_id)

    def _extract_answer_values(
        self,
        briefing: Briefing,
        question: InterviewQuestion,
        answer: str,
    ) -> tuple[dict[str, Any], tuple[AuditEventType, str, dict[str, Any]] | None]:
        if self._briefing_generator is None:
            return _parse_answer(question, answer, briefing), None
        try:
            generated = self._briefing_generator.generate(briefing, question, answer)
            _validate_target_values(question, generated.values)
            _apply_values(briefing, generated.values)
        except DomainError as error:
            decision = decide_local_fallback(
                "briefing_extraction",
                "local_parser",
                error.code,
                model_attempted=True,
            )
            return (
                _parse_answer(question, answer, briefing),
                (
                    AuditEventType.MODEL_FALLBACK_USED,
                    "Se utilizó el extractor local seguro para actualizar el briefing.",
                    fallback_event_details(
                        decision,
                        error=error,
                        extra={"question_id": question.id},
                    ),
                ),
            )
        except ValidationError:
            decision = decide_local_fallback(
                "briefing_extraction",
                "local_parser",
                "BRIEFING_VALUES_INVALID",
                model_attempted=True,
            )
            return (
                _parse_answer(question, answer, briefing),
                (
                    AuditEventType.MODEL_FALLBACK_USED,
                    "Se utilizó el extractor local seguro para actualizar el briefing.",
                    {
                        **fallback_event_details(
                            decision,
                            extra={"question_id": question.id},
                        ),
                        **model_metadata_details(generated.model_metadata),
                    },
                ),
            )
        metadata = generated.model_metadata
        return (
            generated.values,
            (
                AuditEventType.MODEL_GENERATION_COMPLETED,
                "Gemini extrajo valores estructurados para el briefing.",
                {
                    "operation": "briefing_extraction",
                    "question_id": question.id,
                    "fields": sorted(generated.values),
                    **model_metadata_details(metadata),
                },
            ),
        )

    def correct_field(
        self,
        project_id: str,
        *,
        field_name: str,
        value: Any,
        owner_session_id: str,
        idempotency_key: str,
    ) -> InterviewResult:
        snapshot = self._authorized(project_id, owner_session_id)
        if snapshot.project.state not in {
            ProjectState.INTERVIEW,
            ProjectState.BRIEFING_PENDING,
        }:
            raise InterviewAnswerError(
                "El briefing ya no admite correcciones de entrevista.",
                state=snapshot.project.state.value,
            )
        if field_name not in REQUIRED_BRIEFING_FIELDS:
            raise InterviewAnswerError("El campo no es editable en H3.", field=field_name)
        normalized = _normalize_field_value(field_name, value)
        previous = getattr(snapshot.project.briefing, field_name)
        if previous == normalized:
            return self._result(snapshot, actor_id=owner_session_id)

        briefing = Briefing.model_validate(
            {
                **snapshot.project.briefing.model_dump(mode="python"),
                field_name: normalized,
            }
        )
        briefing.answer_history.append(
            InterviewAnswerRecord(
                operation_id=idempotency_key,
                question_id=_correction_question_id(field_name),
                target_fields=[field_name],
                answer=str(normalized),
                values={field_name: normalized},
                recorded_at=self._clock.now(),
                correction=True,
            )
        )
        project = snapshot.project.model_copy(update={"briefing": briefing}, deep=True)
        missing = missing_fields(briefing)
        became_ready = not missing and project.state is ProjectState.INTERVIEW
        if missing and project.state is ProjectState.BRIEFING_PENDING:
            project = transition_project(project, ProjectState.INTERVIEW)
        elif became_ready:
            project = transition_project(project, ProjectState.BRIEFING_PENDING)
        saved = self._projects.save(
            project,
            expected_version=snapshot.version,
            idempotency_key=f"{idempotency_key}:correction",
        )
        self._append_event(
            saved.project,
            AuditEventType.INTERVIEW_ANSWER_CORRECTED,
            owner_session_id,
            "Una respuesta fue corregida y las notas se recalcularon.",
            idempotency_key=f"{idempotency_key}:correction_event",
            details={"field": field_name, "previous_value": previous, "new_value": normalized},
        )
        if became_ready:
            self._append_event(
                saved.project,
                AuditEventType.BRIEFING_READY,
                owner_session_id,
                "El briefing contiene los campos obligatorios y está listo para revisión.",
                idempotency_key=f"{idempotency_key}:ready_event",
                details={"missing_fields": []},
            )
        return self._result(saved, actor_id=owner_session_id)

    def confirm_briefing(
        self,
        project_id: str,
        *,
        owner_session_id: str,
        idempotency_key: str,
    ) -> InterviewResult:
        snapshot = self._authorized(project_id, owner_session_id)
        missing = missing_fields(snapshot.project.briefing)
        if missing:
            raise BriefingIncompleteError(missing)
        if snapshot.project.state is ProjectState.BRIEFING_CONFIRMED:
            return self._result(snapshot, actor_id=owner_session_id)
        if snapshot.project.state is not ProjectState.BRIEFING_PENDING:
            raise InterviewAnswerError(
                "El briefing solo puede confirmarse después de revisarlo.",
                state=snapshot.project.state.value,
            )
        briefing = snapshot.project.briefing.model_copy(
            update={
                "confirmed": True,
                "confirmed_by": owner_session_id,
                "confirmed_at": self._clock.now(),
            },
            deep=True,
        )
        project = snapshot.project.model_copy(update={"briefing": briefing}, deep=True)
        project = transition_project(project, ProjectState.BRIEFING_CONFIRMED)
        saved = self._projects.save(
            project,
            expected_version=snapshot.version,
            idempotency_key=f"{idempotency_key}:confirm",
        )
        self._append_event(
            saved.project,
            AuditEventType.BRIEFING_CONFIRMED,
            owner_session_id,
            "Briefing confirmado por una persona; el diseño ya puede comenzar.",
            idempotency_key=f"{idempotency_key}:confirm_event",
            details={"confirmed_by": owner_session_id},
        )
        return self._result(saved, actor_id=owner_session_id)

    def get_notes(self, project_id: str, *, owner_session_id: str) -> BriefingNotes:
        return build_notes(self._authorized(project_id, owner_session_id).project.briefing)

    def _authorized(self, project_id: str, owner_session_id: str) -> ProjectSnapshot:
        snapshot = self._projects.get(project_id, owner_session_id=owner_session_id)
        ProjectService.ensure_owner(snapshot, owner_session_id)
        return snapshot

    def _result(self, snapshot: ProjectSnapshot, *, actor_id: str) -> InterviewResult:
        question = next_question(snapshot.project.briefing)
        return InterviewResult(
            snapshot=snapshot,
            notes=build_notes(snapshot.project.briefing),
            next_question=(
                self._generated_question(snapshot, question, actor_id=actor_id)
                if question
                else None
            ),
        )

    def _generated_question(
        self,
        snapshot: ProjectSnapshot,
        question: InterviewQuestion,
        *,
        actor_id: str,
    ) -> dict[str, Any]:
        local = {
            **_question_payload(question, snapshot.project.briefing),
            "source": "local_catalog",
        }
        if self._question_generator is None:
            return local
        event_key = f"model-question:{snapshot.project.id}:{snapshot.version}:{question.id}"
        cached = self._cached_question(snapshot, question)
        if cached is not None:
            if _question_matches_scope(cached, question):
                return cached
            fallback = {
                **local,
                "source": "local_fallback",
                "fallback_code": "INTERVIEW_QUESTION_CACHE_INVALID",
            }
            decision = decide_local_fallback(
                "interview_question",
                "local_catalog",
                "INTERVIEW_QUESTION_CACHE_INVALID",
                model_attempted=False,
            )
            self._append_event(
                snapshot.project,
                AuditEventType.MODEL_FALLBACK_USED,
                actor_id,
                "La pregunta almacenada no era válida; se utilizó el catálogo local.",
                idempotency_key=f"{event_key}:cache-recovery",
                details=fallback_event_details(
                    decision,
                    extra={
                        "project_version": snapshot.version,
                        "question_id": question.id,
                        "question": fallback,
                    },
                ),
            )
            return fallback
        attempts = self._model_question_attempts(snapshot.project.id)
        if attempts >= self._max_model_questions_per_project:
            fallback = {
                **local,
                "source": "local_limit",
                "fallback_code": "MODEL_QUESTION_LIMIT_REACHED",
            }
            decision = decide_local_fallback(
                "interview_question",
                "local_catalog",
                "MODEL_QUESTION_LIMIT_REACHED",
                model_attempted=False,
            )
            self._append_event(
                snapshot.project,
                AuditEventType.MODEL_FALLBACK_USED,
                actor_id,
                "Se alcanzó el límite de preguntas del modelo; se utilizó el catálogo local.",
                idempotency_key=f"{event_key}:limit",
                details=fallback_event_details(
                    decision,
                    extra={
                        "project_version": snapshot.version,
                        "question_id": question.id,
                        "attempted_questions": attempts,
                        "max_model_questions_per_project": (
                            self._max_model_questions_per_project
                        ),
                        "question": fallback,
                    },
                ),
            )
            return fallback
        try:
            generated = self._question_generator.generate(snapshot.project.briefing, question)
        except DomainError as error:
            fallback = {
                **local,
                "source": "local_fallback",
                "fallback_code": error.code,
            }
            decision = decide_local_fallback(
                "interview_question",
                "local_catalog",
                error.code,
                model_attempted=True,
            )
            self._append_event(
                snapshot.project,
                AuditEventType.MODEL_FALLBACK_USED,
                actor_id,
                "Se utilizó la pregunta local segura.",
                idempotency_key=event_key,
                details=fallback_event_details(
                    decision,
                    error=error,
                    extra={
                        "project_version": snapshot.version,
                        "question_id": question.id,
                        "question": fallback,
                    },
                ),
            )
            return fallback
        payload = generated.model_dump(mode="json")
        self._append_event(
            snapshot.project,
            AuditEventType.MODEL_GENERATION_COMPLETED,
            actor_id,
            "Gemini generó una pregunta de entrevista estructurada.",
            idempotency_key=event_key,
            details={
                "operation": "interview_question",
                "project_version": snapshot.version,
                "question_id": question.id,
                **model_metadata_details(generated.model_metadata),
                "question": payload,
            },
        )
        return payload

    def _model_question_attempts(self, project_id: str) -> int:
        return sum(
            1
            for event in self._events.list_for_project(project_id)
            if event.details.get("operation") == "interview_question"
            and isinstance(event.details.get("model"), str)
        )

    def _cached_question(
        self,
        snapshot: ProjectSnapshot,
        question: InterviewQuestion,
    ) -> dict[str, Any] | None:
        eligible = {
            AuditEventType.MODEL_GENERATION_COMPLETED,
            AuditEventType.MODEL_FALLBACK_USED,
        }
        for event in reversed(self._events.list_for_project(snapshot.project.id)):
            if event.event_type not in eligible:
                continue
            if event.details.get("operation") != "interview_question":
                continue
            if event.details.get("project_version") != snapshot.version:
                continue
            if event.details.get("question_id") != question.id:
                continue
            cached = event.details.get("question")
            if isinstance(cached, dict):
                return dict(cached)
        return None

    def _append_event(
        self,
        project: Project,
        event_type: AuditEventType,
        actor_id: str,
        summary: str,
        *,
        idempotency_key: str,
        details: dict[str, Any],
    ) -> None:
        self._events.append(
            AuditEvent(
                id=_event_id(event_type.value, idempotency_key),
                project_id=project.id,
                event_type=event_type,
                actor_id=actor_id,
                summary=summary,
                occurred_at=self._clock.now(),
                details=details,
            ),
            idempotency_key=idempotency_key,
        )


def missing_fields(briefing: Briefing) -> list[str]:
    return [field for field in REQUIRED_BRIEFING_FIELDS if _is_missing(getattr(briefing, field))]


def next_question(briefing: Briefing) -> InterviewQuestion | None:
    missing = set(missing_fields(briefing))
    return next(
        (question for question in QUESTION_CATALOG if missing.intersection(question.target_fields)),
        None,
    )


def build_notes(briefing: Briefing) -> BriefingNotes:
    missing = missing_fields(briefing)
    clarification = (
        ["external_actions"] if briefing.external_actions == "requires_clarification" else []
    )
    return BriefingNotes(
        objective=briefing.goal or briefing.problem,
        deadline=briefing.deadline or None,
        available_hours=briefing.available_hours,
        input_format=briefing.input_format or None,
        outputs=list(briefing.outputs),
        external_actions=briefing.external_actions,
        approval_owner=briefing.approval_owner or None,
        success_criteria=list(briefing.success_criteria),
        missing_fields=missing,
        requires_clarification=clarification,
        can_confirm=not missing,
    )


def require_confirmed_briefing(project: Project) -> Briefing:
    """Reject entry into design until a person confirms the briefing."""
    if project.state is not ProjectState.BRIEFING_CONFIRMED or not project.briefing.confirmed:
        raise DomainError(
            "BRIEFING_NOT_CONFIRMED",
            "El diseño requiere un briefing confirmado por una persona.",
            context={"project_id": project.id, "state": project.state.value},
        )
    return project.briefing.model_copy(deep=True)


def _parse_answer(
    question: InterviewQuestion,
    answer: str,
    briefing: Briefing | None = None,
) -> dict[str, Any]:
    normalized = _plain(answer)
    if question.id == "ask_deadline_and_hours":
        hours = _extract_hours(normalized)
        deadline = _extract_deadline(normalized)
        if not deadline and re.search(
            r"\b(no se especifica|sin plazo|sin fecha|aun no hay fecha|todavia no hay fecha)\b",
            normalized,
        ):
            deadline = "Sin plazo definido"
        return {"deadline": deadline, "available_hours": hours}
    if question.id == "ask_input_and_result":
        outputs: list[str] = []
        if "plan" in normalized:
            outputs.append("Plan semanal")
        if "paquete" in normalized or "evidencia" in normalized:
            outputs.append("Paquete requisito-evidencia")
        academic = bool(outputs) or _is_academic_context(briefing)
        return {
            "input_format": (
                "Lista confirmada por la persona usuaria"
                if "lista" in normalized and not academic
                else (
                    "Lista escrita por el estudiante"
                    if "lista" in normalized
                    else answer.strip()
                )
            ),
            "outputs": outputs or [answer.strip()],
            "desired_result": answer.strip(),
            "success_criteria": (
                ["Cada requisito tiene evidencia"]
                if academic
                else ["El resultado usa únicamente información confirmada"]
            ),
        }
    if question.id == "ask_autonomy_and_approval":
        external_actions: str
        if re.search(r"\b(no|sin)\b.*\b(enviar|modificar|extern)", normalized):
            external_actions = "none"
        elif re.search(r"\b(puede|podra)\b.*\b(enviar|modificar)", normalized):
            external_actions = "allowed"
        else:
            external_actions = "requires_clarification"
        owner = _extract_approval_owner(answer, normalized)
        academic = _is_academic_context(briefing)
        return {
            "external_actions": external_actions,
            "approval_owner": owner,
            "approvals": [f"{owner} aprueba el resultado final"],
            "success_criteria": (
                [
                    "Cada requisito tiene una actividad",
                    "Cada requisito tiene evidencia",
                    f"{owner} aprueba el paquete",
                ]
                if academic
                else [
                    "El resultado cumple los criterios confirmados",
                    f"{owner} aprueba el resultado final",
                ]
            ),
        }
    raise InterviewAnswerError("No existe un analizador local para la pregunta.")


def _is_academic_context(briefing: Briefing | None) -> bool:
    if briefing is None:
        return False
    context = " ".join(
        [briefing.problem, briefing.goal, briefing.desired_result, *briefing.outputs]
    ).casefold()
    return any(
        marker in context
        for marker in (
            "académic",
            "academic",
            "estudiante",
            "plan semanal",
            "paquete requisito-evidencia",
            "entrega semanal",
        )
    )


def _extract_approval_owner(answer: str, normalized: str) -> str:
    if re.search(r"\b(yo|estudiante)\b", normalized):
        return "Estudiante"
    match = re.search(
        r"(?:el|la)\s+([a-záéíóúñ][a-záéíóúñ\s]{1,80}?)\s+aprueba",
        normalized,
    )
    if match:
        return match.group(1).strip().capitalize()
    return answer.strip()


def _validate_target_values(
    question: InterviewQuestion,
    values: dict[str, Any],
    *,
    allow_partial: bool = False,
) -> None:
    present_targets = [
        field for field in question.target_fields if not _is_missing(values.get(field))
    ]
    if allow_partial and present_targets:
        return
    missing_targets = [field for field in question.target_fields if _is_missing(values.get(field))]
    if missing_targets:
        raise InterviewAnswerError(
            "La respuesta no permitió completar los campos esperados.",
            question_id=question.id,
            missing_fields=missing_targets,
        )


def _apply_values(briefing: Briefing, values: dict[str, Any]) -> Briefing:
    allowed = set(Briefing.model_fields) - {
        "confirmed",
        "confirmed_by",
        "confirmed_at",
        "answer_history",
    }
    invalid = sorted(set(values) - allowed)
    if invalid:
        raise InterviewAnswerError("La respuesta contiene campos no permitidos.", fields=invalid)
    updates = dict(values)
    if "success_criteria" in updates:
        updates["success_criteria"] = _unique(
            [*briefing.success_criteria, *list(updates["success_criteria"])]
        )
    if "outputs" in updates:
        updates["outputs"] = _unique([*briefing.outputs, *list(updates["outputs"])])
    if "approvals" in updates:
        updates["approvals"] = _unique([*briefing.approvals, *list(updates["approvals"])])
    return Briefing.model_validate({**briefing.model_dump(mode="python"), **updates})


def _normalize_field_value(field_name: str, value: Any) -> Any:
    if field_name == "available_hours":
        try:
            return int(value)
        except (TypeError, ValueError) as error:
            raise InterviewAnswerError("Las horas disponibles deben ser un entero.") from error
    if field_name == "success_criteria":
        if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
            raise InterviewAnswerError("Los criterios de éxito deben ser una lista de textos.")
        return _unique(value)
    if field_name == "external_actions":
        if value not in {"none", "allowed", "requires_clarification", None}:
            raise InterviewAnswerError("El valor de acciones externas no es válido.")
        return value
    if not isinstance(value, str):
        raise InterviewAnswerError("El valor corregido debe ser texto.", field=field_name)
    return value.strip()


def _is_missing(value: Any) -> bool:
    return value is None or value == "" or value == []


def _extract_hours(answer: str) -> int | None:
    daily_match = re.search(r"(\d{1,3})\s*hor(?:a|as)?\s*(?:diarias?|al dia|por dia)", answer)
    if daily_match:
        daily_hours = int(daily_match.group(1))
        days = _extract_day_count(answer)
        return min(168, daily_hours * days) if days is not None else daily_hours
    match = re.search(r"(\d{1,3})\s*hor", answer)
    if match:
        return int(match.group(1))
    words = {"una": 1, "dos": 2, "tres": 3, "cuatro": 4, "cinco": 5, "seis": 6}
    hours = next((number for word, number in words.items() if f"{word} hora" in answer), None)
    if hours is not None:
        return hours

    minute_match = re.search(r"(\d{1,3})\s*min", answer)
    minute_words = {
        "diez": 10,
        "quince": 15,
        "veinte": 20,
        "treinta": 30,
        "cuarenta y cinco": 45,
        "sesenta": 60,
    }
    minutes = (
        int(minute_match.group(1))
        if minute_match
        else next(
            (number for word, number in minute_words.items() if f"{word} minuto" in answer),
            None,
        )
    )
    return max(1, (minutes + 59) // 60) if minutes is not None else None


def _extract_deadline(answer: str) -> str:
    if "viernes" in answer and re.search(r"\b(6|18)(?::00)?\b", answer):
        return "Viernes 18:00"
    match = re.search(r"(lunes|martes|miercoles|jueves|viernes|sabado|domingo)[^.,;]*", answer)
    if match:
        return match.group(0).strip().capitalize()
    days = _extract_day_count(answer)
    return f"En {days} {'día' if days == 1 else 'días'}" if days is not None else ""


def _extract_day_count(answer: str) -> int | None:
    numeric = re.search(r"\b(?:en|dentro de)\s+(\d{1,3})\s*dias?\b", answer)
    if numeric:
        return int(numeric.group(1))
    words = {
        "un": 1,
        "uno": 1,
        "dos": 2,
        "tres": 3,
        "cuatro": 4,
        "cinco": 5,
        "seis": 6,
        "siete": 7,
    }
    return next(
        (
            number
            for word, number in words.items()
            if re.search(rf"\b(?:en|dentro de)\s+{word}\s+dias?\b", answer)
        ),
        None,
    )


def _plain(value: str) -> str:
    decomposed = unicodedata.normalize("NFKD", value.casefold())
    return "".join(character for character in decomposed if not unicodedata.combining(character))


def _question_payload(
    question: InterviewQuestion,
    briefing: Briefing | None = None,
) -> dict[str, Any]:
    missing = [
        field
        for field in question.target_fields
        if briefing is None or _is_missing(getattr(briefing, field))
    ]
    prompt = question.prompt
    reason = question.reason
    if question.id == "ask_deadline_and_hours":
        if missing == ["available_hours"]:
            prompt = "¿Cuántas horas en total puedes dedicar al proceso?"
            reason = "Ya registré el plazo; solo falta estimar el tiempo disponible."
        elif missing == ["deadline"]:
            prompt = "¿Cuándo debe estar listo el resultado?"
            reason = "Ya registré el tiempo disponible; solo falta definir el plazo."
    return {
        "question_id": question.id,
        "question": prompt,
        "reason": reason,
        "target_fields": missing or list(question.target_fields),
        "answer_type": question.answer_type,
    }


def _question_matches_scope(payload: dict[str, Any], question: InterviewQuestion) -> bool:
    payload_targets = payload.get("target_fields")
    return (
        payload.get("question_id") == question.id
        and isinstance(payload_targets, list)
        and bool(payload_targets)
        and set(payload_targets).issubset(set(question.target_fields))
        and payload.get("answer_type") == question.answer_type
        and isinstance(payload.get("question"), str)
        and isinstance(payload.get("reason"), str)
    )


def _event_id(kind: str, key: str) -> str:
    safe_kind = re.sub(r"[^a-z0-9_]", "_", kind.casefold())[:38]
    digest = hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]
    return f"{safe_kind}_{digest}"


def _correction_question_id(field_name: str) -> str:
    return f"correct_{field_name}"[:64]


def _unique(values: list[str]) -> list[str]:
    return list(dict.fromkeys(value.strip() for value in values if value.strip()))
