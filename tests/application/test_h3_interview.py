from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from infrastructure.local.clock import FrozenClock
from infrastructure.local.repositories import InMemoryRepository, JsonLocalRepository
from studio.application.interview_catalog import QUESTION_CATALOG
from studio.application.interview_service import InterviewService, require_confirmed_briefing
from studio.application.project_service import ProjectService
from studio.domain.enums import AuditEventType, ProjectState
from studio.domain.errors import BriefingIncompleteError, DomainError, ProjectAccessDeniedError

NOW = datetime(2026, 8, 13, 16, 0, tzinfo=UTC)
PROJECT_ID = "academic_delivery_project"
OWNER = "demo_user"
DESCRIPTION = (
    "Necesito un agente que me ayude a organizar cada semana los requisitos de mi "
    "proyecto final y compruebe que no olvide ninguna evidencia."
)
FIXTURE_DIRECTORY = Path(__file__).resolve().parents[1] / "fixtures" / "demo"


def services(repository: InMemoryRepository) -> tuple[ProjectService, InterviewService]:
    clock = FrozenClock(NOW)
    return (
        ProjectService(repository, repository, clock),
        InterviewService(repository, repository, clock),
    )


def create_and_start(
    repository: InMemoryRepository,
) -> tuple[ProjectService, InterviewService]:
    projects, interview = services(repository)
    projects.create_project(
        project_id=PROJECT_ID,
        name="Coordinador de entrega académica",
        description=DESCRIPTION,
        owner_session_id=OWNER,
        idempotency_key="create-demo",
    )
    interview.start(PROJECT_ID, owner_session_id=OWNER, idempotency_key="start-demo")
    return projects, interview


def complete_interview(interview: InterviewService) -> None:
    first = interview.record_answer(
        PROJECT_ID,
        question_id="ask_deadline_and_hours",
        answer=(
            "Debe quedar listo el viernes a las 6:00 p. m. y puedo dedicar seis horas "
            "durante la semana."
        ),
        owner_session_id=OWNER,
        idempotency_key="answer-one",
    )
    assert first.next_question is not None
    assert first.next_question["question_id"] == "ask_input_and_result"
    second = interview.record_answer(
        PROJECT_ID,
        question_id="ask_input_and_result",
        answer=(
            "Los escribiré en una lista. El agente debe producir un plan semanal y un "
            "paquete que relacione cada requisito con su evidencia."
        ),
        owner_session_id=OWNER,
        idempotency_key="answer-two",
    )
    assert second.next_question is not None
    assert second.next_question["question_id"] == "ask_autonomy_and_approval"
    third = interview.record_answer(
        PROJECT_ID,
        question_id="ask_autonomy_and_approval",
        answer="Puede organizar la información y proponer el plan. Yo revisaré el resultado final.",
        owner_session_id=OWNER,
        idempotency_key="answer-three",
    )
    assert third.next_question is None
    assert third.snapshot.project.state is ProjectState.BRIEFING_PENDING


def test_project_creation_preserves_description_and_owner() -> None:
    repository = InMemoryRepository(FrozenClock(NOW))
    projects, _ = services(repository)
    snapshot = projects.create_project(
        project_id=PROJECT_ID,
        name="Coordinador de entrega académica",
        description=DESCRIPTION,
        owner_session_id=OWNER,
        idempotency_key="create-demo",
    )
    assert snapshot.project.state is ProjectState.IDEA
    assert snapshot.project.briefing.problem == DESCRIPTION
    assert snapshot.project.owner_session_id == OWNER
    assert repository.list_for_project(PROJECT_ID)[0].event_type is AuditEventType.PROJECT_CREATED


def test_catalog_matches_the_official_three_turn_fixture() -> None:
    turns = json.loads((FIXTURE_DIRECTORY / "interview_turns.json").read_text(encoding="utf-8"))
    assert [question.id for question in QUESTION_CATALOG] == [turn["question_id"] for turn in turns]
    assert [question.prompt for question in QUESTION_CATALOG] == [
        turn["question"] for turn in turns
    ]
    assert [list(question.target_fields) for question in QUESTION_CATALOG] == [
        turn["target_fields"] for turn in turns
    ]


def test_official_three_turn_interview_updates_notes_without_repeating_questions() -> None:
    repository = InMemoryRepository(FrozenClock(NOW))
    _, interview = create_and_start(repository)
    initial = interview.get_notes(PROJECT_ID, owner_session_id=OWNER)
    assert initial.missing_fields == [
        "deadline",
        "available_hours",
        "input_format",
        "external_actions",
        "approval_owner",
        "success_criteria",
    ]
    complete_interview(interview)
    notes = interview.get_notes(PROJECT_ID, owner_session_id=OWNER)
    assert notes.deadline == "Viernes 18:00"
    assert notes.available_hours == 6
    assert notes.input_format == "Lista escrita por el estudiante"
    assert notes.outputs == ["Plan semanal", "Paquete requisito-evidencia"]
    assert notes.external_actions == "requires_clarification"
    assert notes.requires_clarification == ["external_actions"]
    assert notes.missing_fields == []
    assert notes.can_confirm is True
    history = repository.get(PROJECT_ID).project.briefing.answer_history
    assert [item.question_id for item in history] == [
        "ask_deadline_and_hours",
        "ask_input_and_result",
        "ask_autonomy_and_approval",
    ]


def test_local_interview_keeps_a_legal_workflow_domain_neutral() -> None:
    repository = InMemoryRepository(FrozenClock(NOW))
    projects, interview = services(repository)
    project_id = "legal_documents_project"
    projects.create_project(
        project_id=project_id,
        name="Documentos legales",
        description=(
            "Crear documentos legales personalizados para clientes de distintas profesiones "
            "usando solo datos confirmados."
        ),
        owner_session_id=OWNER,
        idempotency_key="create-legal",
    )
    interview.start(project_id, owner_session_id=OWNER, idempotency_key="start-legal")
    interview.record_answer(
        project_id,
        question_id="ask_deadline_and_hours",
        answer="Debe estar listo el viernes y puedo dedicar 4 horas.",
        owner_session_id=OWNER,
        idempotency_key="legal-one",
    )
    interview.record_answer(
        project_id,
        question_id="ask_input_and_result",
        answer=(
            "Recibe un formulario con datos confirmados y prepara un borrador legal "
            "verificable."
        ),
        owner_session_id=OWNER,
        idempotency_key="legal-two",
    )
    result = interview.record_answer(
        project_id,
        question_id="ask_autonomy_and_approval",
        answer=(
            "No puede enviar ni modificar sistemas externos; el abogado responsable "
            "aprueba el resultado final."
        ),
        owner_session_id=OWNER,
        idempotency_key="legal-three",
    )

    briefing = result.snapshot.project.briefing
    assert briefing.approval_owner == "Abogado responsable"
    assert briefing.external_actions == "none"
    assert briefing.success_criteria == [
        "El resultado usa únicamente información confirmada",
        "El resultado cumple los criterios confirmados",
        "Abogado responsable aprueba el resultado final",
    ]
    assert "semanal" not in briefing.model_dump_json().casefold()
    assert "estudiante" not in briefing.model_dump_json().casefold()


def test_deadline_question_accepts_partial_and_relative_natural_answers() -> None:
    repository = InMemoryRepository(FrozenClock(NOW))
    projects, interview = services(repository)
    partial_id = "partial_deadline_project"
    projects.create_project(
        project_id=partial_id,
        name="Contrato legal",
        description="Crear contratos legales a partir de datos confirmados.",
        owner_session_id=OWNER,
        idempotency_key="create-partial",
    )
    interview.start(partial_id, owner_session_id=OWNER, idempotency_key="start-partial")
    partial = interview.record_answer(
        partial_id,
        question_id="ask_deadline_and_hours",
        answer="No se especifica el plazo.",
        owner_session_id=OWNER,
        idempotency_key="partial-deadline",
    )
    assert partial.snapshot.project.briefing.deadline == "Sin plazo definido"
    assert partial.snapshot.project.briefing.available_hours is None
    assert partial.next_question is not None
    assert partial.next_question["question"] == (
        "¿Cuántas horas en total puedes dedicar al proceso?"
    )
    assert partial.next_question["target_fields"] == ["available_hours"]

    completed_pair = interview.record_answer(
        partial_id,
        question_id="ask_deadline_and_hours",
        answer="Puedo dedicar 2 horas diarias.",
        owner_session_id=OWNER,
        idempotency_key="partial-hours",
    )
    assert completed_pair.snapshot.project.briefing.deadline == "Sin plazo definido"
    assert completed_pair.snapshot.project.briefing.available_hours == 2
    assert completed_pair.next_question is not None
    assert completed_pair.next_question["question_id"] == "ask_input_and_result"

    relative_id = "relative_deadline_project"
    projects.create_project(
        project_id=relative_id,
        name="Otro contrato legal",
        description="Crear contratos legales a partir de datos confirmados.",
        owner_session_id=OWNER,
        idempotency_key="create-relative",
    )
    interview.start(relative_id, owner_session_id=OWNER, idempotency_key="start-relative")
    relative = interview.record_answer(
        relative_id,
        question_id="ask_deadline_and_hours",
        answer="En tres días y 2 horas diarias.",
        owner_session_id=OWNER,
        idempotency_key="relative-answer",
    )
    assert relative.snapshot.project.briefing.deadline == "En 3 días"
    assert relative.snapshot.project.briefing.available_hours == 6


def test_correction_recalculates_notes_and_preserves_history() -> None:
    repository = InMemoryRepository(FrozenClock(NOW))
    _, interview = create_and_start(repository)
    complete_interview(interview)
    five = interview.correct_field(
        PROJECT_ID,
        field_name="available_hours",
        value=5,
        owner_session_id=OWNER,
        idempotency_key="correct-hours-five",
    )
    assert five.notes.available_hours == 5
    six = interview.correct_field(
        PROJECT_ID,
        field_name="available_hours",
        value=6,
        owner_session_id=OWNER,
        idempotency_key="correct-hours-six",
    )
    assert six.notes.available_hours == 6
    history = six.snapshot.project.briefing.answer_history
    assert [item.values["available_hours"] for item in history[-2:]] == [5, 6]
    corrected = [
        event
        for event in repository.list_for_project(PROJECT_ID)
        if event.event_type is AuditEventType.INTERVIEW_ANSWER_CORRECTED
    ]
    assert [event.details["new_value"] for event in corrected] == [5, 6]


def test_confirmation_is_blocked_until_required_fields_are_present() -> None:
    repository = InMemoryRepository(FrozenClock(NOW))
    _, interview = create_and_start(repository)
    with pytest.raises(BriefingIncompleteError) as captured:
        interview.confirm_briefing(
            PROJECT_ID,
            owner_session_id=OWNER,
            idempotency_key="confirm-too-soon",
        )
    assert "deadline" in captured.value.context["missing_fields"]


def test_confirmed_briefing_unlocks_design_state_and_emits_event() -> None:
    repository = InMemoryRepository(FrozenClock(NOW))
    _, interview = create_and_start(repository)
    complete_interview(interview)
    result = interview.confirm_briefing(
        PROJECT_ID,
        owner_session_id=OWNER,
        idempotency_key="confirm-demo",
    )
    assert result.snapshot.project.state is ProjectState.BRIEFING_CONFIRMED
    assert result.snapshot.project.briefing.confirmed is True
    assert result.snapshot.project.briefing.confirmed_by == OWNER
    assert (
        repository.list_for_project(PROJECT_ID)[-1].event_type is AuditEventType.BRIEFING_CONFIRMED
    )
    assert require_confirmed_briefing(result.snapshot.project).confirmed is True


def test_design_gate_rejects_an_unconfirmed_briefing() -> None:
    repository = InMemoryRepository(FrozenClock(NOW))
    create_and_start(repository)
    with pytest.raises(DomainError) as captured:
        require_confirmed_briefing(repository.get(PROJECT_ID).project)
    assert captured.value.code == "BRIEFING_NOT_CONFIRMED"


def test_answers_are_idempotent_and_do_not_duplicate_history_or_events() -> None:
    repository = InMemoryRepository(FrozenClock(NOW))
    _, interview = create_and_start(repository)
    arguments = {
        "question_id": "ask_deadline_and_hours",
        "answer": "Debe quedar listo el viernes a las 6:00 p. m. y tengo seis horas.",
        "owner_session_id": OWNER,
        "idempotency_key": "answer-one",
    }
    first = interview.record_answer(PROJECT_ID, **arguments)  # type: ignore[arg-type]
    replay = interview.record_answer(PROJECT_ID, **arguments)  # type: ignore[arg-type]
    assert replay.snapshot.version == first.snapshot.version
    assert len(replay.snapshot.project.briefing.answer_history) == 1
    matching = [
        event
        for event in repository.list_for_project(PROJECT_ID)
        if event.event_type is AuditEventType.INTERVIEW_ANSWER_RECORDED
    ]
    assert len(matching) == 1


def test_deadline_answer_accepts_available_time_expressed_in_minutes() -> None:
    repository = InMemoryRepository(FrozenClock(NOW))
    _, interview = create_and_start(repository)
    result = interview.record_answer(
        PROJECT_ID,
        question_id="ask_deadline_and_hours",
        answer="Cada viernes a las 16:00; puedo dedicar veinte minutos a revisarlo.",
        owner_session_id=OWNER,
        idempotency_key="answer-in-minutes",
    )

    assert result.snapshot.project.briefing.deadline == "Viernes a las 16:00"
    assert result.snapshot.project.briefing.available_hours == 1


def test_project_access_is_scoped_to_owner_session() -> None:
    repository = InMemoryRepository(FrozenClock(NOW))
    projects, interview = create_and_start(repository)
    with pytest.raises(ProjectAccessDeniedError):
        projects.get_snapshot(PROJECT_ID, owner_session_id="other_session")
    with pytest.raises(ProjectAccessDeniedError):
        interview.get_notes(PROJECT_ID, owner_session_id="other_session")


def test_json_repository_restores_interview_notes_after_restart(tmp_path: Path) -> None:
    data = tmp_path / "studio-data"
    first_repository = JsonLocalRepository(data, FrozenClock(NOW))
    _, interview = create_and_start(first_repository)
    complete_interview(interview)
    restarted = JsonLocalRepository(data, FrozenClock(NOW))
    restored = InterviewService(restarted, restarted, FrozenClock(NOW)).get_notes(
        PROJECT_ID,
        owner_session_id=OWNER,
    )
    assert restored.available_hours == 6
    assert restored.missing_fields == []
    assert len(restarted.get(PROJECT_ID).project.briefing.answer_history) == 3
