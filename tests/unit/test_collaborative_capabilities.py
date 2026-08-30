from __future__ import annotations

import io
import zipfile
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

from fastapi.testclient import TestClient

from app.main import create_app
from infrastructure.local.clock import FrozenClock
from infrastructure.local.conversation_memory import InMemoryConversationMemoryRepository
from infrastructure.local.repositories import InMemoryRepository
from studio.application.conversation_memory import ConversationMemoryService
from studio.capabilities.documents import DocumentLibrary
from studio.capabilities.web import VertexWebResearcher
from studio.capabilities.workspace import WorkspaceReader

NOW = datetime(2026, 8, 21, 16, 0, tzinfo=UTC)


def test_document_library_extracts_text_and_isolates_sessions(tmp_path: Path) -> None:
    library = DocumentLibrary(tmp_path)
    record = library.add("browser_alpha", "requisitos.md", b"# Mision\nCrear un informe seguro")

    assert library.inspect("browser_alpha", record.id)["content"].startswith("# Mision")
    assert library.search("browser_alpha", record.id, "informe")["matches"][0]["line"] == 2
    assert library.list("browser_beta") == ()


def test_document_library_extracts_docx_without_executing_it(tmp_path: Path) -> None:
    payload = io.BytesIO()
    with zipfile.ZipFile(payload, "w") as archive:
        archive.writestr(
            "word/document.xml",
            '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>Contrato seguro</w:t></w:r></w:p></w:body></w:document>',
        )
    record = DocumentLibrary(tmp_path).add("browser_alpha", "contrato.docx", payload.getvalue())
    assert record.text == "Contrato seguro"


def test_document_library_preserves_validated_images_for_multimodal_chat(
    tmp_path: Path,
) -> None:
    payload = b"\x89PNG\r\n\x1a\n" + b"safe-image-bytes"
    library = DocumentLibrary(tmp_path)

    record = library.add("browser_alpha", "captura.png", payload)
    inspected = library.inspect("browser_alpha", record.id)

    assert inspected["media"]["mime_type"] == "image/png"
    assert library.media("browser_alpha", (record.id,))[0].mime_type == "image/png"
    assert library.list("browser_alpha")[0]["media_type"] == "image/png"


def test_document_upload_api_lists_and_deletes(tmp_path: Path) -> None:
    clock = FrozenClock(NOW)
    projects = InMemoryRepository(clock)
    api = TestClient(
        create_app(
            projects,
            projects,
            clock,
            document_library=DocumentLibrary(tmp_path),
        )
    )
    headers = {"X-Studio-Session": "browser_documents"}
    uploaded = api.post(
        "/api/v1/collaborative/documents",
        headers=headers,
        files={"file": ("notas.txt", b"contenido verificable", "text/plain")},
    )
    assert uploaded.status_code == 201
    document_id = uploaded.json()["id"]
    assert api.get("/api/v1/collaborative/documents", headers=headers).json()["documents"][0]["id"] == document_id
    inspected = api.get(f"/api/v1/collaborative/documents/{document_id}", headers=headers)
    assert inspected.status_code == 200
    assert inspected.json()["content"] == "contenido verificable"
    assert api.delete(f"/api/v1/collaborative/documents/{document_id}", headers=headers).status_code == 204


def test_multiple_dataset_files_can_be_uploaded_to_one_session(tmp_path: Path) -> None:
    clock = FrozenClock(NOW)
    projects = InMemoryRepository(clock)
    api = TestClient(
        create_app(
            projects,
            projects,
            clock,
            document_library=DocumentLibrary(tmp_path),
        )
    )
    headers = {"X-Studio-Session": "browser_multiple_datasets"}
    document_ids: list[str] = []
    for name, content in (
        ("ventas.csv", b"mes,ventas\nEnero,10\nFebrero,20\n"),
        ("costos.csv", b"area,costo\nProducto,8\nSoporte,4\n"),
    ):
        response = api.post(
            "/api/v1/collaborative/documents",
            headers=headers,
            files={"file": (name, content, "text/csv")},
        )
        assert response.status_code == 201
        document_ids.append(response.json()["id"])

    listed = api.get("/api/v1/collaborative/documents", headers=headers)

    assert listed.status_code == 200
    assert {item["id"] for item in listed.json()["documents"]} == set(document_ids)


def test_workspace_project_map_and_relations_are_bounded(tmp_path: Path) -> None:
    (tmp_path / "studio").mkdir()
    (tmp_path / "tests").mkdir()
    (tmp_path / "pyproject.toml").write_text("[project]\nname='demo'", encoding="utf-8")
    (tmp_path / "studio" / "service.py").write_text("import json\nfrom pathlib import Path\n", encoding="utf-8")
    (tmp_path / "tests" / "test_service.py").write_text("from studio import service\n", encoding="utf-8")
    reader = WorkspaceReader(tmp_path)

    project_map = reader.map_project()
    relations = reader.related("studio/service.py")

    assert project_map["total_files"] == 3
    assert project_map["total_lines"] == 5
    assert project_map["line_count_files"] == 3
    assert "pyproject.toml" in project_map["manifests"]
    assert relations["imports"] == ["json", "pathlib"]
    assert "tests/test_service.py" in relations["referenced_by"]


def test_memory_recall_returns_relevant_visible_excerpt() -> None:
    memory = ConversationMemoryService(InMemoryConversationMemoryRepository(), FrozenClock(NOW))
    memory.save(
        "browser_alpha",
        conversation_id="chat_previous",
        title="Contrato SaaS",
        messages=[{"role": "user", "content": "La propiedad intelectual pertenece a la empresa."}],
        phase="alignment",
    )
    recalled = memory.recall("browser_alpha", "¿Quién conserva la propiedad intelectual?")
    assert recalled[0]["title"] == "Contrato SaaS"
    assert "empresa" in recalled[0]["excerpt"]


def test_vertex_web_researcher_returns_grounded_sources() -> None:
    web = SimpleNamespace(uri="https://example.com/fuente", title="Fuente oficial")
    response = SimpleNamespace(
        text="Resumen respaldado.",
        candidates=[SimpleNamespace(grounding_metadata=SimpleNamespace(grounding_chunks=[SimpleNamespace(web=web)]))],
    )

    class Models:
        def generate_content(self, **_: object) -> object:
            return response

    result = VertexWebResearcher(SimpleNamespace(models=Models()), "gemini-3.7-flash").search("norma vigente")
    assert result["grounded"] is True
    assert result["sources"] == [{"title": "Fuente oficial", "url": "https://example.com/fuente"}]


def test_vertex_web_researcher_opens_a_verified_public_url() -> None:
    metadata = SimpleNamespace(
        url_metadata=[
            SimpleNamespace(
                retrieved_url="https://example.com/reto",
                url_retrieval_status="URL_RETRIEVAL_STATUS_SUCCESS",
            )
        ]
    )
    response = SimpleNamespace(
        text="Contenido verificado.",
        candidates=[SimpleNamespace(url_context_metadata=metadata)],
    )

    class Models:
        def generate_content(self, **_: object) -> object:
            return response

    result = VertexWebResearcher(SimpleNamespace(models=Models()), "gemini-3.7-flash").open_url(
        "https://example.com/reto"
    )
    assert result["kind"] == "web_page"
    assert result["grounded"] is True
    assert result["sources"][0]["url"] == "https://example.com/reto"


def test_vertex_web_researcher_blocks_private_urls() -> None:
    researcher = VertexWebResearcher(SimpleNamespace(), "gemini-3.7-flash")
    try:
        researcher.open_url("http://127.0.0.1/private")
    except Exception as error:
        assert getattr(error, "code", None) == "WEB_URL_BLOCKED"
    else:
        raise AssertionError("Private URL should be blocked")
