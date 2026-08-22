from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, cast

from fastapi.testclient import TestClient

from app.main import create_app
from infrastructure.local.clock import FrozenClock
from infrastructure.local.repositories import InMemoryRepository

SESSION = "browser_demo_user"
NOW = datetime(2026, 8, 13, 16, 0, tzinfo=UTC)
OFFICIAL_FEEDBACK = (
    "No quiero que el agente envíe nada ni modifique calendarios. Solo debe preparar el "
    "paquete y esperar mi aprobación. También quiero una prueba que compruebe que una "
    "instrucción dentro de los requisitos no pueda saltarse esta regla."
)


def client() -> TestClient:
    repository = InMemoryRepository(FrozenClock(NOW))
    return TestClient(create_app(repository, repository, FrozenClock(NOW)))


def headers(key: str | None = None) -> dict[str, str]:
    result = {"X-Studio-Session": SESSION}
    if key:
        result["Idempotency-Key"] = key
    return result


def create_project(api: TestClient) -> str:
    response = api.post(
        "/api/v1/projects",
        headers=headers("create-browser-project"),
        json={
            "name": "Coordinador de entrega académica",
            "description": (
                "Necesito un agente que me ayude a organizar cada semana los requisitos de "
                "mi proyecto final y compruebe que no olvide ninguna evidencia."
            ),
        },
    )
    assert response.status_code == 201
    return str(response.json()["snapshot"]["project"]["id"])


def complete_interview(api: TestClient, project_id: str) -> None:
    started = api.post(
        f"/api/v1/projects/{project_id}/interview/start",
        headers=headers("start-browser-interview"),
    )
    assert started.status_code == 200
    turns: list[tuple[str, str]] = [
        (
            "ask_deadline_and_hours",
            "Debe quedar listo el viernes a las 6:00 p. m. y tengo seis horas.",
        ),
        (
            "ask_input_and_result",
            "Los escribiré en una lista. Debe producir un plan semanal y un paquete con evidencia.",
        ),
        (
            "ask_autonomy_and_approval",
            "Puede organizar la información y proponer el plan. Yo revisaré el resultado final.",
        ),
    ]
    for index, (question_id, answer) in enumerate(turns, start=1):
        response = api.post(
            f"/api/v1/projects/{project_id}/interview/messages",
            headers=headers(f"browser-answer-{index}"),
            json={"question_id": question_id, "answer": answer},
        )
        assert response.status_code == 200


def reach_revision_two(api: TestClient, project_id: str) -> dict[str, Any]:
    complete_interview(api, project_id)
    confirmed = api.post(
        f"/api/v1/projects/{project_id}/briefing/confirm",
        headers=headers("confirm-browser-briefing"),
    )
    assert confirmed.status_code == 200
    first = api.post(
        f"/api/v1/projects/{project_id}/revisions",
        headers=headers("create-browser-revision-one"),
    )
    assert first.status_code == 201
    second = api.post(
        f"/api/v1/projects/{project_id}/revisions/1/feedback",
        headers=headers("browser-official-feedback"),
        json={"expected_revision": 1, "feedback": OFFICIAL_FEEDBACK},
    )
    assert second.status_code == 201
    return cast(dict[str, Any], second.json())


def test_browser_flow_from_idea_to_human_approval() -> None:
    api = client()
    project_id = create_project(api)
    result = reach_revision_two(api, project_id)
    assert result["snapshot"]["project"]["active_revision"] == 2
    assert len(result["snapshot"]["revisions"]) == 2

    diff = api.get(
        f"/api/v1/projects/{project_id}/revisions/2/diff?from_revision=1",
        headers=headers(),
    )
    assert diff.status_code == 200
    assert {
        item["identifier"] for item in diff.json()["removed"] if item["category"] == "tool"
    } == {
        "create_calendar_blocks",
        "send_review_package",
    }

    approved = api.post(
        f"/api/v1/projects/{project_id}/revisions/2/approval",
        headers=headers("browser-approve-revision-two"),
        json={"decision": "approved", "note": "Aprobado después de revisar el diff."},
    )
    assert approved.status_code == 200
    assert approved.json()["snapshot"]["project"]["state"] == "diseno_aprobado"
    assert (
        approved.json()["snapshot"]["revisions"][1]["specification"]["approval"]["status"]
        == "approved"
    )
    assert approved.json()["events"][-1]["event_type"] == "revision_approved"


def test_api_does_not_allow_skipping_interview_and_confirmation() -> None:
    api = client()
    project_id = create_project(api)
    response = api.post(
        f"/api/v1/projects/{project_id}/revisions",
        headers=headers("skip-to-design"),
    )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "BRIEFING_NOT_CONFIRMED"


def test_request_id_is_returned_on_success_and_structured_errors() -> None:
    api = client()
    success = api.get("/health", headers={"X-Request-ID": "known-request-id"})
    assert success.headers["X-Request-ID"] == "known-request-id"
    error = api.post(
        "/api/v1/projects",
        headers={**headers("invalid-project"), "X-Request-ID": "validation-id"},
        json={"name": "x", "description": "short"},
    )
    assert error.status_code == 422
    assert error.json()["error"]["code"] == "REQUEST_VALIDATION_FAILED"
    assert error.json()["error"]["request_id"] == "validation-id"


def test_request_size_and_unknown_fields_are_rejected() -> None:
    api = client()
    too_large = api.post(
        "/api/v1/projects",
        headers=headers("large-project-payload"),
        json={"name": "Large project", "description": "x" * 40_000},
    )
    assert too_large.status_code == 413
    unexpected = api.post(
        "/api/v1/projects",
        headers=headers("unknown-field-project"),
        json={
            "name": "Valid project",
            "description": "A sufficiently descriptive project request.",
            "unexpected": True,
        },
    )
    assert unexpected.status_code == 422


def test_project_is_restorable_and_protected_by_session() -> None:
    api = client()
    project_id = create_project(api)
    restored = api.get(f"/api/v1/projects/{project_id}", headers=headers())
    assert restored.status_code == 200
    forbidden = api.get(
        f"/api/v1/projects/{project_id}",
        headers={"X-Studio-Session": "another_user"},
    )
    assert forbidden.status_code == 403
    assert forbidden.json()["error"]["code"] == "PROJECT_ACCESS_DENIED"
