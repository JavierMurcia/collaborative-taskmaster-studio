"""Run and persist the controlled laboratory for a generated Taskmaster."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict

from sandbox import EvaluationReport, SandboxEvaluator
from studio.application.project_service import ProjectService
from studio.domain.enums import AuditEventType, ProjectState
from studio.domain.errors import DomainError
from studio.domain.models import ArtifactMetadata, AuditEvent, ProjectSnapshot
from studio.domain.transitions import transition_project
from studio.ports.clock import Clock
from studio.ports.repositories import EventRepository, ProjectRepository


class EvaluationResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    snapshot: ProjectSnapshot
    artifact: ArtifactMetadata
    report: EvaluationReport
    report_relative_path: str
    reused: bool


class EvaluationService:
    def __init__(
        self,
        projects: ProjectRepository,
        events: EventRepository,
        clock: Clock,
        evaluator: SandboxEvaluator,
        generated_root: Path,
    ) -> None:
        self._projects = projects
        self._events = events
        self._clock = clock
        self._evaluator = evaluator
        self._root = generated_root.resolve()

    def evaluate(
        self,
        project_id: str,
        *,
        revision: int,
        owner_session_id: str,
        idempotency_key: str,
    ) -> EvaluationResult:
        snapshot = self._projects.get(project_id, owner_session_id=owner_session_id)
        ProjectService.ensure_owner(snapshot, owner_session_id)
        generation = _generation_artifact(snapshot, revision)
        report_id = _report_id(project_id, revision, generation.sha256)
        existing = next((item for item in snapshot.artifacts if item.id == report_id), None)
        if existing is not None:
            return self._restore(snapshot, existing)
        if snapshot.project.state is not ProjectState.GENERATING:
            raise DomainError(
                "EVALUATION_NOT_READY",
                "Primero debes generar el proyecto aprobado.",
                context={"state": snapshot.project.state.value},
            )
        validating = self._projects.save(
            transition_project(snapshot.project, ProjectState.VALIDATING),
            expected_version=snapshot.version,
            idempotency_key=f"{idempotency_key}:validating",
        )
        self._event(
            project_id,
            revision,
            AuditEventType.EVALUATION_STARTED,
            owner_session_id,
            "Laboratorio aislado iniciado sin credenciales ni efectos externos.",
            f"{idempotency_key}:started",
        )
        source = self._root.joinpath(*Path(generation.relative_path).parts).parent.resolve()
        _ensure_within(source, self._root)
        try:
            report = self._evaluator.evaluate(source)
        except Exception:
            failed = transition_project(validating.project, ProjectState.DESIGN_IN_REVIEW)
            self._projects.save(
                failed,
                expected_version=validating.version,
                idempotency_key=f"{idempotency_key}:failed-state",
            )
            raise
        report_path = source / "evaluation-report.json"
        payload = json.dumps(report.model_dump(mode="json"), ensure_ascii=False, indent=2) + "\n"
        report_path.write_text(payload, encoding="utf-8", newline="\n")
        artifact = ArtifactMetadata(
            id=report_id,
            project_id=project_id,
            revision=revision,
            relative_path=report_path.relative_to(self._root).as_posix(),
            sha256=hashlib.sha256(payload.encode("utf-8")).hexdigest(),
            framework=generation.framework,
            template_version=generation.template_version,
            validation_status="valid" if report.decision == "ready" else "invalid",
        )
        with_artifact = self._projects.add_artifact(
            artifact,
            expected_version=validating.version,
            idempotency_key=f"{idempotency_key}:report",
        )
        for scenario in report.scenarios:
            self._event(
                project_id,
                revision,
                AuditEventType.SCENARIO_COMPLETED,
                owner_session_id,
                f"Escenario {scenario.name}: {'aprobado' if scenario.passed else 'fallido'}.",
                f"{idempotency_key}:scenario:{scenario.scenario_id}",
                details={
                    "scenario_id": scenario.scenario_id,
                    "category": scenario.category,
                    "passed": scenario.passed,
                    "outcome": scenario.outcome,
                },
            )
        target = (
            ProjectState.READY_TO_EXPORT
            if report.decision == "ready"
            else ProjectState.DESIGN_IN_REVIEW
        )
        completed = self._projects.save(
            transition_project(with_artifact.project, target),
            expected_version=with_artifact.version,
            idempotency_key=f"{idempotency_key}:completed-state",
        )
        self._event(
            project_id,
            revision,
            AuditEventType.EVALUATION_COMPLETED,
            owner_session_id,
            f"Evaluación completada con decisión {report.decision}.",
            f"{idempotency_key}:completed",
            details={"decision": report.decision, "report_id": artifact.id},
        )
        return EvaluationResult(
            snapshot=completed,
            artifact=artifact,
            report=report,
            report_relative_path=artifact.relative_path,
            reused=False,
        )

    def get(
        self, project_id: str, *, revision: int, owner_session_id: str
    ) -> EvaluationResult:
        snapshot = self._projects.get(project_id, owner_session_id=owner_session_id)
        ProjectService.ensure_owner(snapshot, owner_session_id)
        reports = [
            item
            for item in snapshot.artifacts
            if item.revision == revision and Path(item.relative_path).name == "evaluation-report.json"
        ]
        if not reports:
            raise DomainError("EVALUATION_NOT_FOUND", "Todavía no existe un informe de evaluación.")
        return self._restore(snapshot, reports[-1])

    def _restore(
        self, snapshot: ProjectSnapshot, artifact: ArtifactMetadata
    ) -> EvaluationResult:
        path = self._root.joinpath(*Path(artifact.relative_path).parts).resolve()
        _ensure_within(path, self._root)
        payload = path.read_bytes()
        if hashlib.sha256(payload).hexdigest() != artifact.sha256:
            raise DomainError("EVALUATION_REPORT_TAMPERED", "El checksum del informe no coincide.")
        report = EvaluationReport.model_validate_json(payload)
        return EvaluationResult(
            snapshot=snapshot,
            artifact=artifact,
            report=report,
            report_relative_path=artifact.relative_path,
            reused=True,
        )

    def _event(
        self,
        project_id: str,
        revision: int,
        event_type: AuditEventType,
        actor_id: str,
        summary: str,
        key: str,
        *,
        details: dict[str, Any] | None = None,
    ) -> None:
        self._events.append(
            AuditEvent(
                id=_event_id(event_type.value, key),
                project_id=project_id,
                event_type=event_type,
                actor_id=actor_id,
                revision=revision,
                summary=summary,
                occurred_at=self._clock.now(),
                details=details or {},
            ),
            idempotency_key=f"{key}:event",
        )


def require_exportable(snapshot: ProjectSnapshot) -> None:
    if snapshot.project.state not in {ProjectState.READY_TO_EXPORT, ProjectState.EXPORTED}:
        raise DomainError(
            "EXPORT_BLOCKED_BY_EVALUATION",
            "La exportación está bloqueada hasta obtener una evaluación ready.",
            context={"state": snapshot.project.state.value},
        )


def _generation_artifact(snapshot: ProjectSnapshot, revision: int) -> ArtifactMetadata:
    candidates = [
        item
        for item in snapshot.artifacts
        if item.revision == revision and Path(item.relative_path).name == "taskmaster.manifest.json"
    ]
    if not candidates:
        raise DomainError("GENERATION_ARTIFACT_MISSING", "Primero debes generar el proyecto.")
    return candidates[-1]


def _ensure_within(path: Path, root: Path) -> None:
    try:
        path.relative_to(root)
    except ValueError as error:
        raise DomainError("EVALUATION_PATH_ESCAPE", "La evaluación salió de generated/.") from error


def _report_id(project_id: str, revision: int, source_sha: str) -> str:
    digest = hashlib.sha256(f"{project_id}:{revision}:{source_sha}:h7".encode()).hexdigest()[:16]
    return f"evaluation_{digest}"


def _event_id(kind: str, key: str) -> str:
    safe_kind = re.sub(r"[^a-z0-9_]", "_", kind.casefold())[:38]
    digest = hashlib.sha256(key.encode()).hexdigest()[:16]
    return f"{safe_kind}_{digest}"
