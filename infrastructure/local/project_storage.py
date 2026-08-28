"""Local project storage implementation used by development and tests."""

from __future__ import annotations

import hashlib
from pathlib import Path

from studio.domain.errors import DomainError
from studio.ports.project_storage import StoredProject


class LocalProjectArtifactStore:
    """Keep the canonical project in projects/ while honoring the storage port."""

    def persist_directory(
        self,
        *,
        owner_session_id: str,
        project_id: str,
        build_id: str,
        directory: Path,
    ) -> StoredProject:
        del owner_session_id, project_id, build_id
        root = directory.resolve()
        if not root.is_dir():
            raise DomainError("PROJECT_DIRECTORY_MISSING", "La carpeta del proyecto no existe.")
        digest, count, total = _directory_digest(root)
        return StoredProject(root.as_uri(), digest, count, total)

    def restore_directory(
        self,
        *,
        owner_session_id: str,
        uri: str,
        directory: Path,
        expected_digest: str,
    ) -> None:
        del owner_session_id, uri, expected_digest
        if not directory.resolve().is_dir():
            raise DomainError(
                "PROJECT_ARTIFACT_UNAVAILABLE",
                "El proyecto local ya no está disponible y no existe una copia cloud activa.",
            )

    def persist_file(
        self,
        *,
        owner_session_id: str,
        uri: str,
        relative_path: str,
        source: Path,
    ) -> None:
        del owner_session_id, uri, relative_path
        if not source.is_file():
            raise DomainError("PROJECT_FILE_MISSING", "El archivo del proyecto no existe.")


def _directory_digest(root: Path) -> tuple[str, int, int]:
    digest = hashlib.sha256()
    count = 0
    total = 0
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        relative = path.relative_to(root).as_posix()
        content = path.read_bytes()
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(hashlib.sha256(content).digest())
        count += 1
        total += len(content)
    return digest.hexdigest(), count, total

