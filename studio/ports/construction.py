"""Port for the engine that orchestrates an approved agent construction."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Literal, Protocol

from studio.domain.models import TaskmasterSpecification
from studio.ports.generator import GeneratedBundle, GeneratorAdapter

BuilderRuntime = Literal["antigravity_sdk", "controlled_local_builder"]
ConstructionProgress = Callable[[str, str, Literal["running", "passed"]], None]


class ConstructionOrchestrator(Protocol):
    """Construct a project from an immutable, human-approved contract."""

    runtime_id: BuilderRuntime

    def construct(
        self,
        specification: TaskmasterSpecification,
        destination: Path,
        *,
        generator: GeneratorAdapter,
        contract: Mapping[str, object],
        progress: ConstructionProgress,
    ) -> GeneratedBundle: ...


class ControlledConstructionOrchestrator:
    """Deterministic local builder used when no autonomous runtime is active."""

    runtime_id: BuilderRuntime = "controlled_local_builder"

    def construct(
        self,
        specification: TaskmasterSpecification,
        destination: Path,
        *,
        generator: GeneratorAdapter,
        contract: Mapping[str, object],
        progress: ConstructionProgress,
    ) -> GeneratedBundle:
        del contract
        progress("generation", "Generando el proyecto mediante plantillas verificadas…", "running")
        bundle = generator.generate(specification, destination)
        progress("generation", "Plantilla funcional generada y confinada.", "passed")
        return bundle
