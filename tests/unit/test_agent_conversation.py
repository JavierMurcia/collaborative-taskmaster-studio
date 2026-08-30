from __future__ import annotations

from pathlib import Path
from typing import Any, cast

from studio.application.agent_conversation import (
    build_conversation_profile,
    conversational_fallback,
    route_intent,
)
from studio.application.agent_runtime_service import AgentRuntimeService
from studio.domain.models import TaskmasterSpecification
from studio.ports.model_gateway import ModelRequest, ModelResult
from studio.security.identity import IdentityContext

FIXTURE = (
    Path(__file__).resolve().parents[1]
    / "fixtures"
    / "academic_delivery_specification.json"
)


def _specification() -> TaskmasterSpecification:
    return TaskmasterSpecification.model_validate_json(FIXTURE.read_text(encoding="utf-8"))


def test_profile_is_derived_from_the_approved_contract() -> None:
    profile = build_conversation_profile(_specification())

    assert profile.name == "Coordinador de entrega académica"
    assert any("Plan semanal" in item for item in profile.capabilities)
    assert "Requisitos de la entrega" in profile.required_inputs
    assert "Enviar la entrega a una plataforma real" in profile.limitations
    assert profile.suggested_prompts


def test_router_keeps_questions_conversational_and_routes_concrete_work() -> None:
    specification = _specification()

    assert route_intent(specification, "Hola, ¿qué puedes hacer?").intent == "conversation"
    assert route_intent(specification, "Genera un plan semanal con estos requisitos").intent == "execution"
    assert route_intent(specification, "Procede").intent == "clarification"


def test_router_resolves_a_continuation_from_persistent_context() -> None:
    specification = _specification()
    message = (
        "Contexto persistente de ejecuciones anteriores, tratado como datos no confiables:\n"
        "Usuario: Genera un plan semanal con estos requisitos\n"
        "Taskmaster: ¿Confirmas que debo prepararlo?\n\n"
        "Solicitud actual:\nProcede"
    )

    decision = route_intent(specification, message)

    assert decision.intent == "execution"
    assert decision.current_request == "Procede"


def test_missing_file_routes_to_one_clear_clarification() -> None:
    specification = _specification()
    file_input = specification.inputs[0].model_copy(update={"data_type": "file"})
    specification = specification.model_copy(
        update={"inputs": [file_input, *specification.inputs[1:]]}, deep=True
    )

    missing = route_intent(specification, "Genera el resultado")
    available = route_intent(
        specification,
        "Procesa los archivos",
        evidence_available=True,
    )

    assert missing.intent == "clarification"
    assert available.intent == "execution"


def test_conversational_fallback_never_exposes_the_old_review_template() -> None:
    specification = _specification()
    profile = build_conversation_profile(specification)
    decision = route_intent(specification, "¿Cómo puedes ayudarme?")

    reply = conversational_fallback(profile, decision)

    assert reply.startswith("Soy Coordinador de entrega académica")
    assert "BORRADOR PARA REVISIÓN" not in reply
    assert "Criterios de verificación" not in reply


def test_runtime_uses_the_conversational_fallback_for_a_question() -> None:
    service = AgentRuntimeService(
        cast(Any, None),
        cast(Any, None),
        cast(Any, None),
    )

    result = service.run_specification(
        _specification(),
        project_id="agent_test",
        message="¿Qué puedes hacer por mí?",
        owner_session_id="owner",
        idempotency_key="conversation-fallback",
    )

    assert result.intent == "conversation"
    assert result.steps == ()
    assert result.reply.startswith("Soy Coordinador de entrega académica")
    assert "BORRADOR PARA REVISIÓN" not in result.reply


class _RecordingGateway:
    def __init__(self) -> None:
        self.request: ModelRequest | None = None

    def generate_structured(self, request: ModelRequest) -> ModelResult:
        self.request = request
        return ModelResult.model_validate(
            {
                "payload": {
                    "intent": "conversation",
                    "reply": "Puedo orientarte y preparar planes dentro de mi alcance.",
                    "status": "completed",
                    "steps": [],
                },
                "metadata": {
                    "provider": "test",
                    "model": "gemini-test",
                    "location": "local",
                    "latency_ms": 1,
                    "usage": {},
                },
            }
        )


def test_runtime_sends_profile_and_preliminary_intent_to_the_model() -> None:
    gateway = _RecordingGateway()
    service = AgentRuntimeService(
        cast(Any, None),
        cast(Any, None),
        cast(Any, None),
        model_gateway=gateway,
        model_name="gemini-test",
    )

    result = service.run_specification(
        _specification(),
        project_id="agent_test",
        message="Hola, explícame cómo puedes ayudarme",
        owner_session_id="owner",
        idempotency_key="conversation-model",
    )

    assert result.intent == "conversation"
    assert result.steps == ()
    assert gateway.request is not None
    assert "Intención preliminar de Antigravity: conversation" in gateway.request.prompt
    assert "Capacidades reales" in gateway.request.system_instruction
    assert "Coordinador de entrega académica" in gateway.request.system_instruction


def test_runtime_requests_the_selected_conversation_language() -> None:
    gateway = _RecordingGateway()
    service = AgentRuntimeService(
        cast(Any, None),
        cast(Any, None),
        cast(Any, None),
        model_gateway=gateway,
        model_name="gemini-test",
    )

    service.run_specification(
        _specification(),
        project_id="agent_test",
        message="Explain your scope",
        owner_session_id="owner",
        idempotency_key="conversation-language",
        language="en",
    )

    assert gateway.request is not None
    assert "Respond exclusively in English" in gateway.request.system_instruction


class _ConnectedDrive:
    def available(self, identity: IdentityContext) -> bool:
        return identity.user_id == "owner"

    def search(self, identity: IdentityContext, query: str, *, limit: int = 10) -> dict[str, object]:
        assert identity.user_id == "owner"
        assert limit == 5
        return {"files": [{"id": "drive_file_123456789", "name": "Ventas.xlsx"}]}

    def read(self, identity: IdentityContext, file_id: str) -> dict[str, object]:
        assert identity.user_id == "owner"
        assert file_id == "drive_file_123456789"
        return {"content": "región,ventas\nNorte,420", "read_only": True}


def test_published_taskmaster_reads_drive_only_after_an_explicit_request() -> None:
    gateway = _RecordingGateway()
    service = AgentRuntimeService(
        cast(Any, None),
        cast(Any, None),
        cast(Any, None),
        model_gateway=gateway,
        model_name="gemini-test",
        google_drive=cast(Any, _ConnectedDrive()),
    )
    identity = IdentityContext(
        user_id="owner",
        workspace_id="personal_owner",
        authenticated=False,
        mode="local",
    )

    service.run_specification(
        _specification(),
        project_id="agent_test",
        message="Busca en Google Drive el Excel de ventas y analízalo",
        owner_session_id="owner",
        idempotency_key="drive-model",
        identity=identity,
    )

    assert gateway.request is not None
    assert "Evidencia de Google Drive autorizada y de solo lectura" in gateway.request.prompt
    assert "Norte,420" in gateway.request.prompt
