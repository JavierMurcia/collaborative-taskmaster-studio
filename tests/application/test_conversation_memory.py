from __future__ import annotations

from datetime import UTC, datetime

from fastapi.testclient import TestClient

from app.main import create_app
from infrastructure.local.clock import FrozenClock
from infrastructure.local.conversation_memory import JsonConversationMemoryRepository
from infrastructure.local.repositories import InMemoryRepository
from studio.application.conversation_memory import ConversationMemoryService

NOW = datetime(2026, 8, 21, 15, 0, tzinfo=UTC)


def test_local_memory_survives_restart_and_isolates_sessions(tmp_path) -> None:
    first = ConversationMemoryService(JsonConversationMemoryRepository(tmp_path), FrozenClock(NOW))
    first.save(
        "browser_alpha",
        conversation_id="chat_demo-1",
        title="Diseñar agente",
        messages=[{"role": "user", "content": "Necesito un agente"}],
        phase="discovery",
    )

    restarted = ConversationMemoryService(JsonConversationMemoryRepository(tmp_path), FrozenClock(NOW))
    assert [item.id for item in restarted.list("browser_alpha")] == ["chat_demo-1"]
    assert restarted.list("browser_beta") == ()

    restarted.delete("browser_alpha", "chat_demo-1")
    assert restarted.list("browser_alpha") == ()


def test_conversation_api_saves_lists_and_deletes_visible_state() -> None:
    clock = FrozenClock(NOW)
    projects = InMemoryRepository(clock)
    api = TestClient(create_app(projects, projects, clock))
    headers = {"X-Studio-Session": "browser_memory_demo"}
    body = {
        "title": "Contrato SaaS",
        "phase": "runtime",
        "agent_id": "catalog_1234567890abcdef",
        "messages": [
            {"role": "user", "content": "Diseña un contrato SaaS"},
            {
                "role": "assistant",
                "content": "Aclaremos el alcance.",
                "model": "gemini-3.7-flash",
                "agentDraft": {"readiness": 20},
            },
        ],
    }

    saved = api.put(
        "/api/v1/collaborative/conversations/chat_contract-1",
        headers=headers,
        json=body,
    )
    assert saved.status_code == 200
    assert saved.json()["messages"][1]["agentDraft"] == {"readiness": 20}
    assert saved.json()["phase"] == "runtime"
    assert saved.json()["agent_id"] == "catalog_1234567890abcdef"

    listed = api.get("/api/v1/collaborative/conversations", headers=headers)
    assert listed.status_code == 200
    assert [item["id"] for item in listed.json()["conversations"]] == ["chat_contract-1"]
    assert api.get(
        "/api/v1/collaborative/conversations",
        headers={"X-Studio-Session": "browser_someone_else"},
    ).json() == {"conversations": []}

    deleted = api.delete(
        "/api/v1/collaborative/conversations/chat_contract-1",
        headers=headers,
    )
    assert deleted.status_code == 204
    assert api.get("/api/v1/collaborative/conversations", headers=headers).json() == {
        "conversations": []
    }
