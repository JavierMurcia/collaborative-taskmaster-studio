from __future__ import annotations

import json
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

from adapters.controlled.builder import (
    IsolatedControlledConstructionOrchestrator,
    _credential_free_environment,
)
from adapters.google_adk import GoogleAdkGenerator
from studio.application.official_designer import OfficialAcademicDesigner
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
        confirmed_at=datetime(2026, 8, 28, tzinfo=UTC),
    )
    specification = OfficialAcademicDesigner().initial_design(
        project_id="isolated_builder_test",
        briefing=briefing,
        now=datetime(2026, 8, 28, tzinfo=UTC),
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


def test_controlled_builder_runs_through_isolated_worker_contract(tmp_path: Path) -> None:
    root = tmp_path / "projects"
    destination = root / "reporter"

    def fake_runner(command: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
        assert command[:3] == ["isolated-python", "-m", "adapters.controlled.worker"]
        assert (cwd / "adapters" / "controlled" / "worker.py").is_file()
        request = json.loads(Path(command[3]).read_text(encoding="utf-8"))
        specification = TaskmasterSpecification.model_validate(request["specification"])
        bundle = GoogleAdkGenerator(Path(request["root"])).generate(
            specification,
            Path(request["destination"]),
        )
        evidence = bundle.output_directory / ".studio" / "isolated-builder.json"
        evidence.parent.mkdir(parents=True)
        evidence.write_text('{"runtime":"isolated_controlled_builder"}\n')
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    orchestrator = IsolatedControlledConstructionOrchestrator(
        "isolated-python",
        runner=fake_runner,
    )
    bundle = orchestrator.construct(
        _specification(),
        destination,
        generator=GoogleAdkGenerator(root),
        contract={"sha256": "approved-contract"},
        progress=lambda _phase, _message, _status: None,
    )

    assert orchestrator.runtime_id == "isolated_controlled_builder"
    assert (bundle.output_directory / "app" / "agent.py").is_file()
    assert (bundle.output_directory / ".studio" / "isolated-builder.json").is_file()


def test_isolated_worker_does_not_inherit_cloud_credentials(monkeypatch) -> None:
    monkeypatch.setenv("GOOGLE_APPLICATION_CREDENTIALS", "/secret/key.json")
    monkeypatch.setenv("GEMINI_API_KEY", "secret")
    monkeypatch.setenv("STUDIO_GOOGLE_OAUTH_CLIENT_SECRET", "secret")

    environment = _credential_free_environment()

    assert "GOOGLE_APPLICATION_CREDENTIALS" not in environment
    assert "GEMINI_API_KEY" not in environment
    assert "STUDIO_GOOGLE_OAUTH_CLIENT_SECRET" not in environment
    assert environment["STUDIO_ISOLATED_BUILD_WORKER"] == "true"


def test_isolated_builder_executes_real_worker_process(tmp_path: Path) -> None:
    root = tmp_path / "projects"
    destination = root / "real-worker"
    orchestrator = IsolatedControlledConstructionOrchestrator(sys.executable)

    bundle = orchestrator.construct(
        _specification(),
        destination,
        generator=GoogleAdkGenerator(root),
        contract={"sha256": "approved-contract"},
        progress=lambda _phase, _message, _status: None,
    )

    evidence = json.loads(
        (bundle.output_directory / ".studio" / "isolated-builder.json").read_text(
            encoding="utf-8"
        )
    )
    assert evidence["runtime"] == "isolated_controlled_builder"
    assert evidence["credentials_available"] is False
    assert evidence["contract_sha256"] == "approved-contract"
