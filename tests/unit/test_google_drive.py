from __future__ import annotations

from urllib.parse import parse_qs, urlsplit

from studio.capabilities.google_drive import GoogleDriveReader
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
