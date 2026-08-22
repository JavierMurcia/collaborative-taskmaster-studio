"""Safe, session-scoped reset of the official demonstration aggregate."""

from __future__ import annotations

import hashlib
import logging
import shutil
from pathlib import Path

from pydantic import BaseModel, ConfigDict

from studio.application.demo_fixture import load_official_demo_fixture
from studio.domain.errors import DomainError
from studio.domain.models import Briefing, Project, ProjectSnapshot
from studio.ports.clock import Clock
from studio.ports.repositories import ProjectRepository

CONFIRMATION_PHRASE = "REINICIAR_DEMO"
LOGGER = logging.getLogger("studio.demo_reset")


class DemoResetResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    snapshot: ProjectSnapshot
    events: tuple[()] = ()
    fixture_id: str
    reset_id: str
    generated_files_removed: bool


class DemoResetService:
    def __init__(
        self,
        projects: ProjectRepository,
        clock: Clock,
        generated_root: Path,
    ) -> None:
        self._projects = projects
        self._clock = clock
        self._generated_root = generated_root.resolve()

    def reset(
        self,
        project_id: str,
        *,
        owner_session_id: str,
        confirmation: str,
        idempotency_key: str,
    ) -> DemoResetResult:
        if confirmation != CONFIRMATION_PHRASE:
            raise DomainError(
                "DEMO_RESET_CONFIRMATION_REQUIRED",
                "Confirme explícitamente el reinicio de la demostración.",
            )

        fixture = load_official_demo_fixture()
        initial = Project(
            id=project_id,
            name=fixture.project.name,
            owner_session_id=owner_session_id,
            briefing=Briefing(
                problem=fixture.project.description,
                goal="Organizar requisitos semanales y sus evidencias",
            ),
            created_at=self._clock.now(),
            updated_at=self._clock.now(),
        )
        snapshot = self._projects.reset_demo(
            initial,
            owner_session_id=owner_session_id,
            idempotency_key=idempotency_key,
        )
        clean_snapshot = (
            snapshot.version == 1
            and snapshot.project.state.value == "idea"
            and snapshot.project.active_revision is None
            and not snapshot.revisions
            and not snapshot.approvals
            and not snapshot.artifacts
        )
        removed = self._remove_generated_project(project_id) if clean_snapshot else False
        reset_id = hashlib.sha256(
            f"{project_id}:{owner_session_id}:{idempotency_key}".encode()
        ).hexdigest()[:20]
        LOGGER.info(
            "demo_reset",
            extra={
                "reset_id": reset_id,
                "project_id": project_id,
                "fixture_id": fixture.fixture_id,
            },
        )
        return DemoResetResult(
            snapshot=snapshot,
            fixture_id=fixture.fixture_id,
            reset_id=reset_id,
            generated_files_removed=removed,
        )

    def _remove_generated_project(self, project_id: str) -> bool:
        target = (self._generated_root / project_id).resolve()
        try:
            target.relative_to(self._generated_root)
        except ValueError as error:
            raise DomainError(
                "DEMO_RESET_PATH_REJECTED",
                "La ruta de artefactos del proyecto no es segura.",
            ) from error
        if not target.exists():
            return False
        if not target.is_dir():
            raise DomainError(
                "DEMO_RESET_PATH_REJECTED",
                "La ruta de artefactos del proyecto no es un directorio seguro.",
            )
        shutil.rmtree(target)
        return True
