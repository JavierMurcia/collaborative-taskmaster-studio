"""FastAPI entry point for local development and Cloud Run."""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path
from time import perf_counter
from typing import Any, cast
from uuid import uuid4

import uvicorn
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint

from adapters.antigravity import AntigravitySdkOrchestrator
from adapters.controlled import IsolatedControlledConstructionOrchestrator
from adapters.frameworks import (
    AntigravityGenerator,
    FrameworkGeneratorRegistry,
    GenAiSdkGenerator,
    GenkitGenerator,
)
from adapters.google_adk import GoogleAdkGenerator
from app.api.router import ServiceContainer, create_router
from app.server import resolve_server_binding
from infrastructure.cloud_run import (
    load_build_definition,
    load_deployment_definition,
    load_iam_definition,
    load_identity_definition,
    load_runtime_configuration,
)
from infrastructure.cloud_tasks import CloudTasksSettings, initialize_cloud_tasks
from infrastructure.firestore import (
    FirestoreAgentCatalog,
    FirestoreBuildQueueStore,
    FirestoreConversationMemoryRepository,
    FirestoreProjectRepository,
    FirestoreSettings,
    initialize_firestore,
)
from infrastructure.firestore.indexes import verify_index_manifest
from infrastructure.firestore.retention import (
    DemoRetentionPolicy,
    verify_retention_manifest,
)
from infrastructure.local.build_queue import JsonBuildQueueStore
from infrastructure.local.clock import SystemClock
from infrastructure.local.conversation_memory import (
    InMemoryConversationMemoryRepository,
    JsonConversationMemoryRepository,
)
from infrastructure.local.project_storage import LocalProjectArtifactStore
from infrastructure.local.repositories import JsonLocalRepository
from infrastructure.storage import (
    CloudProjectArtifactStore,
    CloudStorageSettings,
    initialize_cloud_storage,
)
from infrastructure.vertex import VertexModelGateway, VertexSettings, inspect_vertex_readiness
from sandbox import SandboxEvaluator
from studio.application.agent_catalog import AgentCatalog, AgentCatalogRepository
from studio.application.agent_runtime_service import AgentRuntimeService
from studio.application.briefing_generator import StructuredBriefingGenerator
from studio.application.builder_readiness import inspect_builder_readiness
from studio.application.catalog_agent_execution_service import CatalogAgentExecutionService
from studio.application.chat_build_service import ChatBuildService
from studio.application.collaborative_chat_service import CollaborativeChatService
from studio.application.connection_service import ConnectionService
from studio.application.conversation_memory import (
    ConversationMemoryRepository,
    ConversationMemoryService,
)
from studio.application.demo_reset import DemoResetService
from studio.application.evaluation_service import EvaluationService
from studio.application.export_service import AgentExportService
from studio.application.generation_service import GenerationService
from studio.application.interview_question_generator import (
    StructuredInterviewQuestionGenerator,
)
from studio.application.plugin_registry import PluginRegistry
from studio.application.revision_generator import StructuredRevisionGenerator
from studio.application.specification_generator import StructuredSpecificationGenerator
from studio.application.translation_service import TranslationService
from studio.capabilities.documents import DocumentLibrary, LargeUploadManager
from studio.capabilities.github import GitHubReader
from studio.capabilities.google_calendar import GoogleCalendarReader
from studio.capabilities.google_drive import GoogleDriveReader
from studio.capabilities.google_gmail import GoogleGmailReader
from studio.capabilities.web import VertexWebResearcher
from studio.capabilities.workspace import WorkspaceReader
from studio.domain.errors import DomainError
from studio.ports.clock import Clock
from studio.ports.construction import BuilderRuntime
from studio.ports.repositories import EventRepository, ProjectRepository
from studio.security import IdentityVerifier, WorkerIdentitySettings, WorkerTokenVerifier
from studio.security.credential_vault import build_credential_vault

ROOT = Path(__file__).resolve().parent
STATIC = ROOT / "static"
LOGGER = logging.getLogger("collaborative-taskmaster-studio")
MAX_REQUEST_BYTES = 32_768
MAX_DOCUMENT_REQUEST_BYTES = 26_500_000
MAX_UPLOAD_CHUNK_REQUEST_BYTES = 8_500_000
def _workspace_read_limit() -> int:
    try:
        return int(os.getenv("STUDIO_COLLABORATOR_MAX_READ_BYTES", "16384"))
    except ValueError:
        return 16_384


class RequestContextMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        request_id = request.headers.get("X-Request-ID") or uuid4().hex
        request.state.request_id = request_id[:128]
        content_length = request.headers.get("content-length")
        if request.url.path == "/api/v1/collaborative/documents":
            request_limit = MAX_DOCUMENT_REQUEST_BYTES
        elif (
            request.method == "PUT"
            and request.url.path.startswith("/api/v1/collaborative/document-uploads/")
        ):
            request_limit = MAX_UPLOAD_CHUNK_REQUEST_BYTES
        else:
            request_limit = MAX_REQUEST_BYTES
        if content_length and content_length.isdigit() and int(content_length) > request_limit:
            return _error_response(
                request,
                413,
                "REQUEST_TOO_LARGE",
                "La solicitud supera el límite permitido.",
            )
        started = perf_counter()
        response = await call_next(request)
        response.headers["X-Request-ID"] = request.state.request_id
        response.headers["X-Response-Time-Ms"] = f"{(perf_counter() - started) * 1000:.2f}"
        return response


class IdentityMiddleware(BaseHTTPMiddleware):
    """Replace every client owner hint with a verified, server-derived identity key."""

    def __init__(self, app: Any, verifier: IdentityVerifier) -> None:
        super().__init__(app)
        self._verifier = verifier

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        path = request.url.path
        public_paths = {
            "/api/v1/meta",
            "/api/v1/collaborative/auth/google/start",
            "/api/v1/collaborative/auth/refresh",
            "/api/v1/collaborative/auth/logout",
            "/api/v1/collaborative/connections/oauth/callback",
            "/api/v1/internal/build-worker",
        }
        protected = path.startswith("/api/v1/") and path not in public_paths
        if not protected:
            return await call_next(request)
        try:
            identity = self._verifier.verify(
                request.headers.get("Authorization"),
                request.headers.get("X-Studio-Session"),
            )
        except DomainError as error:
            request.state.request_id = getattr(request.state, "request_id", uuid4().hex)
            status = 401 if error.code.startswith("AUTHENTICATION") else 400
            return _error_response(request, status, error.code, error.message)
        request.state.identity = identity
        headers = [
            (name, value)
            for name, value in request.scope.get("headers", [])
            if name.lower() != b"x-studio-session"
        ]
        headers.append((b"x-studio-session", identity.isolation_key.encode("ascii")))
        request.scope["headers"] = headers
        response = await call_next(request)
        response.headers["X-Studio-Identity-Mode"] = identity.mode
        return response


def create_app(
    repository: ProjectRepository | None = None,
    events: EventRepository | None = None,
    clock: Clock | None = None,
    generated_root: Path | None = None,
    vertex_settings: VertexSettings | None = None,
    firestore_settings: FirestoreSettings | None = None,
    cloud_storage_settings: CloudStorageSettings | None = None,
    conversation_memory: ConversationMemoryRepository | None = None,
    document_library: DocumentLibrary | None = None,
    large_upload_manager: LargeUploadManager | None = None,
    identity_verifier: IdentityVerifier | None = None,
) -> FastAPI:
    app = FastAPI(
        title="Collaborative Taskmaster Studio",
        description="Design, review, and generate auditable Taskmaster agents.",
        version="0.1.0",
    )
    app.add_middleware(RequestContextMiddleware)
    active_identity_verifier = identity_verifier or IdentityVerifier()
    app.add_middleware(IdentityMiddleware, verifier=active_identity_verifier)
    app.mount("/static", StaticFiles(directory=STATIC), name="static")
    runtime_identity = load_identity_definition()
    runtime_iam = load_iam_definition()
    build_pipeline = load_build_definition()
    runtime_configuration = load_runtime_configuration()
    cloud_run_deployment = load_deployment_definition()
    active_firestore_settings = firestore_settings or FirestoreSettings.from_environment()
    firestore_runtime = initialize_firestore(active_firestore_settings)
    active_storage_settings = cloud_storage_settings or CloudStorageSettings.from_environment()
    storage_runtime = initialize_cloud_storage(active_storage_settings)
    cloud_tasks_settings = CloudTasksSettings.from_environment()
    cloud_tasks_runtime = initialize_cloud_tasks(cloud_tasks_settings)
    firestore_indexes = verify_index_manifest()
    retention_policy = DemoRetentionPolicy(active_firestore_settings.demo_retention_days)
    firestore_retention = verify_retention_manifest(retention_policy)
    app.state.firestore_settings = active_firestore_settings
    app.state.firestore_runtime = firestore_runtime
    app.state.cloud_storage_runtime = storage_runtime
    active_vertex_settings = vertex_settings or VertexSettings.from_environment()
    vertex_readiness = inspect_vertex_readiness(active_vertex_settings)
    model_gateway = (
        VertexModelGateway(active_vertex_settings, vertex_readiness)
        if vertex_readiness.status == "ready" and active_vertex_settings.enabled
        else None
    )
    question_generator = (
        StructuredInterviewQuestionGenerator(model_gateway)
        if model_gateway is not None and active_vertex_settings.model_questions_enabled
        else None
    )
    briefing_generator = (
        StructuredBriefingGenerator(model_gateway)
        if model_gateway is not None and active_vertex_settings.model_briefing_enabled
        else None
    )
    specification_generator = (
        StructuredSpecificationGenerator(model_gateway)
        if model_gateway is not None and active_vertex_settings.model_specification_enabled
        else None
    )
    revision_generator = (
        StructuredRevisionGenerator(model_gateway)
        if model_gateway is not None and active_vertex_settings.model_revision_enabled
        else None
    )
    cloud_calls_enabled = model_gateway is not None
    vertex_readiness = vertex_readiness.model_copy(
        update={"cloud_calls_enabled": cloud_calls_enabled}
    )
    app.state.vertex_settings = active_vertex_settings
    app.state.vertex_readiness = vertex_readiness
    active_clock = clock or SystemClock()
    data_directory = Path(os.getenv("STUDIO_DATA_DIRECTORY", ".studio-data"))
    if repository is None:
        persistence_ready = (
            (not active_firestore_settings.enabled or firestore_runtime.readiness.status == "ready")
            and (not active_storage_settings.enabled or storage_runtime.readiness.status == "ready")
        )
        if firestore_runtime.client is not None and persistence_ready:
            repository = FirestoreProjectRepository(
                cast(Any, firestore_runtime.client),
                active_clock,
                transaction_max_attempts=(active_firestore_settings.transaction_max_attempts),
                retention_policy=retention_policy,
            )
            firestore_runtime = firestore_runtime.__class__(
                settings=firestore_runtime.settings,
                readiness=firestore_runtime.readiness.model_copy(
                    update={"repository_active": True}
                ),
                client=firestore_runtime.client,
            )
            app.state.firestore_runtime = firestore_runtime
        else:
            repository = JsonLocalRepository(data_directory, active_clock)
    else:
        persistence_ready = True
    event_repository = events if events is not None else cast(EventRepository, repository)
    if conversation_memory is None:
        if firestore_runtime.client is not None and persistence_ready:
            conversation_memory = FirestoreConversationMemoryRepository(
                cast(Any, firestore_runtime.client)
            )
        elif repository is not None and not isinstance(repository, JsonLocalRepository):
            conversation_memory = InMemoryConversationMemoryRepository()
        else:
            conversation_memory = JsonConversationMemoryRepository(data_directory)
    if document_library is None:
        document_library = DocumentLibrary(data_directory)
    if large_upload_manager is None:
        large_upload_root = Path(
            os.getenv("STUDIO_LARGE_UPLOAD_ROOT", str(data_directory / "large-uploads"))
        )
        large_upload_manager = LargeUploadManager(large_upload_root, document_library)
    plugin_registry = PluginRegistry()
    credential_vault = build_credential_vault(
        cast(Any, firestore_runtime.client) if firestore_runtime.client is not None else None
    )
    connection_service = ConnectionService(
        data_directory,
        plugin_registry,
        vault=credential_vault,
    )
    google_drive = GoogleDriveReader(connection_service)
    google_gmail = GoogleGmailReader(connection_service)
    google_calendar = GoogleCalendarReader(connection_service)
    github = GitHubReader(connection_service)
    agent_catalog: AgentCatalogRepository = (
        FirestoreAgentCatalog(cast(Any, firestore_runtime.client))
        if firestore_runtime.client is not None and persistence_ready
        else AgentCatalog(data_directory)
    )
    builder_readiness = inspect_builder_readiness()
    construction_orchestrator = (
        AntigravitySdkOrchestrator(os.environ["STUDIO_ANTIGRAVITY_PYTHON"])
        if builder_readiness.active_builder == "antigravity"
        else IsolatedControlledConstructionOrchestrator(sys.executable)
    )
    configured_builder_runtime = os.getenv("STUDIO_EXTERNAL_BUILDER_RUNTIME", "").strip()
    external_builder_runtime: BuilderRuntime | None = (
        cast(BuilderRuntime, configured_builder_runtime)
        if configured_builder_runtime
        in {"antigravity_sdk", "controlled_local_builder", "isolated_controlled_builder"}
        else None
    )
    output_root = generated_root or Path(os.getenv("STUDIO_GENERATED_ROOT", "generated"))
    projects_root = Path(os.getenv("STUDIO_PROJECTS_ROOT", "projects"))
    if not projects_root.is_absolute():
        projects_root = (Path.cwd() / projects_root).resolve()
    projects_root.mkdir(parents=True, exist_ok=True)
    project_store = (
        CloudProjectArtifactStore(cast(Any, storage_runtime.client), active_storage_settings)
        if storage_runtime.client is not None and persistence_ready
        else LocalProjectArtifactStore()
    )
    build_queue = (
        FirestoreBuildQueueStore(cast(Any, firestore_runtime.client))
        if firestore_runtime.client is not None and persistence_ready
        else JsonBuildQueueStore(data_directory)
    )
    worker_tokens = (
        WorkerTokenVerifier(
            WorkerIdentitySettings(
                enabled=True,
                audience=cloud_tasks_settings.audience,
                service_account_email=cloud_tasks_settings.worker_service_account,
                queue_name=cloud_tasks_settings.queue,
            )
        )
        if cloud_tasks_runtime.dispatcher is not None
        else None
    )
    framework_generators = FrameworkGeneratorRegistry(
        (
            GoogleAdkGenerator(output_root),
            GenAiSdkGenerator(output_root),
            AntigravityGenerator(output_root),
            GenkitGenerator(output_root),
        )
    )
    project_framework_generators = FrameworkGeneratorRegistry(
        (
            GoogleAdkGenerator(projects_root),
            GenAiSdkGenerator(projects_root),
            AntigravityGenerator(projects_root),
            GenkitGenerator(projects_root),
        )
    )
    generation = GenerationService(
        repository,
        event_repository,
        active_clock,
        framework_generators,
        output_root,
    )
    evaluation = EvaluationService(
        repository,
        event_repository,
        active_clock,
        SandboxEvaluator(timeout_seconds=float(os.getenv("STUDIO_SANDBOX_TIMEOUT", "8"))),
        output_root,
    )
    export = AgentExportService(repository, framework_generators, output_root)
    demo_reset = DemoResetService(repository, active_clock, output_root)
    agent_runtime = AgentRuntimeService(
        repository,
        event_repository,
        active_clock,
        model_gateway,
        active_vertex_settings.model,
        active_vertex_settings.max_model_output_tokens,
        google_drive,
    )
    services = ServiceContainer.build(
        repository,
        event_repository,
        active_clock,
        generation,
        evaluation,
        export,
        demo_reset,
        question_generator,
        briefing_generator,
        specification_generator,
        revision_generator,
        active_vertex_settings.max_model_questions_per_project,
        agent_runtime=agent_runtime,
        collaborative_chat=CollaborativeChatService(
            model_gateway,
            active_vertex_settings.model,
            active_vertex_settings.max_model_output_tokens,
            workspace_reader=WorkspaceReader(
                os.getenv("STUDIO_COLLABORATOR_WORKSPACE_ROOT", str(ROOT.parent)),
                max_bytes=_workspace_read_limit(),
            ),
            web_researcher=(
                VertexWebResearcher(
                    model_gateway.tool_client(),
                    active_vertex_settings.model,
                )
                if model_gateway is not None
                else None
            ),
            document_library=document_library,
            conversation_memory=ConversationMemoryService(conversation_memory, active_clock),
            google_drive=google_drive,
            google_gmail=google_gmail,
            google_calendar=google_calendar,
            github=github,
            connections=connection_service,
        ),
        conversation_memory=ConversationMemoryService(conversation_memory, active_clock),
        documents=document_library,
        large_uploads=large_upload_manager,
        chat_builder=ChatBuildService(
            project_framework_generators,
            projects_root,
            orchestrator=construction_orchestrator,
            plugin_registry=plugin_registry,
            agent_catalog=agent_catalog,
            project_store=project_store,
            build_queue=build_queue,
            dispatcher=cloud_tasks_runtime.dispatcher,
            external_dispatch_required=cloud_tasks_settings.enabled,
            builder_runtime=(
                external_builder_runtime
                if cloud_tasks_runtime.dispatcher is not None
                else construction_orchestrator.runtime_id
            ),
        ),
        agent_catalog=agent_catalog,
        catalog_agent_runtime=CatalogAgentExecutionService(
            agent_catalog,
            agent_runtime,
            projects_root,
            project_store,
        ),
        plugin_registry=plugin_registry,
        connections=connection_service,
        google_drive=google_drive,
        google_gmail=google_gmail,
        google_calendar=google_calendar,
        github=github,
        translation=TranslationService(model_gateway, active_vertex_settings.model),
        builder_readiness=builder_readiness,
        worker_tokens=worker_tokens,
    )
    app.state.services = services
    app.state.startup_complete = True
    app.state.persistence_ready = persistence_ready
    app.state.orchestration_ready = cloud_tasks_runtime.ready
    app.include_router(create_router(services))

    @app.exception_handler(DomainError)
    async def domain_error(request: Request, error: DomainError) -> JSONResponse:
        status = _domain_status(error.code)
        return _error_response(
            request,
            status,
            error.code,
            error.message,
            context=error.context,
        )

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, error: RequestValidationError) -> JSONResponse:
        return _error_response(
            request,
            422,
            "REQUEST_VALIDATION_FAILED",
            "La solicitud contiene campos inválidos.",
            context={"issues": error.errors()},
        )

    @app.exception_handler(Exception)
    async def unexpected_error(request: Request, error: Exception) -> JSONResponse:
        LOGGER.exception(
            "Unhandled request error request_id=%s path=%s",
            getattr(request.state, "request_id", "unknown"),
            request.url.path,
            exc_info=error,
        )
        return _error_response(
            request,
            500,
            "INTERNAL_ERROR",
            "El servidor no pudo completar la solicitud. Inténtalo nuevamente.",
        )

    @app.get("/health", tags=["system"])
    async def health() -> dict[str, str]:
        return {"status": "ok", "service": "collaborative-taskmaster-studio"}

    @app.get("/health/live", tags=["system"])
    async def liveness() -> dict[str, str]:
        return {"status": "alive", "service": "collaborative-taskmaster-studio"}

    @app.get("/health/startup", tags=["system"])
    async def startup() -> JSONResponse:
        ready = bool(app.state.startup_complete)
        return JSONResponse(
            status_code=200 if ready else 503,
            content={
                "status": "started" if ready else "not_started",
                "service": "collaborative-taskmaster-studio",
                "checks": {"application": "ready" if ready else "not_ready"},
            },
        )

    @app.get("/health/ready", tags=["system"])
    async def readiness() -> JSONResponse:
        startup_ready = bool(app.state.startup_complete)
        persistence_ready = bool(app.state.persistence_ready)
        orchestration_ready = bool(app.state.orchestration_ready)
        ready = startup_ready and persistence_ready and orchestration_ready
        return JSONResponse(
            status_code=200 if ready else 503,
            content={
                "status": "ready" if ready else "not_ready",
                "service": "collaborative-taskmaster-studio",
                "checks": {
                    "application": "ready" if startup_ready else "not_ready",
                    "persistence": "ready" if persistence_ready else "not_ready",
                    "orchestration": "ready" if orchestration_ready else "not_ready",
                },
            },
        )

    @app.get("/api/v1/meta", tags=["system"])
    async def meta() -> dict[str, object]:
        model_active = model_gateway is not None
        return {
            "name": "Collaborative Taskmaster Studio",
            "version": "0.1.0",
            "mode": "gemini" if model_active else "local",
            "cloud_calls_enabled": cloud_calls_enabled,
            "runtime_ui": {
                "mode": "gemini" if model_active else "local",
                "model": (
                    active_vertex_settings.model if model_active else "fallback-determinista-local"
                ),
                "label": (
                    active_vertex_settings.model.replace("gemini-", "Gemini ").replace(
                        "-flash", " Flash"
                    )
                    if model_active
                    else "Fallback local"
                ),
                "provider": "Vertex AI" if model_active else "Sin llamadas cloud",
                "model_calls_enabled": model_active,
            },
            "stage": "H10-10",
            "fallback": "Diseñador determinista local",
            "generator": "Google ADK templates 1.0.0",
            "framework_selection": {
                "mode": "automatic",
                "available": ["google_adk", "genai_sdk", "antigravity", "genkit"],
                "policy": "deterministic-signals",
            },
            "agent_builder": builder_readiness.model_dump(mode="json"),
            "build_orchestration": (
                services.chat_builder.readiness()
                if services.chat_builder is not None
                else {
                    "durable_queue": False,
                    "worker_isolated": False,
                    "runtime": "unavailable",
                    "max_attempts": 0,
                    "restart_recovery": False,
                }
            ),
            "plugin_registry": {
                "selection_mode": "automatic_least_privilege",
                "declared": len(plugin_registry.list()),
                "available": len(
                    [item for item in plugin_registry.list() if item.availability == "available"]
                ),
                "connection_required": len(
                    [
                        item
                        for item in plugin_registry.list()
                        if item.availability == "connection_required"
                    ]
                ),
            },
            "identity": {
                "mode": active_identity_verifier.settings.mode,
                "multi_user": True,
                "verified_tokens_required": (
                    active_identity_verifier.settings.mode == "identity_platform"
                ),
                "isolation": "user_and_workspace",
                "firebase_config": {
                    "apiKey": os.getenv("STUDIO_FIREBASE_API_KEY", ""),
                    "authDomain": os.getenv("STUDIO_FIREBASE_AUTH_DOMAIN", ""),
                    "projectId": active_identity_verifier.settings.project_id,
                    "appId": os.getenv("STUDIO_FIREBASE_APP_ID", ""),
                },
            },
            "collaborative_capabilities": {
                "workspace_read": {
                    "active": True,
                    "scope": "studio_project",
                    "mode": "bounded_research_on_request",
                    "cloud_processing": "Vertex AI",
                    "max_operations_per_message": 4,
                    "max_searched_files": 200,
                }
            },
            "laboratory": "sandbox local sin credenciales",
            "cloud_run_identity": {
                "status": "declared",
                "account_id": runtime_identity.account_id,
                "purpose": runtime_identity.purpose,
                "user_managed_keys_allowed": (runtime_identity.user_managed_keys_allowed),
                "cloud_verified": False,
                "roles_assigned": False,
            },
            "cloud_run_iam": {
                "status": "declared",
                "exact_project_roles": runtime_iam.exact_project_roles,
                "roles": [binding.role for binding in runtime_iam.bindings],
                "firestore_database_scoped": True,
                "storage_bucket_scoped": True,
                "cloud_verified": False,
                "bindings_applied": False,
            },
            "build_pipeline": {
                "status": "declared",
                "region": build_pipeline.region,
                "repository": build_pipeline.repository.repository_id,
                "repository_format": build_pipeline.repository.format,
                "immutable_tags": build_pipeline.repository.immutable_tags,
                "builder_account_id": build_pipeline.builder_identity.account_id,
                "builder_roles": [binding.role for binding in build_pipeline.builder_bindings],
                "cloudbuild_config_verification_phase": "pre_build",
                "cloud_verified": False,
                "resources_applied": False,
                "build_submitted": False,
            },
            "runtime_configuration": {
                "status": "declared",
                "environment_variable_count": len(runtime_configuration.environment_variables),
                "secret_reference_count": len(runtime_configuration.secrets),
                "secret_provider": runtime_configuration.secret_policy.provider,
                "plaintext_secrets_allowed": (
                    runtime_configuration.secret_policy.allow_plaintext_values
                ),
                "latest_secret_alias_allowed": (
                    runtime_configuration.secret_policy.latest_alias_allowed
                ),
                "runtime_secret_accessor_required": (
                    runtime_configuration.runtime_secret_accessor_required
                ),
                "repository_scan_required": True,
                "repository_scan_phase": "pre_build",
                "cloud_verified": False,
                "configuration_applied": False,
                "secret_payloads_created": False,
            },
            "cloud_run_deployment": {
                "status": "declared",
                "service": cloud_run_deployment.service_name,
                "region": cloud_run_deployment.region,
                "image_digest_required": (cloud_run_deployment.require_image_digest),
                "scaling_scope": cloud_run_deployment.scaling_scope,
                "min_instances": cloud_run_deployment.service_min_instances,
                "max_instances": cloud_run_deployment.service_max_instances,
                "container_concurrency": (cloud_run_deployment.container_concurrency),
                "revision_min_instances": (cloud_run_deployment.revision_min_instances),
                "runtime_account_id": runtime_identity.account_id,
                "public_access": cloud_run_deployment.allow_unauthenticated,
                "cloud_verified": False,
                "deployment_executed": False,
                "service_ready": False,
                "public_url": None,
            },
            "adk_root": {
                "entrypoint": "agents.agent:root_agent",
                "declared_tools": 0,
                "delegation_tools": 2,
                "loading": "lazy",
                "specialists": ["interviewer_agent", "designer_agent"],
            },
            "model_telemetry": {
                "outcomes": ["completed", "fallback"],
                "fields": [
                    "provider",
                    "model",
                    "model_version",
                    "location",
                    "response_id",
                    "latency_ms",
                    "usage",
                ],
                "sensitive_content_recorded": False,
            },
            "model_limits": {
                "max_output_tokens": active_vertex_settings.max_model_output_tokens,
                "max_questions_per_project": (
                    active_vertex_settings.max_model_questions_per_project
                ),
                "question_limit_fallback": "local_catalog",
            },
            "local_fallback": {
                "mode": "deterministic",
                "strategies": [
                    "local_catalog",
                    "local_parser",
                    "deterministic_designer",
                    "deterministic_reviewer",
                ],
                "state_preserved": True,
                "audited": True,
            },
            "recorded_contracts": {
                "schema_version": "1.0.0",
                "recordings": 5,
                "sanitized": True,
                "live_calls": False,
            },
            "firestore_database": {
                "status": firestore_runtime.readiness.status,
                "database_id": active_firestore_settings.database_id,
                "location": active_firestore_settings.location,
                "type": "FIRESTORE_NATIVE",
                "edition": "STANDARD",
                "delete_protection": True,
                "runtime_enabled": firestore_runtime.readiness.client_initialized,
                "database_verified": firestore_runtime.readiness.database_verified,
                "repository_active": firestore_runtime.readiness.repository_active,
                "cloud_calls_enabled": firestore_runtime.readiness.cloud_calls_enabled,
                "credentials_source": firestore_runtime.readiness.credentials_source,
            },
            "project_storage": {
                "status": storage_runtime.readiness.status,
                "durable": storage_runtime.readiness.status == "ready",
                "bucket": active_storage_settings.bucket,
                "prefix": active_storage_settings.prefix,
                "local_workspace": str(projects_root),
                "archive_format": None,
            },
            "firestore_project_repository": {
                "implemented": True,
                "active": firestore_runtime.readiness.repository_active,
                "collection": "projects",
                "subcollections": [
                    "briefings",
                    "revisions",
                    "approvals",
                    "events",
                    "artifacts",
                ],
                "owner_scoped": True,
                "optimistic_concurrency": True,
                "immutable_revisions": True,
                "human_approval_records": True,
                "ordered_audit_events": True,
                "artifact_metadata_only": True,
                "critical_transactions": True,
                "transaction_max_attempts": (active_firestore_settings.transaction_max_attempts),
            },
            "firestore_indexes": firestore_indexes.as_dict(),
            "firestore_retention": firestore_retention.as_dict(),
            "vertex": vertex_readiness.model_dump(mode="json"),
            "model_features": {
                "questions": question_generator is not None,
                "briefing": briefing_generator is not None,
                "specification": specification_generator is not None,
                "revision": revision_generator is not None,
            },
        }

    @app.get("/", include_in_schema=False)
    async def index() -> FileResponse:
        return FileResponse(
            STATIC / "index.html",
            headers={"Cache-Control": "no-cache, must-revalidate"},
        )

    return app


def _domain_status(code: str) -> int:
    if code in {"ENTITY_NOT_FOUND", "REVISION_NOT_FOUND", "EVALUATION_NOT_FOUND", "BUILD_NOT_FOUND"}:
        return 404
    if code in {"WORKER_AUTH_REQUIRED", "WORKER_AUTH_INVALID", "WORKER_TASK_INVALID"}:
        return 401
    if code in {"WORKER_DISABLED", "BUILD_DISPATCH_UNAVAILABLE", "BUILD_DISPATCH_FAILED"}:
        return 503
    if code in {"PROJECT_ACCESS_DENIED"}:
        return 403
    if "CONFLICT" in code or code in {
        "DESIGN_ALREADY_REVISED",
        "REVISION_IMMUTABLE",
        "STALE_APPROVAL_REVISION",
    }:
        return 409
    return 400


def _error_response(
    request: Request,
    status: int,
    code: str,
    message: str,
    *,
    context: dict[str, Any] | None = None,
) -> JSONResponse:
    request_id = getattr(request.state, "request_id", uuid4().hex)
    return JSONResponse(
        status_code=status,
        content={
            "error": {
                "code": code,
                "message": message,
                "context": context or {},
                "request_id": request_id,
            }
        },
        headers={"X-Request-ID": request_id},
    )


app = create_app()


def run() -> None:
    binding = resolve_server_binding()
    uvicorn.run(
        "app.main:app",
        host=binding.host,
        port=binding.port,
        reload=False,
    )


if __name__ == "__main__":
    run()
