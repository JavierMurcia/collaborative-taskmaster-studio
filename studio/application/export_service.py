"""Build a downloadable, reproducible ZIP from a laboratory-approved agent."""

from __future__ import annotations

import io
import re
import shutil
import uuid
import zipfile
from pathlib import Path, PurePosixPath

from pydantic import BaseModel, ConfigDict

from studio.application.evaluation_service import require_exportable
from studio.application.project_service import ProjectService
from studio.domain.errors import DomainError
from studio.domain.models import ProjectSnapshot, TaskmasterSpecification
from studio.ports.generator import GeneratorAdapter
from studio.ports.repositories import ProjectRepository


class ExportedAgent(BaseModel):
    model_config = ConfigDict(frozen=True)

    filename: str
    content: bytes
    file_count: int
    framework: str
    revision: int


class AgentExportService:
    """Rehydrate the approved revision and package it without trusting ephemeral disk."""

    def __init__(
        self,
        projects: ProjectRepository,
        adapter: GeneratorAdapter,
        generated_root: Path,
    ) -> None:
        self._projects = projects
        self._adapter = adapter
        self._root = generated_root.resolve()

    def export(self, project_id: str, *, owner_session_id: str) -> ExportedAgent:
        snapshot = self._projects.get(project_id, owner_session_id=owner_session_id)
        ProjectService.ensure_owner(snapshot, owner_session_id)
        require_exportable(snapshot)
        specification = self._approved_specification(snapshot)
        destination = self._root / ".exports" / f"{project_id}-{uuid.uuid4().hex}"
        try:
            bundle = self._adapter.generate(specification, destination)
            content = self._zip(bundle.output_directory)
        finally:
            if destination.exists():
                shutil.rmtree(destination)
        slug = re.sub(r"[^a-z0-9]+", "-", snapshot.project.name.casefold()).strip("-")
        return ExportedAgent(
            filename=f"{slug or 'taskmaster'}-r{specification.revision}.zip",
            content=content,
            file_count=len(bundle.files) + 1,
            framework=specification.generation.target_framework,
            revision=specification.revision,
        )

    @staticmethod
    def _approved_specification(snapshot: ProjectSnapshot) -> TaskmasterSpecification:
        revision = snapshot.project.active_revision
        stored = next((item for item in snapshot.revisions if item.number == revision), None)
        if stored is None:
            raise DomainError("REVISION_NOT_FOUND", "No existe una revisión aprobada para exportar.")
        return stored.specification

    @staticmethod
    def _zip(root: Path) -> bytes:
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for path in sorted(item for item in root.rglob("*") if item.is_file()):
                relative = PurePosixPath(path.relative_to(root).as_posix())
                if relative.is_absolute() or ".." in relative.parts:
                    raise DomainError("EXPORT_PATH_ESCAPE", "El paquete contiene una ruta insegura.")
                archive.writestr(relative.as_posix(), path.read_bytes())
        return buffer.getvalue()
