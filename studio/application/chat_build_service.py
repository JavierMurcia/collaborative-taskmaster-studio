"""Asynchronous, auditable construction sessions started from the collaborative chat."""

from __future__ import annotations

import hashlib
import io
import json
import re
import shutil
import threading
import time
import uuid
import zipfile
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Literal, cast

from pydantic import BaseModel, ConfigDict, Field

from infrastructure.local.project_storage import LocalProjectArtifactStore
from studio.application.agent_catalog import AgentCatalogRepository
from studio.application.capability_selection import requires_workspace_read
from studio.application.collaborative_chat_service import AgentDraft
from studio.application.framework_selector import FrameworkRecommendation, select_framework
from studio.application.official_designer import OfficialAcademicDesigner
from studio.application.plugin_registry import PluginRegistry, PluginSelection
from studio.domain.enums import ApprovalStatus
from studio.domain.errors import DomainError
from studio.domain.models import Approval, Briefing, Policy, TaskmasterSpecification, Tool
from studio.ports.build_dispatcher import BuildDispatcher, BuildOperation
from studio.ports.build_queue import BuildQueueStore
from studio.ports.construction import (
    BuilderRuntime,
    ConstructionOrchestrator,
    ControlledConstructionOrchestrator,
)
from studio.ports.generator import GeneratorAdapter
from studio.ports.project_storage import ProjectArtifactStore

BuildState = Literal[
    "queued",
    "building",
    "awaiting_test_approval",
    "testing",
    "completed",
    "failed",
    "stopped",
]
BuildEventKind = Literal[
    "status",
    "artifact",
    "approval_required",
    "test",
    "completed",
    "failed",
    "stopped",
]
BuildEventStatus = Literal["running", "passed", "waiting", "failed", "stopped"]


class BuildEvent(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    sequence: int = Field(ge=1)
    kind: BuildEventKind
    phase: str
    message: str
    status: BuildEventStatus
    transient: bool = True
    occurred_at: datetime
    details: dict[str, object] = Field(default_factory=dict)


class BuildTestResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str
    passed: bool
    detail: str


class AgentBuildContract(BaseModel):
    """Immutable handoff between Gemini's design and the deterministic builder."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["1.0.0"] = "1.0.0"
    project_id: str
    agent: AgentDraft
    framework: FrameworkRecommendation
    plugins: tuple[PluginSelection, ...] = ()
    confirmed_by: str
    confirmed_at: datetime
    sha256: str


class ChatBuildSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    build_id: str
    project_id: str
    state: BuildState
    builder: str
    builder_runtime: BuilderRuntime
    framework: FrameworkRecommendation
    agent_name: str
    capabilities: tuple[str, ...] = ()
    plugins: tuple[PluginSelection, ...] = ()
    contract_digest: str
    events: tuple[BuildEvent, ...]
    file_count: int = 0
    tests: tuple[BuildTestResult, ...] = ()
    download_ready: bool = False
    catalog_agent_id: str = ""
    project_directory: str = ""
    error: str = ""
    queue_attempt: int = 0
    queue_max_attempts: int = 2
    durable_queue: bool = False


class _BuildRecord:
    def __init__(
        self,
        *,
        build_id: str,
        project_id: str,
        owner_session_id: str,
        draft: AgentDraft,
        framework: FrameworkRecommendation,
        contract: AgentBuildContract,
        plugins: tuple[PluginSelection, ...],
        builder_runtime: BuilderRuntime,
    ) -> None:
        self.build_id = build_id
        self.project_id = project_id
        self.owner_session_id = owner_session_id
        self.draft = draft
        self.framework = framework
        self.contract = contract
        self.plugins = plugins
        self.builder_runtime = builder_runtime
        self.state: BuildState = "queued"
        self.events: list[BuildEvent] = []
        self.file_count = 0
        self.tests: tuple[BuildTestResult, ...] = ()
        self.output_directory: Path | None = None
        self.error = ""
        self.catalog_agent_id = ""
        self.artifact_uri = ""
        self.artifact_digest = ""
        self.attempts = 0
        self.max_attempts = 2


class ChatBuildService:
    """Build approved chat drafts while exposing only observable work, never hidden reasoning."""

    def __init__(
        self,
        adapter: GeneratorAdapter,
        generated_root: Path,
        *,
        orchestrator: ConstructionOrchestrator | None = None,
        step_delay_seconds: float = 0.12,
        executor: ThreadPoolExecutor | None = None,
        now: Callable[[], datetime] | None = None,
        plugin_registry: PluginRegistry | None = None,
        agent_catalog: AgentCatalogRepository | None = None,
        project_store: ProjectArtifactStore | None = None,
        build_queue: BuildQueueStore | None = None,
        dispatcher: BuildDispatcher | None = None,
        external_dispatch_required: bool = False,
        builder_runtime: BuilderRuntime | None = None,
        max_attempts: int = 2,
    ) -> None:
        self._adapter = adapter
        self._root = generated_root.resolve()
        self._root.mkdir(parents=True, exist_ok=True)
        self._orchestrator = orchestrator or ControlledConstructionOrchestrator()
        self._delay = max(0.0, step_delay_seconds)
        self._executor = executor or ThreadPoolExecutor(
            max_workers=2, thread_name_prefix="taskmaster-builder"
        )
        self._now = now or (lambda: datetime.now(UTC))
        self._plugins = plugin_registry or PluginRegistry()
        self._catalog = agent_catalog
        self._project_store = project_store or LocalProjectArtifactStore()
        self._build_queue = build_queue
        self._dispatcher = dispatcher
        self._external_dispatch_required = external_dispatch_required
        self._builder_runtime = builder_runtime or self._orchestrator.runtime_id
        self._max_attempts = min(3, max(1, max_attempts))
        self._records: dict[str, _BuildRecord] = {}
        self._lock = threading.RLock()
        self._resume_pending()

    def readiness(self) -> dict[str, object]:
        return {
            "durable_queue": bool(self._build_queue and self._build_queue.durable),
            "worker_isolated": self._builder_runtime
            in {"isolated_controlled_builder", "antigravity_sdk"},
            "runtime": self._builder_runtime,
            "max_attempts": self._max_attempts,
            "restart_recovery": self._build_queue is not None,
            "external_dispatch": bool(self._dispatcher and self._dispatcher.external),
            "external_dispatch_required": self._external_dispatch_required,
            "dispatcher": (
                "cloud_tasks"
                if self._dispatcher
                else ("unavailable" if self._external_dispatch_required else "in_process")
            ),
        }

    def start(
        self,
        draft_payload: dict[str, object],
        *,
        owner_session_id: str,
        confirmation: str,
    ) -> ChatBuildSnapshot:
        if confirmation != "CONSTRUIR_AGENTE":
            raise DomainError(
                "BUILD_CONFIRMATION_REQUIRED",
                "Confirma explícitamente el diseño antes de entregarlo al ingeniero.",
            )
        draft = AgentDraft.model_validate(draft_payload)
        if not draft.ready_to_create or not draft.name or not draft.purpose:
            raise DomainError(
                "AGENT_DRAFT_NOT_READY",
                "Gemini debe completar el diseño antes de iniciar la construcción.",
                context={"readiness": draft.readiness},
            )
        recommendation = select_framework(
            purpose=draft.purpose,
            workflow=draft.workflow,
            external_actions=draft.external_actions,
            inputs=draft.inputs,
            outputs=draft.outputs,
            constraints=draft.constraints,
        )
        build_id = f"build_{uuid.uuid4().hex[:16]}"
        project_id = f"agent_{hashlib.sha256(build_id.encode()).hexdigest()[:16]}"
        plugin_selection = self._plugins.select(
            purpose=draft.purpose,
            workflow=draft.workflow,
            inputs=draft.inputs,
            outputs=draft.outputs,
            external_actions=draft.external_actions,
        )
        contract = _build_contract(
            project_id=project_id,
            draft=draft,
            framework=recommendation,
            plugins=plugin_selection,
            confirmed_by=owner_session_id,
            confirmed_at=self._now(),
        )
        record = _BuildRecord(
            build_id=build_id,
            project_id=project_id,
            owner_session_id=owner_session_id,
            draft=draft,
            framework=recommendation,
            contract=contract,
            plugins=plugin_selection,
            builder_runtime=self._builder_runtime,
        )
        record.max_attempts = self._max_attempts
        with self._lock:
            self._records[build_id] = record
            self._event(
                record,
                kind="status",
                phase="handoff",
                message="Diseño aprobado y entregado al Ingeniero de agentes.",
                status="passed",
                transient=False,
            )
        self._dispatch(build_id, "construct")
        return self._snapshot(record)

    def get(
        self,
        build_id: str,
        *,
        owner_session_id: str,
        after_sequence: int = 0,
    ) -> ChatBuildSnapshot:
        with self._lock:
            record = self._authorized(build_id, owner_session_id)
            snapshot = self._snapshot(record)
        if after_sequence <= 0:
            return snapshot
        return snapshot.model_copy(
            update={
                "events": tuple(
                    event for event in snapshot.events if event.sequence > after_sequence
                )
            }
        )

    def decide_tests(
        self,
        build_id: str,
        *,
        owner_session_id: str,
        decision: Literal["approved", "rejected"],
    ) -> ChatBuildSnapshot:
        with self._lock:
            record = self._authorized(build_id, owner_session_id)
            if record.state != "awaiting_test_approval":
                raise DomainError(
                    "BUILD_DECISION_NOT_AVAILABLE",
                    "La construcción no está esperando esta decisión.",
                    context={"state": record.state},
                )
            if decision == "rejected":
                record.state = "stopped"
                self._event(
                    record,
                    kind="stopped",
                    phase="approval",
                    message="Las pruebas fueron rechazadas; el proyecto se conservó sin ejecutarlas.",
                    status="stopped",
                    transient=False,
                )
                return self._snapshot(record)
            record.state = "testing"
            record.attempts = 0
            self._event(
                record,
                kind="status",
                phase="approval",
                message="Pruebas autorizadas explícitamente por la persona usuaria.",
                status="passed",
                transient=False,
            )
        self._dispatch(build_id, "test")
        return self.get(build_id, owner_session_id=owner_session_id)

    def execute_dispatched(self, build_id: str, operation: BuildOperation) -> None:
        """Execute one authenticated delivery idempotently from the durable record."""

        with self._lock:
            record = self._internal_record(build_id)
            allowed = (
                record.state == "queued"
                if operation == "construct"
                else record.state == "testing"
            )
            if not allowed:
                return
            if operation == "construct":
                # Claim the delivery before leaving the lock so duplicate Cloud Tasks
                # cannot construct the same workspace concurrently in this worker.
                record.state = "building"
                self._persist(record)
        target = self._construct if operation == "construct" else self._test
        target(build_id, external=True)

    def download(self, build_id: str, *, owner_session_id: str) -> tuple[str, bytes]:
        with self._lock:
            record = self._authorized(build_id, owner_session_id)
            if record.state != "completed" or record.output_directory is None:
                raise DomainError(
                    "BUILD_DOWNLOAD_NOT_READY",
                    "El paquete solo está disponible después de superar las pruebas.",
                )
            root = record.output_directory
            if not root.is_dir() and record.artifact_uri:
                self._project_store.restore_directory(
                    owner_session_id=record.owner_session_id,
                    uri=record.artifact_uri,
                    directory=root,
                    expected_digest=record.artifact_digest,
                )
            filename = f"{_slug(record.draft.name)}-{record.framework.framework}.zip"
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for path in sorted(item for item in root.rglob("*") if item.is_file()):
                relative = PurePosixPath(path.relative_to(root).as_posix())
                if relative.is_absolute() or ".." in relative.parts:
                    raise DomainError(
                        "BUILD_DOWNLOAD_PATH_ESCAPE", "El paquete contiene una ruta insegura."
                    )
                archive.writestr(relative.as_posix(), path.read_bytes())
        return filename, buffer.getvalue()

    def _construct(self, build_id: str, *, external: bool = False) -> None:
        try:
            with self._lock:
                record = self._records[build_id]
                record.attempts += 1
                record.state = "building"
                self._event(
                    record,
                    kind="status",
                    phase="analysis",
                    message="Analizando la especificación aprobada…",
                    status="running",
                )
            self._pause()
            with self._lock:
                record = self._records[build_id]
                self._event(
                    record,
                    kind="status",
                    phase="framework",
                    message=f"Framework confirmado: {record.framework.label} ({record.framework.language}).",
                    status="passed",
                )
                self._event(
                    record,
                    kind="status",
                    phase="workspace",
                    message="Preparando un espacio de trabajo confinado…",
                    status="running",
                )
            specification = self._specification(record)
            capabilities = tuple(
                tool.id for tool in specification.tools if tool.mode == "read_only"
            )
            if capabilities:
                with self._lock:
                    record = self._records[build_id]
                    self._event(
                        record,
                        kind="status",
                        phase="policies",
                        message=(
                            "Capacidades de solo lectura incorporadas: "
                            + ", ".join(capabilities)
                            + "."
                        ),
                        status="passed",
                    )
            destination = self._project_directory(record)
            if destination.is_dir() and (destination / "studio-build-contract.json").is_file():
                output_directory = destination
            else:
                if record.attempts > 1 and destination.exists():
                    # A prior isolated attempt may have left only a disposable scaffold.
                    # Managed projects always contain the build contract and are preserved.
                    shutil.rmtree(destination)
                bundle = self._orchestrator.construct(
                    specification,
                    destination,
                    generator=self._adapter,
                    contract=record.contract.model_dump(mode="json"),
                    progress=lambda phase, message, status: self._construction_progress(
                        build_id, phase, message, status
                    ),
                )
                output_directory = bundle.output_directory
                self._write_managed_artifacts(
                    output_directory,
                    record,
                    specification,
                )
            with self._lock:
                record.output_directory = output_directory
                self._persist(record)
            stored = self._project_store.persist_directory(
                owner_session_id=record.owner_session_id,
                project_id=record.project_id,
                build_id=record.build_id,
                directory=output_directory,
            )
            self._pause()
            with self._lock:
                record = self._records[build_id]
                record.output_directory = output_directory
                record.artifact_uri = stored.uri
                record.artifact_digest = stored.digest
                record.file_count = sum(
                    1 for path in output_directory.rglob("*") if path.is_file()
                )
                self._event(
                    record,
                    kind="artifact",
                    phase="generation",
                    message=f"Proyecto generado: {record.file_count} archivos con manifiesto y checksums.",
                    status="passed",
                    details={"file_count": record.file_count},
                )
                self._event(
                    record,
                    kind="status",
                    phase="policies",
                    message="Políticas, límites y aprobación humana incorporados.",
                    status="passed",
                )
                record.state = "awaiting_test_approval"
                test_count = 3 + int("workspace_read" in capabilities) + int(bool(record.plugins))
                self._event(
                    record,
                    kind="approval_required",
                    phase="testing",
                    message="Se necesita autorización para ejecutar verificaciones en el entorno aislado.",
                    status="waiting",
                    transient=False,
                    details={
                        "network": "blocked",
                        "credentials": "not_used",
                        "test_count": test_count,
                    },
                )
        except Exception as error:  # pragma: no cover - defensive worker boundary
            retry = self._handle_worker_error(
                build_id, error, phase="generation", operation="construct"
            )
            if retry and external:
                raise

    def _test(self, build_id: str, *, external: bool = False) -> None:
        try:
            with self._lock:
                record = self._records[build_id]
                record.attempts += 1
                root = record.output_directory
                if root is None:
                    raise DomainError(
                        "BUILD_OUTPUT_MISSING", "No existe una salida para verificar."
                    )
                if not root.is_dir() and record.artifact_uri:
                    self._project_store.restore_directory(
                        owner_session_id=record.owner_session_id,
                        uri=record.artifact_uri,
                        directory=root,
                        expected_digest=record.artifact_digest,
                    )
                self._event(
                    record,
                    kind="status",
                    phase="testing",
                    message="Ejecutando verificaciones sin red ni credenciales…",
                    status="running",
                )
            manifest = json.loads((root / "taskmaster.manifest.json").read_text(encoding="utf-8"))
            results: tuple[BuildTestResult, ...] = (
                self._verify_manifest(root, manifest),
                self._verify_no_secrets(root),
                self._verify_entrypoint(root, record.framework.framework),
            )
            if (root / "app" / "workspace.py").is_file():
                results = (*results, self._verify_workspace_read(root))
            if record.plugins:
                results = (*results, self._verify_plugin_gateway(root))
            for result in results:
                self._pause()
                with self._lock:
                    record = self._records[build_id]
                    self._event(
                        record,
                        kind="test",
                        phase="testing",
                        message=result.name,
                        status="passed" if result.passed else "failed",
                        details={"detail": result.detail},
                    )
            with self._lock:
                record = self._records[build_id]
                record.tests = results
                if all(item.passed for item in results):
                    stored = self._project_store.persist_directory(
                        owner_session_id=record.owner_session_id,
                        project_id=record.project_id,
                        build_id=record.build_id,
                        directory=root,
                    )
                    record.artifact_uri = stored.uri
                    record.artifact_digest = stored.digest
                    record.state = "completed"
                    if self._catalog is not None:
                        catalog_agent = self._catalog.register(
                            build_id=record.build_id,
                            project_id=record.project_id,
                            owner_session_id=record.owner_session_id,
                            name=record.draft.name,
                            purpose=record.draft.purpose,
                            framework=record.framework.framework,
                            framework_label=record.framework.label,
                            builder_runtime=record.builder_runtime,
                            contract_digest=record.contract.sha256,
                            plugins=record.plugins,
                            artifact_directory=root,
                            artifact_uri=stored.uri,
                            artifact_digest=stored.digest,
                            artifact_file_count=stored.file_count,
                            artifact_total_bytes=stored.total_bytes,
                        )
                        record.catalog_agent_id = catalog_agent.id
                    self._event(
                        record,
                        kind="completed",
                        phase="completed",
                        message=f"Construcción completada: {record.file_count} archivos y {len(results)}/{len(results)} verificaciones aprobadas.",
                        status="passed",
                        transient=False,
                        details={"file_count": record.file_count, "tests_passed": len(results)},
                    )
                else:
                    record.state = "failed"
                    record.error = "Una o más verificaciones no fueron superadas."
                    self._event(
                        record,
                        kind="failed",
                        phase="testing",
                        message="El laboratorio detuvo la entrega porque una verificación falló.",
                        status="failed",
                        transient=False,
                    )
        except Exception as error:  # pragma: no cover - defensive worker boundary
            retry = self._handle_worker_error(build_id, error, phase="testing", operation="test")
            if retry and external:
                raise

    def _construction_progress(
        self,
        build_id: str,
        phase: str,
        message: str,
        status: Literal["running", "passed"],
    ) -> None:
        with self._lock:
            record = self._records[build_id]
            self._event(
                record,
                kind="status",
                phase=phase,
                message=message,
                status=status,
            )

    def _specification(self, record: _BuildRecord) -> TaskmasterSpecification:
        draft = record.draft
        briefing = Briefing(
            problem=draft.purpose,
            goal=draft.purpose,
            desired_result="; ".join(draft.outputs) or draft.purpose,
            actors=[draft.intended_user] if draft.intended_user else ["Persona responsable"],
            inputs=draft.inputs or ["Solicitud confirmada"],
            tools=draft.external_actions,
            constraints=draft.constraints,
            scope_in=draft.workflow or ["Preparar y verificar el resultado"],
            scope_out=["Omitir aprobación humana", "Ejecutar acciones no declaradas"],
            approvals=[draft.approval_rule or "Aprobación humana antes de completar"],
            success_criteria=draft.success_criteria or ["Resultado revisado por una persona"],
            available_hours=1,
            input_format="Entradas confirmadas en el chat",
            outputs=draft.outputs or ["Resultado verificable"],
            external_actions="allowed" if draft.external_actions else "none",
            approval_owner=draft.intended_user or "Persona responsable",
            confirmed=True,
            confirmed_by=record.owner_session_id,
            confirmed_at=self._now(),
        )
        specification = OfficialAcademicDesigner().initial_design(
            project_id=record.project_id,
            briefing=briefing,
            now=self._now(),
        )
        if requires_workspace_read(
            draft.purpose,
            draft.inputs,
            draft.workflow,
            draft.external_actions,
        ):
            specification = _add_workspace_read(specification)
        return specification.model_copy(
            update={
                "metadata": specification.metadata.model_copy(
                    update={"name": draft.name[:100], "summary": draft.purpose[:500]}
                ),
                "generation": specification.generation.model_copy(
                    update={
                        "target_framework": record.framework.framework,
                        "language": record.framework.language,
                        "template_version": "1.0.0",
                    }
                ),
                "approval": Approval(
                    status=ApprovalStatus.APPROVED,
                    decided_by=record.owner_session_id,
                    decided_at=self._now(),
                    note="Diseño confirmado explícitamente desde el chat.",
                ),
            },
            deep=True,
        )

    def _project_directory(self, record: _BuildRecord) -> Path:
        """Return a stable, collision-safe directory directly inside projects/."""

        if record.output_directory is not None:
            return record.output_directory
        base = _slug(record.draft.name) or record.project_id
        candidate = self._root / base
        if not candidate.exists():
            return candidate
        return self._root / f"{base}-{record.project_id.removeprefix('agent_')[:8]}"

    @staticmethod
    def _write_managed_artifacts(
        root: Path,
        record: _BuildRecord,
        specification: TaskmasterSpecification,
    ) -> int:
        """Attach the approved handoff and plugin plan to the checksummed bundle."""

        artifacts = {
            "studio-build-contract.json": record.contract.model_dump_json(indent=2) + "\n",
            "taskmaster.specification.json": specification.model_dump_json(
                indent=2,
                by_alias=True,
            )
            + "\n",
            "plugins.json": json.dumps(
                {
                    "schema_version": "1.0.0",
                    "plugins": [item.model_dump(mode="json") for item in record.plugins],
                    "policy": (
                        "Plugins marked connection_required remain disabled until OAuth and "
                        "human approval are configured."
                    ),
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            "studio_plugin_gateway.py": _PLUGIN_GATEWAY_SOURCE,
        }
        manifest_path = root / "taskmaster.manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        files = manifest.get("files")
        if not isinstance(files, list):
            raise DomainError(
                "BUILD_MANIFEST_INVALID", "El manifiesto generado no contiene archivos."
            )
        for relative_path, content in artifacts.items():
            path = root / relative_path
            path.write_text(content, encoding="utf-8", newline="\n")
            payload = path.read_bytes()
            files.append(
                {
                    "relative_path": relative_path,
                    "sha256": hashlib.sha256(payload).hexdigest(),
                    "size_bytes": len(payload),
                }
            )
        manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
            newline="\n",
        )
        return len(artifacts)

    @staticmethod
    def _verify_manifest(root: Path, manifest: dict[str, object]) -> BuildTestResult:
        raw_files = manifest.get("files", [])
        valid = False
        if isinstance(raw_files, list) and raw_files:
            valid = True
            for raw in raw_files:
                if not isinstance(raw, dict):
                    valid = False
                    break
                relative = PurePosixPath(str(raw.get("relative_path", "")))
                path = root.joinpath(*relative.parts)
                if relative.is_absolute() or ".." in relative.parts or not path.is_file():
                    valid = False
                    break
                digest = hashlib.sha256(path.read_bytes()).hexdigest()
                if digest != raw.get("sha256"):
                    valid = False
                    break
        return BuildTestResult(
            name="Integridad del manifiesto y archivos",
            passed=valid,
            detail="Todos los archivos declarados conservan su checksum."
            if valid
            else "El manifiesto o un checksum no coincide.",
        )

    @staticmethod
    def _verify_no_secrets(root: Path) -> BuildTestResult:
        forbidden = re.compile(
            r"(-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----|AIza[0-9A-Za-z_-]{20,}|sk-[0-9A-Za-z]{20,})"
        )
        leaked = False
        for path in root.rglob("*"):
            if not path.is_file() or path.name == ".env.example":
                continue
            try:
                leaked = bool(forbidden.search(path.read_text(encoding="utf-8")))
            except UnicodeDecodeError:
                continue
            if leaked:
                break
        return BuildTestResult(
            name="Ausencia de credenciales incorporadas",
            passed=not leaked,
            detail="No se encontraron claves privadas ni tokens reales."
            if not leaked
            else "Se detectó un patrón de credencial.",
        )

    @staticmethod
    def _verify_entrypoint(root: Path, framework: str) -> BuildTestResult:
        expected = {
            "google_adk": root / "app" / "agent.py",
            "genai_sdk": root / "app.py",
            "antigravity": root / "agent.py",
            "genkit": root / "src" / "index.ts",
        }[framework]
        valid = expected.is_file()
        return BuildTestResult(
            name="Punto de entrada del framework",
            passed=valid,
            detail=f"Se verificó {expected.relative_to(root).as_posix()}."
            if valid
            else "Falta el punto de entrada esperado.",
        )

    @staticmethod
    def _verify_workspace_read(root: Path) -> BuildTestResult:
        """Exercise the standalone reader without loading the generated agent or using network."""

        import importlib.util
        import tempfile

        module_path = root / "app" / "workspace.py"
        try:
            spec = importlib.util.spec_from_file_location("generated_workspace_reader", module_path)
            if spec is None or spec.loader is None:
                raise RuntimeError("No fue posible cargar el lector generado.")
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            with tempfile.TemporaryDirectory() as temporary:
                workspace = Path(temporary)
                (workspace / "source.md").write_text("contenido verificable", encoding="utf-8")
                reader = module.WorkspaceReader(workspace)
                result = reader.read_text("source.md")
                valid = result["content"] == "contenido verificable"
                try:
                    reader.read_text("../outside.txt")
                except PermissionError:
                    traversal_blocked = True
                else:
                    traversal_blocked = False
            passed = valid and traversal_blocked
            detail = "Lectura confinada y escape de ruta bloqueado."
        except Exception as error:  # pragma: no cover - defensive verification boundary
            passed = False
            detail = f"El lector no superó su contrato: {type(error).__name__}."
        return BuildTestResult(
            name="Lectura confinada del workspace",
            passed=passed,
            detail=detail,
        )

    @staticmethod
    def _verify_plugin_gateway(root: Path) -> BuildTestResult:
        gateway = root / "studio_plugin_gateway.py"
        plugins = root / "plugins.json"
        try:
            compile(gateway.read_text(encoding="utf-8"), str(gateway), "exec")
            payload = json.loads(plugins.read_text(encoding="utf-8"))
            declared = payload.get("plugins", [])
            valid = gateway.is_file() and isinstance(declared, list) and bool(declared)
        except (OSError, SyntaxError, ValueError):
            valid = False
        return BuildTestResult(
            name="Gateway de plugins cerrado por defecto",
            passed=valid,
            detail=(
                "El paquete declara plugins y exige conexión y aprobación antes de escribir."
                if valid
                else "El contrato de plugins o su gateway no es válido."
            ),
        )

    def _authorized(self, build_id: str, owner_session_id: str) -> _BuildRecord:
        record = self._records.get(build_id)
        if record is None and self._build_queue is not None:
            payload = self._build_queue.load(build_id, owner_session_id)
            if payload is not None:
                record = self._record_from_payload(payload)
                self._records[build_id] = record
        if record is None or record.owner_session_id != owner_session_id:
            raise DomainError(
                "BUILD_NOT_FOUND", "No existe una construcción accesible con ese identificador."
            )
        return record

    def _snapshot(self, record: _BuildRecord) -> ChatBuildSnapshot:
        return ChatBuildSnapshot(
            build_id=record.build_id,
            project_id=record.project_id,
            state=record.state,
            builder=(
                "Antigravity · Ingeniero de agentes"
                if record.builder_runtime == "antigravity_sdk"
                else (
                    "Trabajador aislado · Constructor Google ADK"
                    if record.builder_runtime == "isolated_controlled_builder"
                    else "Constructor local seguro · respaldo de Antigravity"
                )
            ),
            builder_runtime=record.builder_runtime,
            framework=record.framework,
            agent_name=record.draft.name,
            capabilities=(
                ("workspace.read",)
                if requires_workspace_read(
                    record.draft.purpose,
                    record.draft.inputs,
                    record.draft.workflow,
                    record.draft.external_actions,
                )
                else ()
            ),
            plugins=record.plugins,
            contract_digest=record.contract.sha256,
            events=tuple(record.events),
            file_count=record.file_count,
            tests=record.tests,
            download_ready=record.state == "completed",
            catalog_agent_id=record.catalog_agent_id,
            project_directory=(
                str(record.output_directory) if record.output_directory is not None else ""
            ),
            error=record.error,
            queue_attempt=record.attempts,
            queue_max_attempts=record.max_attempts,
            durable_queue=bool(self._build_queue and self._build_queue.durable),
        )

    def _event(
        self,
        record: _BuildRecord,
        *,
        kind: BuildEventKind,
        phase: str,
        message: str,
        status: BuildEventStatus,
        transient: bool = True,
        details: dict[str, object] | None = None,
    ) -> None:
        record.events.append(
            BuildEvent(
                sequence=len(record.events) + 1,
                kind=kind,
                phase=phase,
                message=message,
                status=status,
                transient=transient,
                occurred_at=self._now(),
                details=details or {},
            )
        )
        self._persist(record)

    def _handle_worker_error(
        self,
        build_id: str,
        error: Exception,
        *,
        phase: str,
        operation: BuildOperation,
    ) -> bool:
        with self._lock:
            record = self._records[build_id]
            retry = not isinstance(error, DomainError) and record.attempts < record.max_attempts
            if retry:
                record.state = "queued" if operation == "construct" else "testing"
                self._event(
                    record,
                    kind="status",
                    phase="queue",
                    message="El trabajador se reiniciará una vez tras un fallo transitorio.",
                    status="waiting",
                    transient=False,
                    details={
                        "attempt": record.attempts,
                        "max_attempts": record.max_attempts,
                        "error_type": type(error).__name__,
                    },
                )
            else:
                record.state = "failed"
                record.error = "El trabajador aislado se detuvo de forma segura."
                self._event(
                    record,
                    kind="failed",
                    phase=phase,
                    message="El trabajador aislado se detuvo de forma segura.",
                    status="failed",
                    transient=False,
                    details={
                        "attempt": record.attempts,
                        "error_type": type(error).__name__,
                    },
                )
        if retry and self._dispatcher is None:
            self._dispatch(build_id, operation)
        return retry

    def _dispatch(self, build_id: str, operation: BuildOperation) -> None:
        with self._lock:
            record = self._records[build_id]
            attempt = record.attempts
        if self._dispatcher is not None:
            try:
                task_name = self._dispatcher.dispatch(build_id, operation, attempt)
            except Exception as error:
                with self._lock:
                    record = self._records[build_id]
                    self._event(
                        record,
                        kind="status",
                        phase="queue",
                        message="La construcción quedó guardada, pero no pudo entregarse al worker.",
                        status="waiting",
                        transient=False,
                        details={"error_type": type(error).__name__},
                    )
                raise DomainError(
                    "BUILD_DISPATCH_FAILED",
                    "La construcción quedó guardada, pero Cloud Tasks no pudo recibirla.",
                ) from error
            with self._lock:
                record = self._records[build_id]
                self._event(
                    record,
                    kind="status",
                    phase="queue",
                    message="Trabajo entregado a la cola durable de construcción.",
                    status="waiting",
                    transient=False,
                    details={"task": task_name.rsplit("/", 1)[-1], "operation": operation},
                )
            return
        if self._external_dispatch_required:
            raise DomainError(
                "BUILD_DISPATCH_UNAVAILABLE",
                "La cola externa requerida no está disponible; no se ejecutará dentro del servidor web.",
            )
        target = self._construct if operation == "construct" else self._test
        self._executor.submit(target, build_id)

    def _persist(self, record: _BuildRecord) -> None:
        if self._build_queue is not None:
            self._build_queue.save(record.build_id, self._record_payload(record))

    def _resume_pending(self) -> None:
        if self._build_queue is None:
            return
        try:
            pending = self._build_queue.list_pending()
        except Exception:
            return
        for payload in pending:
            try:
                record = self._record_from_payload(payload)
            except (KeyError, TypeError, ValueError):
                continue
            self._records[record.build_id] = record
            if record.state in {"queued", "building"}:
                record.state = "queued"
                self._event(
                    record,
                    kind="status",
                    phase="queue",
                    message="Construcción recuperada después de reiniciar el servicio.",
                    status="waiting",
                    transient=False,
                )
                try:
                    self._dispatch(record.build_id, "construct")
                except DomainError:
                    continue
            elif record.state == "testing":
                self._event(
                    record,
                    kind="status",
                    phase="queue",
                    message="Verificación recuperada después de reiniciar el servicio.",
                    status="waiting",
                    transient=False,
                )
                try:
                    self._dispatch(record.build_id, "test")
                except DomainError:
                    continue

    def _internal_record(self, build_id: str) -> _BuildRecord:
        record = None
        if self._build_queue is not None:
            payload = self._build_queue.load_internal(build_id)
            if payload is not None:
                record = self._record_from_payload(payload)
                self._records[build_id] = record
        if record is None:
            record = self._records.get(build_id)
        if record is None:
            raise DomainError("BUILD_NOT_FOUND", "La construcción solicitada no existe.")
        return record

    def _record_payload(self, record: _BuildRecord) -> dict[str, object]:
        return {
            "schema_version": "1.0.0",
            "build_id": record.build_id,
            "project_id": record.project_id,
            "owner_session_id": record.owner_session_id,
            "draft": record.draft.model_dump(mode="json"),
            "framework": record.framework.model_dump(mode="json"),
            "contract": record.contract.model_dump(mode="json"),
            "plugins": [item.model_dump(mode="json") for item in record.plugins],
            "builder_runtime": record.builder_runtime,
            "state": record.state,
            "events": [item.model_dump(mode="json") for item in record.events],
            "file_count": record.file_count,
            "tests": [item.model_dump(mode="json") for item in record.tests],
            "output_directory": str(record.output_directory or ""),
            "artifact_uri": record.artifact_uri,
            "artifact_digest": record.artifact_digest,
            "error": record.error,
            "catalog_agent_id": record.catalog_agent_id,
            "attempts": record.attempts,
            "max_attempts": record.max_attempts,
            "updated_at": self._now().isoformat(),
        }

    @staticmethod
    def _record_from_payload(payload: dict[str, object]) -> _BuildRecord:
        record = _BuildRecord(
            build_id=str(payload["build_id"]),
            project_id=str(payload["project_id"]),
            owner_session_id=str(payload["owner_session_id"]),
            draft=AgentDraft.model_validate(payload["draft"]),
            framework=FrameworkRecommendation.model_validate(payload["framework"]),
            contract=AgentBuildContract.model_validate(payload["contract"]),
            plugins=tuple(
                PluginSelection.model_validate(item)
                for item in cast(list[object], payload.get("plugins", []))
            ),
            builder_runtime=cast(BuilderRuntime, payload["builder_runtime"]),
        )
        record.state = cast(BuildState, payload.get("state", "queued"))
        record.events = [
            BuildEvent.model_validate(item)
            for item in cast(list[object], payload.get("events", []))
        ]
        record.file_count = int(payload.get("file_count", 0))
        record.tests = tuple(
            BuildTestResult.model_validate(item)
            for item in cast(list[object], payload.get("tests", []))
        )
        output_directory = str(payload.get("output_directory", ""))
        record.output_directory = Path(output_directory) if output_directory else None
        record.artifact_uri = str(payload.get("artifact_uri", ""))
        record.artifact_digest = str(payload.get("artifact_digest", ""))
        record.error = str(payload.get("error", ""))
        record.catalog_agent_id = str(payload.get("catalog_agent_id", ""))
        record.attempts = int(payload.get("attempts", 0))
        record.max_attempts = int(payload.get("max_attempts", 2))
        return record

    def _pause(self) -> None:
        if self._delay:
            time.sleep(self._delay)


def _slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.casefold()).strip("-") or "taskmaster"


_PLUGIN_GATEWAY_SOURCE = '''"""Generated fail-closed plugin gateway. Configure handlers at runtime."""
from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any


class PluginGateway:
    def __init__(self, handlers: dict[tuple[str, str], Callable[[dict[str, Any]], dict[str, Any]]]):
        payload = json.loads(Path(__file__).with_name("plugins.json").read_text(encoding="utf-8"))
        self._plugins = {item["plugin_id"]: item for item in payload.get("plugins", [])}
        self._handlers = handlers

    def invoke(self, plugin_id: str, operation: str, arguments: dict[str, Any], *, approved: bool = False) -> dict[str, Any]:
        plugin = self._plugins.get(plugin_id)
        if plugin is None or operation not in plugin.get("operations", []):
            raise PermissionError("Plugin u operación no declarados.")
        if plugin.get("availability") != "available":
            raise PermissionError("El plugin requiere una conexión explícita.")
        handler = self._handlers.get((plugin_id, operation))
        if handler is None:
            raise PermissionError("No existe un adaptador habilitado para esta operación.")
        if operation in {"send", "create", "update", "delete", "publish"} and not approved:
            raise PermissionError("La operación necesita aprobación humana.")
        return handler(arguments)
'''


def _build_contract(
    *,
    project_id: str,
    draft: AgentDraft,
    framework: FrameworkRecommendation,
    plugins: tuple[PluginSelection, ...],
    confirmed_by: str,
    confirmed_at: datetime,
) -> AgentBuildContract:
    unsigned = {
        "schema_version": "1.0.0",
        "project_id": project_id,
        "agent": draft.model_dump(mode="json"),
        "framework": framework.model_dump(mode="json"),
        "plugins": [item.model_dump(mode="json") for item in plugins],
        "confirmed_by": confirmed_by,
        "confirmed_at": confirmed_at.isoformat(),
    }
    digest = hashlib.sha256(
        json.dumps(unsigned, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return AgentBuildContract(
        project_id=project_id,
        agent=draft,
        framework=framework,
        plugins=plugins,
        confirmed_by=confirmed_by,
        confirmed_at=confirmed_at,
        sha256=digest,
    )


def _add_workspace_read(specification: TaskmasterSpecification) -> TaskmasterSpecification:
    """Attach the least-privilege workspace reader to an explicitly approved design."""

    if any(tool.id == "workspace_read" for tool in specification.tools):
        return specification
    tool = Tool.model_validate(
        {
            "id": "workspace_read",
            "name": "Leer archivo del workspace",
            "description": (
                "Lee un archivo de texto dentro del directorio asignado al agente; "
                "bloquea rutas externas, enlaces simbólicos y archivos sensibles."
            ),
            "mode": "read_only",
            "risk": "low",
            "input_schema": {
                "type": "object",
                "required": ["relative_path"],
                "properties": {"relative_path": {"type": "string", "maxLength": 500}},
            },
            "output_schema": {
                "type": "object",
                "required": ["path", "content", "size_bytes", "sha256"],
                "properties": {
                    "path": {"type": "string"},
                    "content": {"type": "string"},
                    "size_bytes": {"type": "integer"},
                    "sha256": {"type": "string"},
                },
            },
            "side_effects": [],
            "required_secret_refs": [],
        }
    )
    policy = Policy.model_validate(
        {
            "id": "confine_workspace_read",
            "name": "Lectura confinada al workspace",
            "type": "data",
            "rule": (
                "workspace_read solo puede leer texto permitido dentro de "
                "TASKMASTER_WORKSPACE_ROOT."
            ),
            "effect": "Bloquear cualquier ruta, enlace o archivo fuera del alcance permitido.",
        }
    )
    return specification.model_copy(
        update={
            "tools": [*specification.tools, tool],
            "policies": [*specification.policies, policy],
            "mission": specification.mission.model_copy(
                update={
                    "scope_in": [
                        *specification.mission.scope_in,
                        "Leer archivos de texto dentro del workspace asignado",
                    ],
                    "scope_out": [
                        *specification.mission.scope_out,
                        "Leer rutas externas, secretos o enlaces simbólicos",
                    ],
                }
            ),
        },
        deep=True,
    )
