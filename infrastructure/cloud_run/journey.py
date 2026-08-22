"""End-to-end H10-10 journey through the deployed Collaborative Partner."""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import asdict, dataclass
from typing import Any, cast
from urllib.error import HTTPError
from urllib.parse import urljoin, urlparse
from urllib.request import Request, urlopen
from uuid import uuid4

from studio.application.demo_fixture import load_official_demo_fixture

Requester = Callable[[Request, float], tuple[int, Any]]

DEMO_FIXTURE = load_official_demo_fixture()
ANSWERS = DEMO_FIXTURE.answers
FEEDBACK = DEMO_FIXTURE.feedback.text


@dataclass(frozen=True, slots=True)
class JourneyStep:
    name: str
    method: str
    path: str
    status_code: int


@dataclass(frozen=True, slots=True)
class JourneyResult:
    status: str
    base_url: str
    project_id: str
    approved_revision: int
    artifact_id: str
    evaluation_decision: str
    model_completed_events: int
    model_fallback_events: int
    audit_event_count: int
    steps: tuple[JourneyStep, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            **asdict(self),
            "steps": [asdict(step) for step in self.steps],
        }


def run_demo_journey(
    base_url: str,
    *,
    timeout_seconds: float = 90.0,
    requester: Requester | None = None,
) -> JourneyResult:
    """Create, interview, revise, approve, generate, and evaluate one Taskmaster."""
    normalized = _validate_url(base_url)
    send = requester or _request_json
    token = uuid4().hex
    session = f"h10_journey_{token[:12]}"
    steps: list[JourneyStep] = []

    def call(
        name: str,
        method: str,
        path: str,
        *,
        expected: int,
        body: dict[str, Any] | None = None,
        idempotency: str | None = None,
    ) -> Any:
        headers = {"X-Studio-Session": session}
        if idempotency is not None:
            headers["Idempotency-Key"] = f"h10-{idempotency}-{token}"
        data = None
        if body is not None:
            headers["Content-Type"] = "application/json"
            data = json.dumps(body).encode("utf-8")
        status_code, payload = send(
            Request(
                urljoin(normalized, path),
                data=data,
                headers=headers,
                method=method,
            ),
            timeout_seconds,
        )
        if status_code != expected:
            raise RuntimeError(
                f"El paso {name} respondió HTTP {status_code}; se esperaba {expected}: "
                f"{payload!r}"
            )
        steps.append(JourneyStep(name, method, path, status_code))
        return payload

    created = cast(
        dict[str, Any],
        call(
            "project_create",
            "POST",
            "/api/v1/projects",
            expected=201,
            idempotency="create",
            body={
                "name": DEMO_FIXTURE.project.name,
                "description": DEMO_FIXTURE.project.description,
            },
        ),
    )
    project_id = _project(created)

    interview = cast(
        dict[str, Any],
        call(
            "interview_start",
            "POST",
            f"/api/v1/projects/{project_id}/interview/start",
            expected=200,
            idempotency="interview-start",
        ),
    )
    for index in range(1, 4):
        question = cast(dict[str, Any], interview.get("next_question") or {})
        question_id = str(question.get("question_id", ""))
        if question_id not in ANSWERS:
            raise RuntimeError(
                "La entrevista no devolvió una pregunta del catálogo controlado."
            )
        interview = cast(
            dict[str, Any],
            call(
                f"interview_answer_{index}",
                "POST",
                f"/api/v1/projects/{project_id}/interview/messages",
                expected=200,
                idempotency=f"answer-{index}",
                body={"question_id": question_id, "answer": ANSWERS[question_id]},
            ),
        )
    notes = cast(dict[str, Any], interview.get("notes", {}))
    if notes.get("can_confirm") is not True or interview.get("next_question") is not None:
        raise RuntimeError("La entrevista no produjo un briefing completo y confirmable.")

    call(
        "briefing_confirm",
        "POST",
        f"/api/v1/projects/{project_id}/briefing/confirm",
        expected=200,
        idempotency="briefing-confirm",
    )
    first = cast(
        dict[str, Any],
        call(
            "revision_create",
            "POST",
            f"/api/v1/projects/{project_id}/revisions",
            expected=201,
            idempotency="revision-one",
        ),
    )
    if cast(dict[str, Any], first.get("revision", {})).get("number") != (
        DEMO_FIXTURE.feedback.expected_revision
    ):
        raise RuntimeError("La primera especificación no quedó en la revisión 1.")

    revised = cast(
        dict[str, Any],
        call(
            "feedback_apply",
            "POST",
            f"/api/v1/projects/{project_id}/revisions/1/feedback",
            expected=201,
            idempotency="feedback",
            body={"expected_revision": 1, "feedback": FEEDBACK},
        ),
    )
    if cast(dict[str, Any], revised.get("revision", {})).get("number") != (
        DEMO_FIXTURE.approval.revision
    ):
        raise RuntimeError("El feedback no produjo la revisión 2.")

    diff = cast(
        dict[str, Any],
        call(
            "revision_diff",
            "GET",
            f"/api/v1/projects/{project_id}/revisions/2/diff?from_revision=1",
            expected=200,
        ),
    )
    if diff.get("from_revision") != 1 or diff.get("to_revision") != 2:
        raise RuntimeError("El diff no compara las revisiones 1 y 2.")

    approved = cast(
        dict[str, Any],
        call(
            "human_approval",
            "POST",
            f"/api/v1/projects/{project_id}/revisions/2/approval",
            expected=200,
            idempotency="approval",
            body={
                "decision": DEMO_FIXTURE.approval.decision,
                "note": DEMO_FIXTURE.approval.note,
            },
        ),
    )
    if _state(approved) != "diseno_aprobado":
        raise RuntimeError("La decisión humana no dejó el diseño aprobado.")

    generated = cast(
        dict[str, Any],
        call(
            "taskmaster_generation",
            "POST",
            f"/api/v1/projects/{project_id}/generation",
            expected=201,
            idempotency="generation",
            body={"revision": DEMO_FIXTURE.generation.revision},
        ),
    )
    artifact = cast(dict[str, Any], generated.get("artifact", {}))
    manifest = cast(dict[str, Any], generated.get("manifest", {}))
    if (
        artifact.get("validation_status")
        != DEMO_FIXTURE.expected.artifact_validation_status
        or artifact.get("framework") != DEMO_FIXTURE.generation.target_framework
        or manifest.get("template_version") != DEMO_FIXTURE.generation.template_version
    ):
        raise RuntimeError("El paquete generado no cumple el contrato Google ADK.")
    artifact_id = str(artifact.get("id", ""))
    if not artifact_id:
        raise RuntimeError("La generación no informó un artefacto persistido.")

    evaluated = cast(
        dict[str, Any],
        call(
            "sandbox_evaluation",
            "POST",
            f"/api/v1/projects/{project_id}/evaluations",
            expected=201,
            idempotency="evaluation",
            body={"revision": DEMO_FIXTURE.evaluation.revision},
        ),
    )
    report = cast(dict[str, Any], evaluated.get("report", {}))
    decision = str(report.get("decision", ""))
    scenarios = cast(list[dict[str, Any]], report.get("scenarios", []))
    if decision != DEMO_FIXTURE.evaluation.expected_decision or len(scenarios) < len(
        DEMO_FIXTURE.evaluation.required_categories
    ) or not all(
        item.get("passed") is True for item in scenarios
    ):
        raise RuntimeError("El laboratorio no aprobó los escenarios obligatorios.")

    events = cast(
        list[dict[str, Any]],
        call(
            "audit_events",
            "GET",
            f"/api/v1/projects/{project_id}/events",
            expected=200,
        ),
    )
    kinds = [str(item.get("event_type", "")) for item in events]
    required_events = {
        event_type.value for event_type in DEMO_FIXTURE.expected.required_event_types
    }
    if not required_events.issubset(kinds) or len(events) < (
        DEMO_FIXTURE.expected.minimum_audit_events
    ):
        raise RuntimeError("La trayectoria no contiene todos los hitos auditables.")

    return JourneyResult(
        status="passed",
        base_url=normalized.rstrip("/"),
        project_id=project_id,
        approved_revision=DEMO_FIXTURE.expected.approved_revision,
        artifact_id=artifact_id,
        evaluation_decision=decision,
        model_completed_events=kinds.count("model_generation_completed"),
        model_fallback_events=kinds.count("model_fallback_used"),
        audit_event_count=len(events),
        steps=tuple(steps),
    )


def _project(payload: dict[str, Any]) -> str:
    project = cast(
        dict[str, Any],
        cast(dict[str, Any], payload.get("snapshot", {})).get("project", {}),
    )
    project_id = str(project.get("id", ""))
    if not project_id:
        raise RuntimeError("La creación no devolvió un proyecto.")
    return project_id


def _state(payload: dict[str, Any]) -> str:
    return str(
        cast(
            dict[str, Any],
            cast(dict[str, Any], payload.get("snapshot", {})).get("project", {}),
        ).get("state", "")
    )


def _request_json(request: Request, timeout_seconds: float) -> tuple[int, Any]:
    try:
        with urlopen(request, timeout=timeout_seconds) as response:  # noqa: S310
            return response.status, json.load(response)
    except HTTPError as error:
        return error.code, json.loads(error.read().decode("utf-8") or "{}")


def _validate_url(base_url: str) -> str:
    parsed = urlparse(base_url)
    if parsed.scheme != "https" or not parsed.netloc or parsed.query or parsed.fragment:
        raise ValueError("H10-10 exige una URL base HTTPS sin query ni fragmento.")
    return base_url.rstrip("/") + "/"
