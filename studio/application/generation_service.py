"""Generate a reproducible framework project from an approved revision."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

from studio.application.approval_service import require_approved_revision
from studio.application.project_service import ProjectService
from studio.domain.enums import AuditEventType, ProjectState
from studio.domain.errors import DomainError
from studio.domain.models import ArtifactMetadata, AuditEvent, ProjectSnapshot
from studio.domain.transitions import transition_project
from studio.ports.clock import Clock
from studio.ports.generator import GeneratedBundle, GeneratorAdapter
from studio.ports.repositories import EventRepository, ProjectRepository


class GenerationResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    snapshot: ProjectSnapshot
    artifact: ArtifactMetadata
    output_relative_path: str
    manifest: dict[str, Any]
    reused: bool


class GenerationService:
    def __init__(
        self,
        projects: ProjectRepository,
        events: EventRepository,
        clock: Clock,
        adapter: GeneratorAdapter,
        generated_root: Path,
    ) -> None:
        self._projects = projects
        self._events = events
        self._clock = clock
        self._adapter = adapter
        self._root = generated_root.resolve()

    def generate(
        self,
        project_id: str,
        *,
        revision: int,
        owner_session_id: str,
        idempotency_key: str,
    ) -> GenerationResult:
        snapshot = self._projects.get(project_id, owner_session_id=owner_session_id)
        ProjectService.ensure_owner(snapshot, owner_session_id)
        artifact_id = _artifact_id(project_id, revision, self._adapter.template_version)
        existing = next((item for item in snapshot.artifacts if item.id == artifact_id), None)
        if existing is not None:
            return self._restore(snapshot, existing)

        specification = require_approved_revision(snapshot)
        if specification.revision != revision:
            raise DomainError(
                "GENERATION_REVISION_MISMATCH",
                "Solo puede generarse la revisión activa aprobada.",
                context={"requested_revision": revision, "active_revision": specification.revision},
            )
        self._adapter.validate_capabilities(specification)
        generating_project = transition_project(snapshot.project, ProjectState.GENERATING).model_copy(
            update={"updated_at": self._clock.now()}, deep=True
        )
        generating = self._projects.save(
            generating_project,
            expected_version=snapshot.version,
            idempotency_key=f"{idempotency_key}:state",
        )
        self._append_event(
            project_id,
            revision,
            AuditEventType.GENERATION_STARTED,
            owner_session_id,
            "Generación ADK iniciada desde una revisión humana aprobada.",
            f"{idempotency_key}:started",
        )
        destination = self._root / project_id / f"revision-{revision}"
        try:
            bundle = self._adapter.generate(specification, destination)
            artifact = self._artifact(
                artifact_id,
                project_id,
                revision,
                specification.generation.target_framework,
                bundle,
            )
            saved = self._projects.add_artifact(
                artifact,
                expected_version=generating.version,
                idempotency_key=f"{idempotency_key}:artifact",
            )
        except Exception as error:
            restored_project = transition_project(
                generating.project, ProjectState.DESIGN_APPROVED
            ).model_copy(update={"updated_at": self._clock.now()}, deep=True)
            self._projects.save(
                restored_project,
                expected_version=generating.version,
                idempotency_key=f"{idempotency_key}:rollback",
            )
            self._append_event(
                project_id,
                revision,
                AuditEventType.GENERATION_FAILED,
                owner_session_id,
                "La generación se detuvo de forma segura; la revisión aprobada se conservó.",
                f"{idempotency_key}:failed",
                details={"error_type": type(error).__name__},
            )
            raise
        manifest = _read_manifest(bundle.manifest_path)
        self._append_event(
            project_id,
            revision,
            AuditEventType.ARTIFACT_GENERATED,
            owner_session_id,
            f"Proyecto {specification.generation.target_framework} generado con manifiesto y checksums verificados.",
            f"{idempotency_key}:generated",
            details={
                "artifact_id": artifact.id,
                "template_version": artifact.template_version,
                "file_count": len(bundle.files),
            },
        )
        return GenerationResult(
            snapshot=saved,
            artifact=artifact,
            output_relative_path=bundle.output_directory.relative_to(self._root).as_posix(),
            manifest=manifest,
            reused=False,
        )

    def _restore(
        self,
        snapshot: ProjectSnapshot,
        artifact: ArtifactMetadata,
    ) -> GenerationResult:
        manifest_path = self._root.joinpath(*Path(artifact.relative_path).parts)
        if not manifest_path.is_file():
            raise DomainError(
                "GENERATION_ARTIFACT_MISSING",
                "El registro existe, pero su manifiesto local no está disponible.",
            )
        payload = manifest_path.read_bytes()
        if hashlib.sha256(payload).hexdigest() != artifact.sha256:
            raise DomainError(
                "GENERATION_MANIFEST_TAMPERED",
                "El checksum del manifiesto generado no coincide.",
            )
        return GenerationResult(
            snapshot=snapshot,
            artifact=artifact,
            output_relative_path=manifest_path.parent.relative_to(self._root).as_posix(),
            manifest=json.loads(payload),
            reused=True,
        )

    def _artifact(
        self,
        artifact_id: str,
        project_id: str,
        revision: int,
        framework: Literal["google_adk", "genai_sdk", "genkit", "antigravity"],
        bundle: GeneratedBundle,
    ) -> ArtifactMetadata:
        payload = bundle.manifest_path.read_bytes()
        return ArtifactMetadata(
            id=artifact_id,
            project_id=project_id,
            revision=revision,
            relative_path=bundle.manifest_path.relative_to(self._root).as_posix(),
            sha256=hashlib.sha256(payload).hexdigest(),
            framework=framework,
            template_version=bundle.template_version,
            validation_status="valid",
        )

    def _append_event(
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


def _artifact_id(project_id: str, revision: int, template_version: str) -> str:
    digest = hashlib.sha256(
        f"{project_id}:{revision}:{template_version}".encode()
    ).hexdigest()[:16]
    return f"artifact_{digest}"


def _event_id(kind: str, key: str) -> str:
    safe_kind = re.sub(r"[^a-z0-9_]", "_", kind.casefold())[:38]
    digest = hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]
    return f"{safe_kind}_{digest}"


def _read_manifest(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise DomainError("GENERATION_MANIFEST_INVALID", "El manifiesto no es un objeto JSON.")
    return payload
