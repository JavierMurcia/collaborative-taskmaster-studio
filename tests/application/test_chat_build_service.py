from __future__ import annotations

import time
import zipfile
from io import BytesIO
from pathlib import Path

import pytest

from adapters.frameworks import (
    AntigravityGenerator,
    FrameworkGeneratorRegistry,
    GenAiSdkGenerator,
    GenkitGenerator,
)
from adapters.google_adk import GoogleAdkGenerator
from infrastructure.local.build_queue import JsonBuildQueueStore
from studio.application.agent_catalog import AgentCatalog
from studio.application.agent_runtime_service import AgentRuntimeResult, RuntimeStep
from studio.application.catalog_agent_execution_service import CatalogAgentExecutionService
from studio.application.chat_build_service import ChatBuildService, ChatBuildSnapshot
from studio.domain.errors import DomainError
from studio.ports.build_dispatcher import BuildOperation


def _draft() -> dict[str, object]:
    return {
        "name": "Extractor verificable",
        "purpose": "Extraer campos JSON de solicitudes y preparar un resultado verificable.",
        "intended_user": "Analista responsable",
        "inputs": ["Solicitud JSON"],
        "outputs": ["Resumen estructurado"],
        "workflow": ["Validar entrada", "Extraer campos", "Revisar resultado"],
        "external_actions": [],
        "constraints": ["No usar credenciales", "No usar red"],
        "approval_rule": "Una persona debe aprobar antes de completar.",
        "success_criteria": ["JSON válido", "Resultado revisado"],
        "missing_information": [],
        "readiness": 100,
        "ready_to_create": True,
    }


def _workspace_draft() -> dict[str, object]:
    draft = _draft()
    draft.update(
        {
            "name": "Lector de fuentes locales",
            "purpose": "Leer archivos de texto dentro del directorio asignado al agente.",
            "inputs": ["Documentos locales del workspace"],
            "workflow": ["Inspeccionar directorio", "Leer archivos", "Preparar resumen"],
            "external_actions": ["workspace.read"],
        }
    )
    return draft


def _service(
    tmp_path: Path,
    *,
    catalog: AgentCatalog | None = None,
    queue: JsonBuildQueueStore | None = None,
    dispatcher=None,
) -> ChatBuildService:
    root = tmp_path / "projects"
    registry = FrameworkGeneratorRegistry(
        (
            GoogleAdkGenerator(root),
            GenAiSdkGenerator(root),
            AntigravityGenerator(root),
            GenkitGenerator(root),
        )
    )
    return ChatBuildService(
        registry,
        root,
        step_delay_seconds=0,
        agent_catalog=catalog,
        build_queue=queue,
        dispatcher=dispatcher,
        external_dispatch_required=dispatcher is not None,
    )


def _wait(
    service: ChatBuildService,
    build_id: str,
    owner: str,
    expected: set[str],
) -> ChatBuildSnapshot:
    # Full-suite runs can briefly contend with other background builders on Windows.
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        snapshot = service.get(build_id, owner_session_id=owner)
        if snapshot.state in expected:
            return snapshot
        time.sleep(0.01)
    raise AssertionError(f"Build did not reach {expected}")


def test_chat_build_requires_confirmation_and_owner(tmp_path: Path) -> None:
    service = _service(tmp_path)

    with pytest.raises(DomainError, match="Confirma explícitamente"):
        service.start(_draft(), owner_session_id="owner_one", confirmation="NO")

    snapshot = service.start(
        _draft(), owner_session_id="owner_one", confirmation="CONSTRUIR_AGENTE"
    )

    with pytest.raises(DomainError, match="No existe"):
        service.get(snapshot.build_id, owner_session_id="owner_two")


def test_chat_build_waits_for_tests_then_saves_a_project_directory(tmp_path: Path) -> None:
    service = _service(tmp_path)
    started = service.start(
        _draft(), owner_session_id="owner_one", confirmation="CONSTRUIR_AGENTE"
    )
    waiting = _wait(service, started.build_id, "owner_one", {"awaiting_test_approval"})

    assert waiting.framework.framework == "google_adk"
    assert waiting.builder_runtime == "controlled_local_builder"
    assert waiting.events[-1].kind == "approval_required"
    assert waiting.download_ready is False
    assert len(waiting.contract_digest) == 64

    service.decide_tests(
        started.build_id,
        owner_session_id="owner_one",
        decision="approved",
    )
    completed = _wait(service, started.build_id, "owner_one", {"completed", "failed"})

    assert completed.state == "completed"
    assert len(completed.tests) == 3
    assert all(result.passed for result in completed.tests)
    assert completed.download_ready is True
    project_directory = Path(completed.project_directory)
    assert project_directory.parent == (tmp_path / "projects").resolve()
    assert (project_directory / "taskmaster.specification.json").is_file()
    assert (project_directory / "taskmaster.manifest.json").is_file()

    # The legacy package remains available through the API for backwards compatibility,
    # but the product's primary artifact is the persistent project directory above.
    filename, content = service.download(started.build_id, owner_session_id="owner_one")
    assert filename.endswith("-google_adk.zip")
    with zipfile.ZipFile(BytesIO(content)) as archive:
        assert "taskmaster.manifest.json" in archive.namelist()
        assert "app/agent.py" in archive.namelist()
        assert "studio-build-contract.json" in archive.namelist()
        assert "plugins.json" in archive.namelist()


def test_chat_build_can_be_stopped_before_tests(tmp_path: Path) -> None:
    service = _service(tmp_path)
    started = service.start(
        _draft(), owner_session_id="owner_one", confirmation="CONSTRUIR_AGENTE"
    )
    _wait(service, started.build_id, "owner_one", {"awaiting_test_approval"})

    stopped = service.decide_tests(
        started.build_id,
        owner_session_id="owner_one",
        decision="rejected",
    )

    assert stopped.state == "stopped"
    assert stopped.events[-1].kind == "stopped"
    with pytest.raises(DomainError, match="solo está disponible"):
        service.download(started.build_id, owner_session_id="owner_one")


def test_chat_build_is_restored_from_durable_queue_before_test_approval(
    tmp_path: Path,
) -> None:
    queue = JsonBuildQueueStore(tmp_path / "data")
    first = _service(tmp_path, queue=queue)
    started = first.start(
        _draft(), owner_session_id="owner_one", confirmation="CONSTRUIR_AGENTE"
    )
    waiting = _wait(first, started.build_id, "owner_one", {"awaiting_test_approval"})
    assert waiting.durable_queue is True

    restored_service = _service(tmp_path, queue=queue)
    restored = restored_service.get(started.build_id, owner_session_id="owner_one")
    assert restored.state == "awaiting_test_approval"
    assert restored.project_directory == waiting.project_directory

    restored_service.decide_tests(
        started.build_id,
        owner_session_id="owner_one",
        decision="approved",
    )
    completed = _wait(
        restored_service,
        started.build_id,
        "owner_one",
        {"completed", "failed"},
    )
    assert completed.state == "completed"


def test_external_dispatch_runs_each_phase_only_when_delivered(tmp_path: Path) -> None:
    class RecordingDispatcher:
        external = True

        def __init__(self) -> None:
            self.calls: list[tuple[str, BuildOperation, int]] = []

        def dispatch(self, build_id: str, operation: BuildOperation, attempt: int) -> str:
            self.calls.append((build_id, operation, attempt))
            return f"queues/taskmaster-builds/tasks/{build_id}-{operation}-a{attempt}"

    dispatcher = RecordingDispatcher()
    queue = JsonBuildQueueStore(tmp_path / "data")
    service = _service(tmp_path, queue=queue, dispatcher=dispatcher)
    started = service.start(
        _draft(), owner_session_id="owner_one", confirmation="CONSTRUIR_AGENTE"
    )
    assert started.state == "queued"
    assert dispatcher.calls == [(started.build_id, "construct", 0)]

    service.execute_dispatched(started.build_id, "construct")
    waiting = service.get(started.build_id, owner_session_id="owner_one")
    assert waiting.state == "awaiting_test_approval"
    service.decide_tests(started.build_id, owner_session_id="owner_one", decision="approved")
    assert dispatcher.calls[-1] == (started.build_id, "test", 0)

    service.execute_dispatched(started.build_id, "test")
    completed = service.get(started.build_id, owner_session_id="owner_one")
    assert completed.state == "completed"


def test_chat_build_adds_and_verifies_confined_workspace_reader(tmp_path: Path) -> None:
    service = _service(tmp_path)
    started = service.start(
        _workspace_draft(),
        owner_session_id="owner_one",
        confirmation="CONSTRUIR_AGENTE",
    )
    waiting = _wait(service, started.build_id, "owner_one", {"awaiting_test_approval"})

    assert waiting.framework.framework == "google_adk"

    service.decide_tests(
        started.build_id,
        owner_session_id="owner_one",
        decision="approved",
    )
    completed = _wait(service, started.build_id, "owner_one", {"completed", "failed"})

    assert completed.state == "completed"
    assert completed.capabilities == ("workspace.read",)
    assert len(completed.tests) == 5
    assert any(result.name == "Gateway de plugins cerrado por defecto" for result in completed.tests)
    assert any(result.name == "Lectura confinada del workspace" for result in completed.tests)
    assert completed.tests[-1].passed is True
    _, content = service.download(started.build_id, owner_session_id="owner_one")
    with zipfile.ZipFile(BytesIO(content)) as archive:
        assert "app/workspace.py" in archive.namelist()
        assert "tests/unit/test_workspace.py" in archive.namelist()
        tools = archive.read("app/tools.py").decode("utf-8")
        assert "def workspace_read" in tools


class _RuntimeSpy:
    def __init__(self) -> None:
        self.messages: list[str] = []

    def run_specification(self, specification, **kwargs) -> AgentRuntimeResult:
        del specification
        self.messages.append(kwargs["message"])
        return AgentRuntimeResult(
            run_id="run_1234567890abcdef",
            reply="Resultado ejecutado desde la carpeta persistente.",
            status="safe_preview",
            steps=(
                RuntimeStep(
                    name="Procesar solicitud",
                    status="simulated",
                    detail="Ejecución controlada dentro del Studio.",
                ),
            ),
            runtime_mode="local_fallback",
            model="runtime-de-prueba",
        )


def test_catalog_agent_runs_from_projects_and_keeps_its_own_memory(tmp_path: Path) -> None:
    catalog = AgentCatalog(tmp_path / "data")
    service = _service(tmp_path, catalog=catalog)
    started = service.start(
        _draft(), owner_session_id="owner_one", confirmation="CONSTRUIR_AGENTE"
    )
    _wait(service, started.build_id, "owner_one", {"awaiting_test_approval"})
    service.decide_tests(
        started.build_id,
        owner_session_id="owner_one",
        decision="approved",
    )
    completed = _wait(service, started.build_id, "owner_one", {"completed", "failed"})
    assert completed.state == "completed"

    agent = catalog.list("owner_one")[0]
    runtime = _RuntimeSpy()
    execution = CatalogAgentExecutionService(catalog, runtime, tmp_path / "projects")
    execution.run(
        agent.id,
        message="Primera solicitud",
        owner_session_id="owner_one",
        idempotency_key="first-run",
    )
    execution.run(
        agent.id,
        message="Segunda solicitud",
        owner_session_id="owner_one",
        idempotency_key="second-run",
    )

    project_directory = Path(agent.artifact_directory)
    state = (project_directory / "runtime-state.json").read_text(encoding="utf-8")
    assert "Primera solicitud" in state
    assert "Segunda solicitud" in state
    assert "Contexto persistente de ejecuciones anteriores" in runtime.messages[1]
