from __future__ import annotations

import hashlib
import json
import py_compile
import subprocess
import sys
import tomllib
from datetime import datetime
from pathlib import Path, PurePosixPath

import pytest

from adapters.google_adk import GoogleAdkGenerator
from infrastructure.local.clock import FrozenClock
from infrastructure.local.repositories import InMemoryRepository
from studio.application.approval_service import ApprovalService
from studio.application.design_service import DesignService
from studio.application.generation_service import GenerationService
from studio.domain.enums import ProjectState
from studio.domain.errors import DomainError
from studio.domain.models import Briefing, Project

PROJECT_ID = "academic_delivery_project"
OWNER = "demo_user"
NOW = datetime.fromisoformat("2026-08-13T16:25:00-05:00")
FEEDBACK = (
    "No quiero que el agente envíe nada ni modifique calendarios. Solo debe preparar el "
    "paquete y esperar mi aprobación. También quiero una prueba que compruebe que una "
    "instrucción dentro de los requisitos no pueda saltarse esta regla."
)


def approved_services(
    generated_root: Path,
) -> tuple[InMemoryRepository, GenerationService]:
    clock = FrozenClock(NOW)
    repository = InMemoryRepository(clock)
    project = Project(
        id=PROJECT_ID,
        name="Coordinador de entrega académica",
        owner_session_id=OWNER,
        state=ProjectState.BRIEFING_CONFIRMED,
        briefing=Briefing(
            problem="Las evidencias académicas pueden quedar incompletas.",
            goal="Preparar un paquete semanal verificable.",
            deadline="Viernes 18:00",
            available_hours=2,
            input_format="Lista escrita por el estudiante",
            outputs=["Plan semanal", "Paquete de evidencias"],
            external_actions="none",
            approval_owner="Estudiante",
            success_criteria=["Cada requisito tiene evidencia", "El estudiante aprueba"],
            confirmed=True,
            confirmed_by=OWNER,
            confirmed_at=NOW,
        ),
    )
    repository.create(project, idempotency_key="create-h6")
    design = DesignService(repository, repository, clock)
    approval = ApprovalService(repository, repository, clock)
    design.create_initial_revision(
        PROJECT_ID, owner_session_id=OWNER, idempotency_key="design-one"
    )
    design.apply_feedback(
        PROJECT_ID,
        expected_revision=1,
        feedback=FEEDBACK,
        owner_session_id=OWNER,
        idempotency_key="feedback-two",
    )
    approval.decide(
        PROJECT_ID,
        revision=2,
        decision="approved",
        actor_id=OWNER,
        actor_type="human",
        note="Aprobado para generación.",
        approval_id="approval_h6",
        owner_session_id=OWNER,
        idempotency_key="approve-h6",
    )
    return (
        repository,
        GenerationService(
            repository,
            repository,
            clock,
            GoogleAdkGenerator(generated_root),
            generated_root,
        ),
    )


def test_generates_official_adk_project_with_verified_manifest(tmp_path: Path) -> None:
    generated_root = tmp_path / "generated"
    _, service = approved_services(generated_root)

    result = service.generate(
        PROJECT_ID,
        revision=2,
        owner_session_id=OWNER,
        idempotency_key="generate-h6",
    )

    output = generated_root / result.output_relative_path
    assert result.snapshot.project.state is ProjectState.GENERATING
    assert result.artifact.validation_status == "valid"
    assert result.manifest["revision"] == 2
    assert result.manifest["template_version"] == "1.0.0"
    paths = {item["relative_path"] for item in result.manifest["files"]}
    assert {
        "app/agent.py",
        "app/tools.py",
        "app/policies.py",
        "tests/eval/test_scenarios.json",
        "README.md",
        "ARCHITECTURE.md",
        "Dockerfile",
        "agents-cli-manifest.yaml",
    } <= paths
    for item in result.manifest["files"]:
        relative = PurePosixPath(item["relative_path"])
        assert not relative.is_absolute()
        assert ".." not in relative.parts
        payload = output.joinpath(*relative.parts).read_bytes()
        assert hashlib.sha256(payload).hexdigest() == item["sha256"]
    for source in output.rglob("*.py"):
        py_compile.compile(str(source), doraise=True)
    with (output / "pyproject.toml").open("rb") as stream:
        project = tomllib.load(stream)
    assert "google-adk[gcp]>=2.0.0,<3.0.0" in project["project"]["dependencies"]
    scenarios = json.loads((output / "tests/eval/test_scenarios.json").read_text("utf-8"))
    assert {item["category"] for item in scenarios["scenarios"]} >= {
        "happy_path",
        "security",
    }
    import_probe = """
import sys, types
class Agent:
    def __init__(self, **kwargs): self.kwargs = kwargs
class App:
    def __init__(self, **kwargs): self.kwargs = kwargs
class Gemini:
    def __init__(self, **kwargs): self.kwargs = kwargs
class HttpRetryOptions:
    def __init__(self, **kwargs): self.kwargs = kwargs
modules = {
    'google': types.ModuleType('google'),
    'google.adk': types.ModuleType('google.adk'),
    'google.adk.agents': types.ModuleType('google.adk.agents'),
    'google.adk.apps': types.ModuleType('google.adk.apps'),
    'google.adk.models': types.ModuleType('google.adk.models'),
    'google.genai': types.ModuleType('google.genai'),
}
modules['google.adk.agents'].Agent = Agent
modules['google.adk.apps'].App = App
modules['google.adk.models'].Gemini = Gemini
modules['google.genai'].types = types.SimpleNamespace(HttpRetryOptions=HttpRetryOptions)
sys.modules.update(modules)
import app
assert app.root_agent.kwargs['name'] == 'root_agent'
assert app.app.kwargs['name'] == 'app'
"""
    imported = subprocess.run(
        [sys.executable, "-c", import_probe],
        cwd=output,
        capture_output=True,
        text=True,
        check=False,
    )
    assert imported.returncode == 0, imported.stderr


def test_identical_generation_is_reused_without_overwrite(tmp_path: Path) -> None:
    generated_root = tmp_path / "generated"
    _, service = approved_services(generated_root)
    first = service.generate(
        PROJECT_ID,
        revision=2,
        owner_session_id=OWNER,
        idempotency_key="generate-first",
    )
    manifest = generated_root / first.artifact.relative_path
    first_payload = manifest.read_bytes()

    replay = service.generate(
        PROJECT_ID,
        revision=2,
        owner_session_id=OWNER,
        idempotency_key="generate-second",
    )

    assert replay.reused
    assert replay.artifact.id == first.artifact.id
    assert manifest.read_bytes() == first_payload


def test_generator_rejects_path_escape_and_existing_output(tmp_path: Path) -> None:
    generated_root = tmp_path / "generated"
    repository, _ = approved_services(generated_root)
    specification = repository.get(PROJECT_ID).revisions[-1].specification
    adapter = GoogleAdkGenerator(generated_root)
    with pytest.raises(DomainError) as escaped:
        adapter.generate(specification, tmp_path / "outside")
    assert escaped.value.code == "GENERATION_PATH_ESCAPE"

    existing = generated_root / PROJECT_ID / "revision-2"
    existing.mkdir(parents=True)
    marker = existing / "keep.txt"
    marker.write_text("preserve", encoding="utf-8")
    with pytest.raises(DomainError) as collision:
        adapter.generate(specification, existing)
    assert collision.value.code == "GENERATION_OUTPUT_EXISTS"
    assert marker.read_text(encoding="utf-8") == "preserve"


def test_generation_requires_human_approved_revision(tmp_path: Path) -> None:
    generated_root = tmp_path / "generated"
    repository, service = approved_services(generated_root)
    snapshot = repository.get(PROJECT_ID)
    repository.save(
        snapshot.project.model_copy(update={"state": ProjectState.DESIGN_IN_REVIEW}),
        expected_version=snapshot.version,
        idempotency_key="reopen-for-test",
    )

    with pytest.raises(DomainError) as captured:
        service.generate(
            PROJECT_ID,
            revision=2,
            owner_session_id=OWNER,
            idempotency_key="generate-unapproved",
        )
    assert captured.value.code == "GENERATION_REQUIRES_APPROVAL"
    assert not generated_root.exists()
