"""Port implemented by framework-specific project generators."""

from __future__ import annotations

from pathlib import Path
from typing import Protocol

from pydantic import BaseModel, ConfigDict

from studio.domain.models import TaskmasterSpecification


class GeneratedFile(BaseModel):
    model_config = ConfigDict(frozen=True)

    relative_path: str
    sha256: str
    size_bytes: int


class GeneratedBundle(BaseModel):
    model_config = ConfigDict(frozen=True)

    output_directory: Path
    manifest_path: Path
    template_version: str
    files: tuple[GeneratedFile, ...]


class GeneratorAdapter(Protocol):
    framework: str
    template_version: str

    def validate_capabilities(self, specification: TaskmasterSpecification) -> None: ...

    def generate(
        self,
        specification: TaskmasterSpecification,
        destination: Path,
    ) -> GeneratedBundle: ...
