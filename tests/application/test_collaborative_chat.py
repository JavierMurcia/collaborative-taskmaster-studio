from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from app.api.schemas import CollaborativeChatRequest
from studio.application.collaborative_chat_service import (
    ChatTurn,
    CollaborativeChatService,
)
from studio.application.connection_service import ConnectionRecord
from studio.capabilities.workspace import WorkspaceReader
from studio.domain.errors import DomainError
from studio.ports.model_gateway import (
    ModelMetadata,
    ModelRequest,
    ModelResult,
    ModelUsage,
)
from studio.security import IdentityContext


class RecordingGateway:
    def __init__(self, readiness: int = 70, workspace_path: str = "") -> None:
        self.request: ModelRequest | None = None
        self.requests: list[ModelRequest] = []
        self.readiness = readiness
        self.workspace_path = workspace_path

    def generate_structured(self, request: ModelRequest) -> ModelResult:
        self.request = request
        self.requests.append(request)
        return ModelResult(
            payload={
                "reply": "Entiendo que quieres ordenar un proceso complejo. ¿Qué resultado final necesitas?",
                "phase": "discovery",
                "intent": "agent_creation",
                "workspace_action": (
                    "inspect" if self.workspace_path and len(self.requests) == 1 else "none"
                ),
                "workspace_path": self.workspace_path if len(self.requests) == 1 else "",
                "workspace_query": "",
                "agent_draft": {
                    "name": "Organizador de entregas",
                    "purpose": "Organizar entregas y comprobar sus evidencias.",
                    "intended_user": "Estudiante",
                    "inputs": ["Requisitos"],
                    "outputs": ["Plan de entrega"],
                    "workflow": ["Recibir requisitos", "Preparar plan"],
                    "external_actions": [],
                    "constraints": ["No enviar sin aprobación"],
                    "approval_rule": "El estudiante aprueba el resultado.",
                    "success_criteria": ["No falta ninguna evidencia"],
                    "missing_information": ["Fecha límite"],
                    "readiness": self.readiness,
                    "ready_to_create": False,
                },
            },
            metadata=ModelMetadata(
                provider="vertex_ai",
                model="gemini-3.7-flash",
                model_version="gemini-3.7-flash-001",
                location="global",
                response_id="response-1",
                latency_ms=120,
                usage=ModelUsage(prompt_tokens=30, output_tokens=18, total_tokens=48),
            ),
        )


class ResearchGateway(RecordingGateway):
    def generate_structured(self, request: ModelRequest) -> ModelResult:
        result = super().generate_structured(request)
        step = len(self.requests)
        payload = dict(result.payload)
        if step == 1:
            payload.update(
                workspace_action="search", workspace_path=".", workspace_query="WorkspaceReader"
            )
        elif step == 2:
            payload.update(
                workspace_action="inspect",
                workspace_path="studio/capabilities/workspace.py",
                workspace_query="",
            )
        else:
            payload.update(workspace_action="none", workspace_path="", workspace_query="")
        return result.model_copy(update={"payload": payload})


class MalformedOnceGateway(RecordingGateway):
    def generate_structured(self, request: ModelRequest) -> ModelResult:
        if not self.requests:
            self.request = request
            self.requests.append(request)
            raise DomainError(
                "MODEL_OUTPUT_INVALID",
                "Vertex AI devolvió contenido que no es JSON válido.",
            )
        return super().generate_structured(request)


class RecordingWebResearcher:
    def __init__(self) -> None:
        self.searches: list[str] = []
        self.urls: list[str] = []

    def search(self, query: str) -> dict[str, object]:
        self.searches.append(query)
        return {
            "kind": "web_search",
            "query": query,
            "summary": "Resultado actual de 2026.",
            "sources": [{"title": "Fuente", "url": "https://example.com"}],
            "grounded": True,
        }

    def open_url(self, url: str) -> dict[str, object]:
        self.urls.append(url)
        return {
            "kind": "web_page",
            "url": url,
            "summary": "Página consultada directamente.",
            "sources": [{"title": "Página", "url": url}],
            "grounded": True,
        }


class FallbackWebResearcher(RecordingWebResearcher):
    def open_url(self, url: str) -> dict[str, object]:
        self.urls.append(url)
        raise DomainError("WEB_PAGE_UNVERIFIED", "La página bloqueó la lectura directa.")


class StaticConnections:
    def __init__(self, records: tuple[ConnectionRecord, ...]) -> None:
        self.records = records

    def list(self, identity: IdentityContext) -> tuple[ConnectionRecord, ...]:
        return self.records


class RecordingGitHub:
    def __init__(self) -> None:
        self.queries: list[str] = []

    def available(self, identity: IdentityContext) -> bool:
        return True

    def list_repositories(
        self, identity: IdentityContext, query: str = "", *, limit: int = 100
    ) -> dict[str, object]:
        del identity, limit
        self.queries.append(query)
        return {
            "kind": "github_repositories",
            "query": query,
            "visible_repository_count": 3,
            "matching_repository_count": 3,
            "repositories": [
                {
                    "id": "1",
                    "name": "studio",
                    "full_name": "JavierMurcia/studio",
                    "html_url": "https://github.com/JavierMurcia/studio",
                    "updated_at": "2026-08-24T12:00:00Z",
                }
            ],
            "read_only": True,
        }


class RecordingDrive:
    def __init__(self) -> None:
        self.queries: list[str] = []
        self.read_ids: list[str] = []

    def available(self, identity: IdentityContext) -> bool:
        return True

    def search(self, identity: IdentityContext, query: str) -> dict[str, object]:
        del identity
        self.queries.append(query)
        return {
            "kind": "google_drive_search",
            "query": query,
            "files": [
                {
                    "id": "doc-1",
                    "name": "Investigación de sistemas",
                    "mimeType": "application/pdf",
                }
            ],
            "read_only": True,
        }

    def read(self, identity: IdentityContext, file_id: str) -> dict[str, object]:
        del identity
        self.read_ids.append(file_id)
        return {
            "kind": "google_drive_file",
            "id": file_id,
            "name": "Investigación de sistemas",
            "mimeType": "application/pdf",
            "content": "Restricciones y oportunidades documentadas para el proyecto.",
            "read_only": True,
        }


def test_collaborative_chat_request_preserves_tool_evidence() -> None:
    request = CollaborativeChatRequest.model_validate(
        {
            "message": "¿Por qué no apareció?",
            "history": [
                {
                    "role": "assistant",
                    "content": "No lo encontré.",
                    "evidence": [
                        "web.search | completed | Internet | hackathons recientes 2026"
                    ],
                }
            ],
        }
    )

    assert request.history[0].evidence == [
        "web.search | completed | Internet | hackathons recientes 2026"
    ]


def test_collaborative_chat_uses_gemini_and_preserves_recent_context() -> None:
    gateway = RecordingGateway()
    service = CollaborativeChatService(gateway, "gemini-3.7-flash")

    result = service.reply(
        "Quiero organizar mejor mis entregas.",
        (ChatTurn(role="assistant", content="¿Qué te preocupa?"),),
    )

    assert result.runtime_mode == "gemini"
    assert result.model == "gemini-3.7-flash"
    assert result.phase == "discovery"
    assert result.intent == "agent_creation"
    assert result.agent_draft.readiness == 70
    assert result.agent_draft.recommended_framework is not None
    assert result.agent_draft.recommended_framework.framework == "google_adk"
    assert gateway.request is not None
    assert gateway.request.purpose == "collaborative_chat"
    assert "¿Qué te preocupa?" in gateway.request.prompt
    assert "lo acompañas en su diseño" in gateway.request.system_instruction
    assert "confirmación explícita" in gateway.request.system_instruction
    assert "integraciones pendientes" in gateway.request.system_instruction
    assert "correo, tickets, Internet" in gateway.request.system_instruction
    assert "modelo gemini-3.7-flash, proveedor Vertex AI" in gateway.request.system_instruction
    assert "No existe fallback conversacional" in gateway.request.system_instruction
    assert '"automatic_model_fallback": false' in gateway.request.prompt
    assert '"collaborator_model": "gemini-3.7-flash"' in gateway.request.prompt


def test_collaborative_chat_grounds_questions_about_its_runtime_identity() -> None:
    gateway = RecordingGateway(readiness=10)
    service = CollaborativeChatService(gateway, "gemini-3.7-flash")

    service.reply("¿Qué modelo de IA usa el agente colaborativo?", ())

    assert gateway.request is not None
    assert "responde únicamente con estos hechos verificados" in (
        gateway.request.system_instruction
    )
    prompt = gateway.request.prompt
    assert '"provider": "Vertex AI"' in prompt
    assert '"when_vertex_is_unavailable"' in prompt
    assert '"internet_access": false' in prompt


def test_collaborative_chat_reports_connection_state_from_authoritative_registry() -> None:
    gateway = RecordingGateway(readiness=10)
    now = datetime.now(UTC)
    identity = IdentityContext(
        user_id="javier",
        workspace_id="personal_javier",
        email="javier@example.com",
        authenticated=True,
        mode="identity_platform",
    )
    records = tuple(
        ConnectionRecord(
            id=f"conn_{index}",
            owner_id=identity.user_id,
            workspace_id=identity.workspace_id,
            plugin_id=plugin_id,
            title=title,
            provider=provider,
            status=status,
            account_label="javier@example.com" if status == "connected" else None,
            message="Estado de prueba",
            updated_at=now,
        )
        for index, (plugin_id, title, provider, status) in enumerate(
            (
                ("google.drive", "Google Drive", "Google", "connected"),
                ("google.gmail", "Gmail", "Google", "connected"),
                ("google.calendar", "Google Calendar", "Google", "connected"),
                ("github", "GitHub", "GitHub", "setup_required"),
            )
        )
    )
    service = CollaborativeChatService(
        gateway,
        "gemini-3.7-flash",
        connections=StaticConnections(records),  # type: ignore[arg-type]
    )

    result = service.reply(
        "Me refiero a Google Drive, Gmail, Google Calendar y GitHub.",
        (),
        identity=identity,
    )

    assert "**3 de 4**" in result.reply
    assert "**Gmail:** Conectado y activo" in result.reply
    assert "**Google Calendar:** Conectado y activo" in result.reply
    assert "**GitHub:** Configuración requerida" in result.reply
    assert gateway.request is not None
    assert '"google.gmail"' in gateway.request.prompt
    assert '"status": "connected"' in gateway.request.prompt


def test_collaborative_chat_counts_github_repositories_instead_of_reporting_connection_status() -> None:
    gateway = RecordingGateway(readiness=10)
    github = RecordingGitHub()
    identity = IdentityContext(
        user_id="javier",
        workspace_id="personal_javier",
        authenticated=True,
        mode="identity_platform",
    )
    service = CollaborativeChatService(
        gateway,
        "gemini-3.7-flash",
        github=github,  # type: ignore[arg-type]
    )

    result = service.reply(
        "¿Cuántos repositorios tengo en GitHub?",
        (),
        identity=identity,
    )

    assert github.queries == [""]
    assert result.tool_activity[0].capability == "github.repositories"
    assert result.tool_activity[0].status == "completed"
    assert "Estado verificado de tus conexiones" not in result.reply
    assert len(gateway.requests) == 2
    assert '"visible_repository_count": 3' in gateway.requests[-1].prompt


def test_collaborative_chat_waits_for_enough_evidence_before_selecting_framework() -> None:
    gateway = RecordingGateway(readiness=10)
    service = CollaborativeChatService(gateway, "gemini-3.7-flash")

    result = service.reply("Recomiéndame algún agente que podamos crear.", ())

    assert result.agent_draft.readiness == 10
    assert result.agent_draft.recommended_framework is None


def test_collaborative_chat_retries_one_malformed_structured_response() -> None:
    gateway = MalformedOnceGateway()
    service = CollaborativeChatService(gateway, "gemini-3.7-flash")

    result = service.reply("Hola", ())

    assert result.reply.startswith("Entiendo")
    assert len(gateway.requests) == 2
    assert gateway.requests[-1].purpose == "collaborative_chat_json_repair"
    assert "REINTENTO DE CONTRATO" in gateway.requests[-1].system_instruction


def test_short_confirmation_continues_previous_opportunity_research() -> None:
    gateway = RecordingGateway()
    github = RecordingGitHub()
    drive = RecordingDrive()
    web = RecordingWebResearcher()
    identity = IdentityContext(
        user_id="javier",
        workspace_id="personal_javier",
        authenticated=True,
        mode="identity_platform",
    )
    service = CollaborativeChatService(
        gateway,
        "gemini-3.7-flash",
        github=github,  # type: ignore[arg-type]
        google_drive=drive,  # type: ignore[arg-type]
        web_researcher=web,
    )
    history = (
        ChatTurn(role="user", content="Recomiéndame qué proyecto construir."),
        ChatTurn(role="assistant", content="Puedo contrastar GitHub, Drive y tendencias."),
    )

    result = service.reply("procede", history, identity=identity)

    assert github.queries == [""]
    assert drive.queries == ["proyecto"]
    assert len(web.searches) == 1
    assert result.tool_activity


def test_project_opportunity_radar_contrasts_github_drive_and_verified_web() -> None:
    gateway = RecordingGateway(readiness=10)
    github = RecordingGitHub()
    drive = RecordingDrive()
    web = RecordingWebResearcher()
    identity = IdentityContext(
        user_id="javier",
        workspace_id="personal_javier",
        authenticated=True,
        mode="identity_platform",
    )
    service = CollaborativeChatService(
        gateway,
        "gemini-3.7-flash",
        github=github,  # type: ignore[arg-type]
        google_drive=drive,  # type: ignore[arg-type]
        web_researcher=web,
    )

    result = service.reply(
        "Revisa mi GitHub, Drive y tendencias actuales para decirme qué proyecto construir.",
        (),
        identity=identity,
    )

    assert github.queries == [""]
    assert drive.queries == ["proyecto"]
    assert drive.read_ids == ["doc-1"]
    assert len(web.searches) == 1
    assert str(date.today().year) in web.searches[0]
    assert [item.capability for item in result.tool_activity] == [
        "github.repositories",
        "drive.search",
        "drive.read",
        "web.search",
    ]
    assert len(gateway.requests) == 5
    assert "accumulated_research" in gateway.requests[-1].prompt
    assert "oportunidad recomendada" in gateway.requests[0].system_instruction


def test_collaborative_chat_fails_closed_when_gemini_is_not_connected() -> None:
    service = CollaborativeChatService(None, "gemini-3.7-flash")

    with pytest.raises(DomainError) as captured:
        service.reply("Hola", ())

    assert captured.value.code == "GEMINI_CHAT_UNAVAILABLE"
    assert "Gemini 3.7 Flash" in captured.value.message
    assert "3.5" not in captured.value.message


def test_collaborative_chat_reads_confined_workspace_then_asks_gemini_again(
    tmp_path: Path,
) -> None:
    (tmp_path / "README.md").write_text("# Proyecto seguro", encoding="utf-8")
    gateway = RecordingGateway(workspace_path="README.md")
    service = CollaborativeChatService(
        gateway,
        "gemini-3.7-flash",
        workspace_reader=WorkspaceReader(tmp_path),
    )

    result = service.reply("Lee README.md", ())

    assert len(gateway.requests) == 2
    assert gateway.requests[1].purpose == "collaborative_chat_workspace"
    assert "Proyecto seguro" in gateway.requests[1].prompt
    assert result.tool_activity[0].status == "completed"
    assert result.tool_activity[0].kind == "file"
    assert result.tool_activity[0].path == "README.md"


def test_collaborative_chat_reports_blocked_workspace_escape(tmp_path: Path) -> None:
    gateway = RecordingGateway(workspace_path="../secrets.txt")
    service = CollaborativeChatService(
        gateway,
        "gemini-3.7-flash",
        workspace_reader=WorkspaceReader(tmp_path),
    )

    result = service.reply("Lee el archivo de afuera", ())

    assert len(gateway.requests) == 2
    assert '"status": "blocked"' in gateway.requests[1].prompt
    assert result.tool_activity[0].status == "blocked"


def test_collaborative_chat_can_search_then_open_a_relevant_file(tmp_path: Path) -> None:
    capability_dir = tmp_path / "studio" / "capabilities"
    capability_dir.mkdir(parents=True)
    (capability_dir / "workspace.py").write_text(
        "class WorkspaceReader:\n    pass\n", encoding="utf-8"
    )
    gateway = ResearchGateway()
    service = CollaborativeChatService(
        gateway,
        "gemini-3.7-flash",
        workspace_reader=WorkspaceReader(tmp_path),
    )

    result = service.reply("Investiga cómo funciona WorkspaceReader", ())

    assert len(gateway.requests) == 3
    assert [activity.capability for activity in result.tool_activity] == [
        "workspace.search",
        "workspace.read",
    ]
    assert all(activity.status == "completed" for activity in result.tool_activity)


def test_collaborative_chat_forces_direct_open_for_explicit_url() -> None:
    gateway = RecordingGateway()
    web = RecordingWebResearcher()
    service = CollaborativeChatService(
        gateway,
        "gemini-3.7-flash",
        web_researcher=web,
    )

    result = service.reply("Revisa https://example.com/reto", ())

    assert web.urls == ["https://example.com/reto"]
    assert result.tool_activity[0].capability == "web.open"
    assert result.tool_activity[0].kind == "web_page"
    assert len(gateway.requests) == 2


def test_collaborative_chat_forces_web_search_for_recent_information() -> None:
    gateway = RecordingGateway()
    web = RecordingWebResearcher()
    service = CollaborativeChatService(
        gateway,
        "gemini-3.7-flash",
        web_researcher=web,
    )

    result = service.reply("Busca hackathons recientes para agentes", ())

    assert web.searches == ["Busca hackathons recientes para agentes"]
    assert result.tool_activity[0].capability == "web.search"
    assert len(gateway.requests) == 2


def test_collaborative_chat_searches_for_explicit_url_when_direct_open_is_unavailable() -> None:
    gateway = RecordingGateway()
    web = FallbackWebResearcher()
    service = CollaborativeChatService(
        gateway,
        "gemini-3.7-flash",
        web_researcher=web,
    )
    url = "https://allthingsagentichackathon.devpost.com/"

    result = service.reply(f"Revisa {url}", ())

    assert web.urls == [url]
    assert len(web.searches) == 1
    assert "allthingsagentichackathon" in web.searches[0]
    assert str(date.today().year) in web.searches[0]
    assert result.tool_activity[0].capability == "web.search"
    assert result.tool_activity[0].status == "completed"


def test_collaborative_chat_passes_verified_prior_tool_evidence_to_gemini() -> None:
    gateway = RecordingGateway()
    service = CollaborativeChatService(gateway, "gemini-3.7-flash")

    service.reply(
        "¿Por qué no apareció ese evento?",
        (
            ChatTurn(
                role="assistant",
                content="No lo encontré.",
                evidence=("web.search | completed | Internet | hackathons recientes 2026",),
            ),
        ),
    )

    assert gateway.request is not None
    assert "hackathons recientes 2026" in gateway.request.prompt
    assert date.today().isoformat() in gateway.request.prompt
