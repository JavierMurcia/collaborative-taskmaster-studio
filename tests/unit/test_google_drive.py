from __future__ import annotations

import io
import zipfile
from urllib.parse import parse_qs, urlsplit

from studio.capabilities.google_drive import GoogleDriveReader, _normalize_file_id
from studio.security import IdentityContext


class FakeConnections:
    def access_token(self, identity: IdentityContext, provider: str) -> str:
        assert identity.user_id == "javier"
        assert provider == "google.drive"
        return "token"


class RecordingDriveReader(GoogleDriveReader):
    def __init__(self, pages: list[dict[str, object]]) -> None:
        super().__init__(FakeConnections())  # type: ignore[arg-type]
        self.pages = pages
        self.urls: list[str] = []

    def _json_request(self, url: str, token: str) -> dict[str, object]:
        assert token == "token"
        self.urls.append(url)
        return self.pages.pop(0)


def identity() -> IdentityContext:
    return IdentityContext(
        user_id="javier",
        workspace_id="personal_javier",
        authenticated=True,
        mode="identity_platform",
    )


def test_list_folders_filters_by_folder_mime_type_and_returns_parents() -> None:
    reader = RecordingDriveReader(
        [
            {
                "files": [
                    {
                        "id": "folder-1",
                        "name": "Taskmaster",
                        "mimeType": "application/vnd.google-apps.folder",
                        "parents": ["root"],
                    }
                ]
            }
        ]
    )

    result = reader.list_folders(identity())

    query = parse_qs(urlsplit(reader.urls[0]).query)
    assert "mimeType = 'application/vnd.google-apps.folder'" in query["q"][0]
    assert "parents" in query["fields"][0]
    assert result["count"] == 1
    assert result["shown"] == 1
    assert result["truncated"] is False
    assert result["folders"][0]["parents"] == ["root"]  # type: ignore[index]


def test_list_folders_follows_drive_pagination_with_a_bounded_limit() -> None:
    reader = RecordingDriveReader(
        [
            {"files": [{"id": "one", "name": "A"}], "nextPageToken": "next"},
            {"files": [{"id": "two", "name": "B"}]},
        ]
    )

    result = reader.list_folders(identity(), limit=2)

    assert result["count"] == 2
    assert parse_qs(urlsplit(reader.urls[1]).query)["pageToken"] == ["next"]


def test_list_folders_counts_all_scanned_items_but_bounds_prompt_payload() -> None:
    reader = RecordingDriveReader(
        [{"files": [{"id": f"folder-{index}", "name": f"Folder {index}"} for index in range(80)]}]
    )

    result = reader.list_folders(identity(), limit=100, display_limit=25)

    assert result["count"] == 80
    assert result["shown"] == 25
    assert result["truncated"] is True
    assert len(result["folders"]) == 25  # type: ignore[arg-type]


def test_drive_read_normalizes_model_punctuation_around_file_id() -> None:
    assert _normalize_file_id('“1AbC_defGhijkLMNopQRstuVwxyz-234”') == "1AbC_defGhijkLMNopQRstuVwxyz-234"
    assert _normalize_file_id('file_id: "1AbC_defGhijkLMNopQRstuVwxyz-234"') == "1AbC_defGhijkLMNopQRstuVwxyz-234"


def test_drive_read_rejects_ambiguous_or_human_file_names() -> None:
    assert _normalize_file_id("PREVENCION DE ENVEJECIMIENTO") == ""
    assert _normalize_file_id("1AbC_defGhijkLMN 2Zyx_wvuTsrqPONM") == ""


def test_search_uses_names_and_indexed_content_across_drives() -> None:
    reader = RecordingDriveReader([{"files": []}])

    result = reader.search(identity(), "Taskmaster")

    query = parse_qs(urlsplit(reader.urls[0]).query)
    assert "name contains 'Taskmaster'" in query["q"][0]
    assert "fullText contains 'Taskmaster'" in query["q"][0]
    assert query["includeItemsFromAllDrives"] == ["true"]
    assert result["search_mode"] == "name_and_indexed_content"


def test_drive_reads_docx_with_safe_text_extraction() -> None:
    payload = io.BytesIO()
    with zipfile.ZipFile(payload, "w") as archive:
        archive.writestr(
            "word/document.xml",
            '<w:document xmlns:w="urn:test"><w:body><w:p><w:r><w:t>Contrato SaaS</w:t></w:r></w:p></w:body></w:document>',
        )

    class BinaryReader(RecordingDriveReader):
        def _bytes_request(self, url: str, token: str) -> bytes:
            assert "alt=media" in url
            return payload.getvalue()

    reader = BinaryReader(
        [{
            "id": "1AbC_defGhijkLMNopQRstuVwxyz-234",
            "name": "contrato.docx",
            "mimeType": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        }]
    )

    result = reader.read(identity(), "1AbC_defGhijkLMNopQRstuVwxyz-234")

    assert result["content"] == "Contrato SaaS"
