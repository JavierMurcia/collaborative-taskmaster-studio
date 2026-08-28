"""Durable storage boundary for generated Taskmaster project directories."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol


@dataclass(frozen=True, slots=True)
class StoredProject:
    uri: str
    digest: str
    file_count: int
    total_bytes: int


class ProjectArtifactStore(Protocol):
    def persist_directory(
        self,
        *,
        owner_session_id: str,
        project_id: str,
        build_id: str,
        directory: Path,
    ) -> StoredProject: ...

    def restore_directory(
        self,
        *,
        owner_session_id: str,
        uri: str,
        directory: Path,
        expected_digest: str,
    ) -> None: ...

    def persist_file(
        self,
        *,
        owner_session_id: str,
        uri: str,
        relative_path: str,
        source: Path,
    ) -> None: ...

