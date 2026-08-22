"""Versioned API routes that expose the H3/H4 application services."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Annotated, Any

from fastapi import APIRouter, File, Header, Query, Request, Response, UploadFile
from fastapi.responses import HTMLResponse

from app.api.schemas import (
    AgentDecisionRequest,
    AgentMessageRequest,
    ApprovalRequest,
    BriefingCorrectionRequest,
    CatalogAgentUpdateRequest,
    ChatBuildDecisionRequest,
    ChatBuildRequest,
    CollaborativeChatRequest,
    CollaborativeConversationRequest,
    CreateProjectRequest,
    DemoResetRequest,
    EvaluationRequest,
    FeedbackRequest,
    GenerationRequest,
    InterviewAnswerRequest,
)
from studio.application.agent_catalog import AgentCatalog
from studio.application.agent_runtime_service import AgentRuntimeService
from studio.application.approval_service import ApprovalService
from studio.application.briefing_generator import StructuredBriefingGenerator
from studio.application.builder_readiness import BuilderReadiness, inspect_builder_readiness
from studio.application.chat_build_service import ChatBuildService
from studio.application.collaborative_chat_service import (
    ChatTurn,
    CollaborativeChatService,
)
from studio.application.connection_service import ConnectionService
from studio.application.conversation_memory import ConversationMemoryService
from studio.application.demo_reset import DemoResetService
from studio.application.deployment_readiness import assess_deployment
from studio.application.design_service import DesignService
from studio.application.evaluation_service import EvaluationService
from studio.application.export_service import AgentExportService
from studio.application.generation_service import GenerationService
from studio.application.interview_question_generator import (
    StructuredInterviewQuestionGenerator,
)
from studio.application.interview_service import InterviewService
from studio.application.plugin_registry import PluginRegistry
from studio.application.project_service import ProjectService
from studio.application.revision_generator import StructuredRevisionGenerator
from studio.application.specification_generator import StructuredSpecificationGenerator
from studio.capabilities.documents import MAX_UPLOAD_BYTES, DocumentLibrary
from studio.capabilities.google_drive import GoogleDriveReader
from studio.domain.errors import DomainError
from studio.domain.models import AuditEvent, ProjectSnapshot
from studio.ports.clock import Clock
from studio.ports.repositories import EventRepository, ProjectRepository
from studio.security import IdentityContext

SessionHeader = Annotated[
    str,
    Header(alias="X-Studio-Session", min_length=3, max_length=128, pattern=r"^[A-Za-z0-9_-]+$"),
]
IdempotencyHeader = Annotated[
    str,
    Header(alias="Idempotency-Key", min_length=8, max_length=200),
]


@dataclass(frozen=True, slots=True)
class ServiceContainer:
    projects: ProjectService
    interview: InterviewService
    design: DesignService
    approval: ApprovalService
    generation: GenerationService
    evaluation: EvaluationService
    export: AgentExportService
    demo_reset: DemoResetService
    agent_runtime: AgentRuntimeService
    collaborative_chat: CollaborativeChatService
    conversation_memory: ConversationMemoryService
    documents: DocumentLibrary
    chat_builder: ChatBuildService | None
    agent_catalog: AgentCatalog | None
    plugin_registry: PluginRegistry
    connections: ConnectionService
    google_drive: GoogleDriveReader | None
    builder_readiness: BuilderReadiness
    repository: ProjectRepository
    events: EventRepository

    @classmethod
    def build(
        cls,
        repository: ProjectRepository,
        events: EventRepository,
        clock: Clock,
        generation: GenerationService,
        evaluation: EvaluationService,
        export: AgentExportService,
        demo_reset: DemoResetService,
        question_generator: StructuredInterviewQuestionGenerator | None = None,
        briefing_generator: StructuredBriefingGenerator | None = None,
        specification_generator: StructuredSpecificationGenerator | None = None,
        revision_generator: StructuredRevisionGenerator | None = None,
        max_model_questions_per_project: int = 3,
        agent_runtime: AgentRuntimeService | None = None,
        collaborative_chat: CollaborativeChatService | None = None,
        conversation_memory: ConversationMemoryService | None = None,
        documents: DocumentLibrary | None = None,
        chat_builder: ChatBuildService | None = None,
        agent_catalog: AgentCatalog | None = None,
        plugin_registry: PluginRegistry | None = None,
        connections: ConnectionService | None = None,
        google_drive: GoogleDriveReader | None = None,
        builder_readiness: BuilderReadiness | None = None,
    ) -> ServiceContainer:
        if agent_runtime is None:
            agent_runtime = AgentRuntimeService(repository, events, clock)
        if collaborative_chat is None:
            collaborative_chat = CollaborativeChatService(None, "gemini-3.7-flash")
        if conversation_memory is None:
            from infrastructure.local.conversation_memory import (
                InMemoryConversationMemoryRepository,
            )

            conversation_memory = ConversationMemoryService(
                InMemoryConversationMemoryRepository(), clock
            )
        if documents is None:
            from pathlib import Path

            documents = DocumentLibrary(Path(".studio-data"))
        active_registry = plugin_registry or PluginRegistry()
        if connections is None:
            from pathlib import Path

            connections = ConnectionService(Path(".studio-data"), active_registry)
        return cls(
            projects=ProjectService(repository, events, clock),
            interview=InterviewService(
                repository,
                events,
                clock,
                question_generator,
                briefing_generator,
                max_model_questions_per_project,
            ),
            design=DesignService(
                repository,
                events,
                clock,
                specification_generator=specification_generator,
                revision_generator=revision_generator,
            ),
            approval=ApprovalService(repository, events, clock),
            generation=generation,
            evaluation=evaluation,
            export=export,
            demo_reset=demo_reset,
            agent_runtime=agent_runtime,
            collaborative_chat=collaborative_chat,
            conversation_memory=conversation_memory,
            documents=documents,
            chat_builder=chat_builder,
            agent_catalog=agent_catalog,
            plugin_registry=active_registry,
            connections=connections,
            google_drive=google_drive,
            builder_readiness=builder_readiness or inspect_builder_readiness(),
            repository=repository,
            events=events,
        )


def create_router(services: ServiceContainer) -> APIRouter:
    router = APIRouter(prefix="/api/v1")

    @router.post("/collaborative/messages")
    def collaborative_message(
        body: CollaborativeChatRequest,
        session_id: SessionHeader,
        request: Request,
    ) -> dict[str, Any]:
        identity = _identity(request)
        result = services.collaborative_chat.reply(
            body.message,
            tuple(
                ChatTurn(
                    role=turn.role,
                    content=turn.content,
                    evidence=tuple(turn.evidence),
                )
                for turn in body.history
            ),
            owner_session_id=session_id,
            conversation_id=body.conversation_id,
            document_ids=tuple(body.document_ids),
            identity=identity,
        )
        payload = result.model_dump(mode="json")
        payload["connection_offers"] = [
            item.model_dump(mode="json")
            for item in services.connections.offers(
                identity,
                body.message,
                tuple(result.agent_draft.external_actions),
            )
        ]
        return payload

    @router.post("/collaborative/documents", status_code=201)
    async def upload_collaborative_document(
        session_id: SessionHeader,
        file: Annotated[UploadFile, File()],
    ) -> dict[str, Any]:
        payload = await file.read(MAX_UPLOAD_BYTES + 1)
        return services.documents.add(
            session_id,
            file.filename or "documento",
            payload,
        ).summary()

    @router.get("/collaborative/documents")
    def list_collaborative_documents(session_id: SessionHeader) -> dict[str, Any]:
        return {"documents": list(services.documents.list(session_id))}

    @router.delete("/collaborative/documents/{document_id}", status_code=204)
    def delete_collaborative_document(
        document_id: str,
        session_id: SessionHeader,
    ) -> Response:
        services.documents.delete(session_id, document_id)
        return Response(status_code=204)

    @router.get("/collaborative/conversations")
    def list_collaborative_conversations(session_id: SessionHeader) -> dict[str, Any]:
        return {
            "conversations": [
                item.model_dump(mode="json")
                for item in services.conversation_memory.list(session_id)
            ]
        }

    @router.put("/collaborative/conversations/{conversation_id}")
    def save_collaborative_conversation(
        conversation_id: str,
        body: CollaborativeConversationRequest,
        session_id: SessionHeader,
    ) -> dict[str, Any]:
        return services.conversation_memory.save(
            session_id,
            conversation_id=conversation_id,
            title=body.title,
            messages=body.messages,
            phase=body.phase,
            document_ids=body.document_ids,
        ).model_dump(mode="json")

    @router.delete("/collaborative/conversations/{conversation_id}", status_code=204)
    def delete_collaborative_conversation(
        conversation_id: str,
        session_id: SessionHeader,
    ) -> Response:
        services.conversation_memory.delete(session_id, conversation_id)
        return Response(status_code=204)

    @router.post("/collaborative/builds", status_code=202)
    def start_chat_build(
        body: ChatBuildRequest,
        session_id: SessionHeader,
    ) -> dict[str, Any]:
        if services.chat_builder is None:
            raise DomainError(
                "CHAT_BUILDER_UNAVAILABLE", "El Ingeniero de agentes no está disponible."
            )
        return services.chat_builder.start(
            body.agent_draft,
            owner_session_id=session_id,
            confirmation=body.confirmation,
        ).model_dump(mode="json")

    @router.get("/collaborative/builds/{build_id}")
    def get_chat_build(
        build_id: str,
        session_id: SessionHeader,
        after_sequence: int = 0,
    ) -> dict[str, Any]:
        if services.chat_builder is None:
            raise DomainError(
                "CHAT_BUILDER_UNAVAILABLE", "El Ingeniero de agentes no está disponible."
            )
        return services.chat_builder.get(
            build_id,
            owner_session_id=session_id,
            after_sequence=after_sequence,
        ).model_dump(mode="json")

    @router.post("/collaborative/builds/{build_id}/test-decision")
    def decide_chat_build_tests(
        build_id: str,
        body: ChatBuildDecisionRequest,
        session_id: SessionHeader,
    ) -> dict[str, Any]:
        if services.chat_builder is None:
            raise DomainError(
                "CHAT_BUILDER_UNAVAILABLE", "El Ingeniero de agentes no está disponible."
            )
        return services.chat_builder.decide_tests(
            build_id,
            owner_session_id=session_id,
            decision=body.decision,
        ).model_dump(mode="json")

    @router.get("/collaborative/builds/{build_id}/download.zip")
    def download_chat_build(build_id: str, session_id: SessionHeader) -> Response:
        if services.chat_builder is None:
            raise DomainError(
                "CHAT_BUILDER_UNAVAILABLE", "El Ingeniero de agentes no está disponible."
            )
        filename, content = services.chat_builder.download(build_id, owner_session_id=session_id)
        return Response(
            content=content,
            media_type="application/zip",
            headers={
                "Content-Disposition": f'attachment; filename="{filename}"',
                "Cache-Control": "no-store",
            },
        )

    @router.get("/collaborative/plugins")
    def list_collaborative_plugins(session_id: SessionHeader) -> dict[str, Any]:
        del session_id
        return {
            "selection_mode": "automatic_least_privilege",
            "plugins": [item.model_dump(mode="json") for item in services.plugin_registry.list()],
        }

    @router.get("/collaborative/identity")
    def collaborative_identity(request: Request, session_id: SessionHeader) -> dict[str, Any]:
        del session_id
        return _identity(request).model_dump(mode="json")

    @router.get("/collaborative/connections")
    def list_collaborative_connections(
        request: Request,
        session_id: SessionHeader,
    ) -> dict[str, Any]:
        del session_id
        return {
            "connections": [
                item.model_dump(mode="json")
                for item in services.connections.list(_identity(request))
            ]
        }

    @router.post("/collaborative/connections/{plugin_id}/start")
    def start_collaborative_connection(
        plugin_id: str,
        request: Request,
        session_id: SessionHeader,
    ) -> dict[str, Any]:
        del session_id
        return services.connections.begin(_identity(request), plugin_id).model_dump(mode="json")

    @router.get("/collaborative/connections/oauth/callback", include_in_schema=False)
    def complete_collaborative_connection(
        request: Request,
        state: Annotated[str, Query(min_length=20, max_length=2_000)],
        code: Annotated[str | None, Query(max_length=4_000)] = None,
        error: Annotated[str | None, Query(max_length=200)] = None,
    ) -> HTMLResponse:
        record = services.connections.complete_callback(
            state=state,
            code=code,
            oauth_error=error,
        )
        outcome = "connected" if record.status == "connected" else "error"
        origin = str(request.base_url).rstrip("/")
        message = json.dumps(
            {
                "type": "studio-oauth-result",
                "outcome": outcome,
                "provider": record.plugin_id,
            }
        )
        fallback = f"/?connection={outcome}&provider={record.plugin_id}"
        html = (
            "<!doctype html><html lang='es'><head><meta charset='utf-8'>"
            "<meta name='viewport' content='width=device-width,initial-scale=1'>"
            "<title>Conexión completada</title></head><body>"
            "<p>La autorización terminó. Esta ventana se cerrará automáticamente.</p>"
            "<script>"
            f"const result={message};const target={json.dumps(origin)};"
            "if(window.opener&&!window.opener.closed){window.opener.postMessage(result,target);window.close();}"
            f"else{{window.location.replace({json.dumps(fallback)});}}"
            "</script></body></html>"
        )
        return HTMLResponse(
            html,
            headers={
                "Cache-Control": "no-store",
                "Content-Security-Policy": "default-src 'none'; script-src 'unsafe-inline'; style-src 'none'",
                "Referrer-Policy": "no-referrer",
            },
        )

    @router.delete("/collaborative/connections/{connection_id}")
    def revoke_collaborative_connection(
        connection_id: str,
        request: Request,
        session_id: SessionHeader,
    ) -> dict[str, Any]:
        del session_id
        return services.connections.revoke(
            _identity(request), connection_id
        ).model_dump(mode="json")

    @router.get("/collaborative/connections/google.drive/files")
    def search_google_drive(
        request: Request,
        session_id: SessionHeader,
        query: Annotated[str, Query(max_length=120)] = "",
        limit: Annotated[int, Query(ge=1, le=25)] = 10,
    ) -> dict[str, object]:
        del session_id
        if services.google_drive is None:
            raise DomainError("DRIVE_UNAVAILABLE", "La lectura de Drive no está configurada.")
        return services.google_drive.search(_identity(request), query, limit=limit)

    @router.get("/collaborative/connections/google.drive/files/{file_id}")
    def read_google_drive_file(
        file_id: str,
        request: Request,
        session_id: SessionHeader,
    ) -> dict[str, object]:
        del session_id
        if services.google_drive is None:
            raise DomainError("DRIVE_UNAVAILABLE", "La lectura de Drive no está configurada.")
        return services.google_drive.read(_identity(request), file_id)

    @router.get("/collaborative/agents")
    def list_catalog_agents(session_id: SessionHeader) -> dict[str, Any]:
        agents = services.agent_catalog.list(session_id) if services.agent_catalog else ()
        return {"agents": [item.model_dump(mode="json") for item in agents]}

    @router.patch("/collaborative/agents/{agent_id}")
    def update_catalog_agent(
        agent_id: str,
        body: CatalogAgentUpdateRequest,
        session_id: SessionHeader,
    ) -> dict[str, Any]:
        if services.agent_catalog is None:
            raise DomainError(
                "AGENT_CATALOG_UNAVAILABLE", "El catálogo de agentes no está disponible."
            )
        return services.agent_catalog.update(
            agent_id,
            session_id,
            name=body.name,
            icon=body.icon,
        ).model_dump(mode="json")

    @router.delete("/collaborative/agents/{agent_id}", status_code=204)
    def archive_catalog_agent(agent_id: str, session_id: SessionHeader) -> Response:
        if services.agent_catalog is None:
            raise DomainError(
                "AGENT_CATALOG_UNAVAILABLE", "El catálogo de agentes no está disponible."
            )
        services.agent_catalog.archive(agent_id, session_id)
        return Response(status_code=204)

    @router.get("/collaborative/agents/{agent_id}/deployment-readiness")
    def catalog_agent_deployment_readiness(
        agent_id: str,
        session_id: SessionHeader,
    ) -> dict[str, Any]:
        if services.agent_catalog is None:
            raise DomainError(
                "AGENT_CATALOG_UNAVAILABLE", "El catálogo de agentes no está disponible."
            )
        agent = services.agent_catalog.get(agent_id, session_id)
        return assess_deployment(agent, services.builder_readiness).model_dump(mode="json")

    @router.post("/projects", status_code=201)
    def create_project(
        body: CreateProjectRequest,
        session_id: SessionHeader,
        idempotency_key: IdempotencyHeader,
    ) -> dict[str, Any]:
        project_id = _project_id(session_id, idempotency_key)
        snapshot = services.projects.create_project(
            project_id=project_id,
            name=body.name,
            description=body.description,
            owner_session_id=session_id,
            idempotency_key=idempotency_key,
        )
        return _snapshot_payload(snapshot, services.events.list_for_project(project_id))

    @router.get("/projects/{project_id}")
    def get_project(project_id: str, session_id: SessionHeader) -> dict[str, Any]:
        snapshot = services.projects.get_snapshot(project_id, owner_session_id=session_id)
        return _snapshot_payload(snapshot, services.events.list_for_project(project_id))

    @router.post("/projects/{project_id}/demo/reset")
    def reset_demo(
        project_id: str,
        body: DemoResetRequest,
        session_id: SessionHeader,
        idempotency_key: IdempotencyHeader,
    ) -> dict[str, Any]:
        return services.demo_reset.reset(
            project_id,
            owner_session_id=session_id,
            confirmation=body.confirmation,
            idempotency_key=idempotency_key,
        ).model_dump(mode="json")

    @router.post("/projects/{project_id}/interview/start")
    def start_interview(
        project_id: str,
        session_id: SessionHeader,
        idempotency_key: IdempotencyHeader,
    ) -> dict[str, Any]:
        return services.interview.start(
            project_id,
            owner_session_id=session_id,
            idempotency_key=idempotency_key,
        ).model_dump(mode="json")

    @router.post("/projects/{project_id}/interview/messages")
    def record_interview_answer(
        project_id: str,
        body: InterviewAnswerRequest,
        session_id: SessionHeader,
        idempotency_key: IdempotencyHeader,
    ) -> dict[str, Any]:
        return services.interview.record_answer(
            project_id,
            question_id=body.question_id,
            answer=body.answer,
            owner_session_id=session_id,
            idempotency_key=idempotency_key,
        ).model_dump(mode="json")

    @router.patch("/projects/{project_id}/briefing")
    def correct_briefing(
        project_id: str,
        body: BriefingCorrectionRequest,
        session_id: SessionHeader,
        idempotency_key: IdempotencyHeader,
    ) -> dict[str, Any]:
        return services.interview.correct_field(
            project_id,
            field_name=body.field,
            value=body.value,
            owner_session_id=session_id,
            idempotency_key=idempotency_key,
        ).model_dump(mode="json")

    @router.post("/projects/{project_id}/briefing/confirm")
    def confirm_briefing(
        project_id: str,
        session_id: SessionHeader,
        idempotency_key: IdempotencyHeader,
    ) -> dict[str, Any]:
        return services.interview.confirm_briefing(
            project_id,
            owner_session_id=session_id,
            idempotency_key=idempotency_key,
        ).model_dump(mode="json")

    @router.post("/projects/{project_id}/revisions", status_code=201)
    def create_revision(
        project_id: str,
        session_id: SessionHeader,
        idempotency_key: IdempotencyHeader,
    ) -> dict[str, Any]:
        return services.design.create_initial_revision(
            project_id,
            owner_session_id=session_id,
            idempotency_key=idempotency_key,
        ).model_dump(mode="json")

    @router.get("/projects/{project_id}/revisions/{revision}")
    def get_revision(
        project_id: str,
        revision: int,
        session_id: SessionHeader,
    ) -> dict[str, Any]:
        snapshot = services.projects.get_snapshot(project_id, owner_session_id=session_id)
        stored = next((item for item in snapshot.revisions if item.number == revision), None)
        if stored is None:
            from studio.domain.errors import DomainError

            raise DomainError(
                "REVISION_NOT_FOUND",
                f"No existe la revisión {revision}.",
                context={"revision": revision},
            )
        return {
            "revision": stored.model_dump(mode="json", by_alias=True),
            "overview": services.design.get_overview(
                project_id,
                revision=revision,
                owner_session_id=session_id,
            ).model_dump(mode="json"),
        }

    @router.post("/projects/{project_id}/revisions/{revision}/feedback", status_code=201)
    def apply_feedback(
        project_id: str,
        revision: int,
        body: FeedbackRequest,
        session_id: SessionHeader,
        idempotency_key: IdempotencyHeader,
    ) -> dict[str, Any]:
        if revision != body.expected_revision:
            from studio.domain.errors import DomainError

            raise DomainError(
                "REVISION_REQUEST_MISMATCH",
                "La ruta y el cuerpo deben señalar la misma revisión.",
            )
        return services.design.apply_feedback(
            project_id,
            expected_revision=revision,
            feedback=body.feedback,
            owner_session_id=session_id,
            idempotency_key=idempotency_key,
        ).model_dump(mode="json")

    @router.get("/projects/{project_id}/revisions/{revision}/diff")
    def get_diff(
        project_id: str,
        revision: int,
        session_id: SessionHeader,
        from_revision: int = 1,
    ) -> dict[str, Any]:
        return services.design.get_diff(
            project_id,
            from_revision=from_revision,
            to_revision=revision,
            owner_session_id=session_id,
        ).model_dump(mode="json")

    @router.post("/projects/{project_id}/revisions/{revision}/approval")
    def decide_revision(
        project_id: str,
        revision: int,
        body: ApprovalRequest,
        session_id: SessionHeader,
        idempotency_key: IdempotencyHeader,
    ) -> dict[str, Any]:
        snapshot = services.approval.decide(
            project_id,
            revision=revision,
            decision=body.decision,
            actor_id=session_id,
            actor_type="human",
            note=body.note,
            approval_id=f"{project_id}_r{revision}_{body.decision}",
            owner_session_id=session_id,
            idempotency_key=idempotency_key,
        )
        return _snapshot_payload(snapshot, services.events.list_for_project(project_id))

    @router.get("/projects/{project_id}/events")
    def list_events(
        project_id: str,
        session_id: SessionHeader,
        after_sequence: int = 0,
    ) -> list[dict[str, Any]]:
        services.projects.get_snapshot(project_id, owner_session_id=session_id)
        return [
            event.model_dump(mode="json")
            for event in services.events.list_for_project(project_id, after_sequence=after_sequence)
        ]

    @router.post("/projects/{project_id}/generation", status_code=201)
    def generate_project(
        project_id: str,
        body: GenerationRequest,
        session_id: SessionHeader,
        idempotency_key: IdempotencyHeader,
    ) -> dict[str, Any]:
        return services.generation.generate(
            project_id,
            revision=body.revision,
            owner_session_id=session_id,
            idempotency_key=idempotency_key,
        ).model_dump(mode="json")

    @router.get("/projects/{project_id}/artifacts")
    def list_artifacts(project_id: str, session_id: SessionHeader) -> list[dict[str, Any]]:
        snapshot = services.projects.get_snapshot(project_id, owner_session_id=session_id)
        return [artifact.model_dump(mode="json") for artifact in snapshot.artifacts]

    @router.get("/projects/{project_id}/export.zip")
    def export_agent(project_id: str, session_id: SessionHeader) -> Response:
        package = services.export.export(project_id, owner_session_id=session_id)
        return Response(
            content=package.content,
            media_type="application/zip",
            headers={
                "Content-Disposition": f'attachment; filename="{package.filename}"',
                "X-Agent-File-Count": str(package.file_count),
                "Cache-Control": "no-store",
            },
        )

    @router.post("/projects/{project_id}/evaluations", status_code=201)
    def evaluate_project(
        project_id: str,
        body: EvaluationRequest,
        session_id: SessionHeader,
        idempotency_key: IdempotencyHeader,
    ) -> dict[str, Any]:
        return services.evaluation.evaluate(
            project_id,
            revision=body.revision,
            owner_session_id=session_id,
            idempotency_key=idempotency_key,
        ).model_dump(mode="json")

    @router.get("/projects/{project_id}/evaluations/{revision}")
    def get_evaluation(
        project_id: str,
        revision: int,
        session_id: SessionHeader,
    ) -> dict[str, Any]:
        return services.evaluation.get(
            project_id, revision=revision, owner_session_id=session_id
        ).model_dump(mode="json")

    @router.post("/projects/{project_id}/agent/messages")
    def run_agent_message(
        project_id: str,
        body: AgentMessageRequest,
        request: Request,
        session_id: SessionHeader,
        idempotency_key: IdempotencyHeader,
    ) -> dict[str, Any]:
        return services.agent_runtime.run(
            project_id,
            message=body.message,
            owner_session_id=session_id,
            idempotency_key=idempotency_key,
            identity=_identity(request),
        ).model_dump(mode="json")

    @router.post("/projects/{project_id}/agent/decisions")
    def decide_agent_output(
        project_id: str,
        body: AgentDecisionRequest,
        session_id: SessionHeader,
        idempotency_key: IdempotencyHeader,
    ) -> dict[str, str]:
        return services.agent_runtime.decide(
            project_id,
            run_id=body.run_id,
            decision=body.decision,
            note=body.note,
            owner_session_id=session_id,
            idempotency_key=idempotency_key,
        )

    @router.get("/request-context", include_in_schema=False)
    def request_context(request: Request) -> dict[str, str]:
        return {"request_id": request.state.request_id}

    return router


def _identity(request: Request) -> IdentityContext:
    identity = getattr(request.state, "identity", None)
    if not isinstance(identity, IdentityContext):
        raise DomainError("AUTHENTICATION_REQUIRED", "No fue posible resolver la identidad.")
    return identity


def _snapshot_payload(
    snapshot: ProjectSnapshot,
    events: tuple[AuditEvent, ...],
) -> dict[str, Any]:
    return {
        "snapshot": snapshot.model_dump(mode="json", by_alias=True),
        "events": [event.model_dump(mode="json") for event in events],
    }


def _project_id(session_id: str, idempotency_key: str) -> str:
    digest = hashlib.sha256(f"{session_id}:{idempotency_key}".encode()).hexdigest()[:16]
    return f"project_{digest}"
