from __future__ import annotations

import asyncio
import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path

import pytest

from adapters.antigravity import builder as antigravity_builder
from adapters.antigravity.builder import (
    AntigravitySdkOrchestrator,
    _ConfinedWorkspace,
    _SdkBindings,
    orchestrate_workspace,
)
from adapters.google_adk import GoogleAdkGenerator
from studio.application.builder_readiness import inspect_builder_readiness
from studio.application.official_designer import OfficialAcademicDesigner
from studio.domain.errors import DomainError
from studio.domain.models import Briefing, TaskmasterSpecification


def _specification() -> TaskmasterSpecification:
    briefing = Briefing(
        problem="Los informes se preparan manualmente.",
        goal="Preparar un informe verificable.",
        desired_result="Borrador listo para revisión humana.",
        actors=["Analista"],
        inputs=["Solicitud confirmada"],
        constraints=["No usar red", "No usar credenciales"],
        scope_in=["Validar entrada", "Preparar borrador"],
        scope_out=["Publicar el informe"],
        approvals=["Una persona aprueba el resultado"],
        success_criteria=["Borrador revisable"],
        available_hours=1,
        input_format="Texto",
        outputs=["Informe"],
        external_actions="none",
        approval_owner="Analista",
        confirmed=True,
        confirmed_by="owner",
        confirmed_at=datetime(2026, 8, 26, tzinfo=UTC),
    )
    specification = OfficialAcademicDesigner().initial_design(
        project_id="agent_antigravity_test",
        briefing=briefing,
        now=datetime(2026, 8, 26, tzinfo=UTC),
    )
    return specification.model_copy(
        update={
            "generation": specification.generation.model_copy(
                update={
                    "target_framework": "google_adk",
                    "language": "python",
                    "template_version": "1.0.0",
                }
            )
        },
        deep=True,
    )


def test_antigravity_orchestrator_uses_only_confined_tools_and_records_evidence(
    tmp_path: Path,
) -> None:
    root = tmp_path / "generated"
    progress: list[tuple[str, str]] = []

    def fake_runner(command: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
        assert command[:3] == ["isolated-python", "-m", "adapters.antigravity.worker"]
        # Cloud Build checks the repository out at /workspace, while local
        # development commonly uses a directory named sentinel-taskmaster.
        # Verify the runner receives the actual repository root instead of
        # coupling the test to a checkout directory name.
        assert (cwd / "adapters" / "antigravity" / "worker.py").is_file()
        request = json.loads(Path(command[3]).read_text(encoding="utf-8"))
        workspace = Path(request["workspace"])
        (workspace / "ANTIGRAVITY.md").write_text(
            "# Construcción observable\n", encoding="utf-8"
        )
        (workspace / ".studio" / "antigravity-orchestration.json").write_text(
            json.dumps(
                {
                    "runtime": "antigravity_sdk",
                    "sdk_version": "0.1.15",
                    "summary": "Proyecto inspeccionado.",
                    "operations": [
                        {"tool": "list_project_files", "count": 10},
                        {"tool": "read_project_file", "path": "README.md"},
                        {"tool": "write_project_file", "path": "ANTIGRAVITY.md"},
                    ],
                }
            ),
            encoding="utf-8",
        )
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    orchestrator = AntigravitySdkOrchestrator("isolated-python", runner=fake_runner)

    bundle = orchestrator.construct(
        _specification(),
        root / "build-one",
        generator=GoogleAdkGenerator(root),
        contract={"sha256": "approved-contract"},
        progress=lambda phase, _message, status: progress.append((phase, status)),
    )

    assert (bundle.output_directory / "ANTIGRAVITY.md").is_file()
    evidence = json.loads(
        (bundle.output_directory / ".studio" / "antigravity-orchestration.json").read_text(
            encoding="utf-8"
        )
    )
    assert evidence["runtime"] == "antigravity_sdk"
    assert evidence["sdk_version"] == "0.1.15"
    assert [item["tool"] for item in evidence["operations"]] == [
        "list_project_files",
        "read_project_file",
        "write_project_file",
    ]
    manifest = json.loads(bundle.manifest_path.read_text(encoding="utf-8"))
    assert manifest["orchestrator"] == "antigravity_sdk"
    assert ("orchestration", "passed") in progress
    assert not (bundle.output_directory / ".studio" / "antigravity-request.json").exists()


def test_antigravity_workspace_rejects_path_escape_and_secret_material(tmp_path: Path) -> None:
    workspace = _ConfinedWorkspace(tmp_path)

    with pytest.raises(ValueError, match="relative paths"):
        workspace.write_project_file("../escape.py", "unsafe")
    with pytest.raises(ValueError, match="secret material"):
        workspace.write_project_file("config.txt", "sk-123456789012345678901234567890")


def test_antigravity_removes_an_incomplete_scaffold_after_worker_failure(tmp_path: Path) -> None:
    root = tmp_path / "generated"
    destination = root / "failed-build"

    def failed_runner(command: list[str], _cwd: Path) -> subprocess.CompletedProcess[str]:
        error_path = Path(command[-1]).parent / "antigravity-error.json"
        error_path.write_text(
            json.dumps({"error_type": "PermissionError", "message": "Vertex denied"}),
            encoding="utf-8",
        )
        return subprocess.CompletedProcess(command, 1, stdout="", stderr="safe failure")

    orchestrator = AntigravitySdkOrchestrator("isolated-python", runner=failed_runner)

    with pytest.raises(DomainError, match="trabajador aislado") as captured:
        orchestrator.construct(
            _specification(),
            destination,
            generator=GoogleAdkGenerator(root),
            contract={"sha256": "approved-contract"},
            progress=lambda *_args: None,
        )

    assert captured.value.context == {
        "return_code": 1,
        "worker_error_type": "PermissionError",
        "worker_message": "Vertex denied",
    }
    assert not destination.exists()


def test_antigravity_starts_with_deny_all_before_allowing_confined_tools(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: dict[str, object] = {}

    class FakeResponse:
        async def text(self) -> str:
            return "Proyecto inspeccionado."

    class FakeAgent:
        def __init__(self, config: dict[str, object]) -> None:
            captured.update(config)

        async def __aenter__(self) -> FakeAgent:
            return self

        async def __aexit__(self, *_args: object) -> None:
            return None

        async def chat(self, _prompt: str) -> FakeResponse:
            tools = captured["tools"]
            assert isinstance(tools, list)
            tools[0]()
            return FakeResponse()

    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "verified-project")
    monkeypatch.setenv("STUDIO_ANTIGRAVITY_VERTEX_LOCATION", "us-central1")
    monkeypatch.setenv("STUDIO_ANTIGRAVITY_MODEL", "gemini-2.5-flash")
    monkeypatch.setattr(
        antigravity_builder,
        "_load_sdk",
        lambda: _SdkBindings(
            agent=FakeAgent,
            config=lambda **values: values,
            deny_all=lambda: "deny-all",
            allow=lambda name: f"allow:{name}",
        ),
    )

    result = asyncio.run(
        orchestrate_workspace(
            _ConfinedWorkspace(tmp_path),
            {"purpose": "Preparar un informe"},
            {"sha256": "approved-contract"},
        )
    )

    assert result == "Proyecto inspeccionado."
    assert captured["policies"] == [
        "deny-all",
        "allow:list_project_files",
        "allow:read_project_file",
        "allow:write_project_file",
    ]
    assert captured["model"] == "gemini-2.5-flash"


def test_readiness_activates_only_a_verified_isolated_python(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    worker_python = tmp_path / "python.exe"
    worker_python.write_bytes(b"placeholder")
    monkeypatch.setenv("STUDIO_AGENT_BUILDER", "antigravity")
    monkeypatch.setenv("STUDIO_ANTIGRAVITY_PYTHON", str(worker_python))
    monkeypatch.setattr(
        "studio.application.builder_readiness.subprocess.run",
        lambda *args, **kwargs: subprocess.CompletedProcess(args, 0),
    )

    readiness = inspect_builder_readiness()

    assert readiness.active_builder == "antigravity"
    antigravity = next(item for item in readiness.capabilities if item.id == "antigravity")
    assert antigravity.status == "active"
