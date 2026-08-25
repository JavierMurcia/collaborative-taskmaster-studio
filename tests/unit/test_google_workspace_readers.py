from __future__ import annotations

import base64
from urllib.parse import parse_qs, urlsplit

from studio.capabilities.google_calendar import GoogleCalendarReader
from studio.capabilities.google_gmail import GoogleGmailReader
from studio.security import IdentityContext


class FakeConnections:
    def access_token(self, identity: IdentityContext, plugin_id: str) -> str:
        assert identity.user_id == "javier"
        assert plugin_id in {"google.gmail", "google.calendar"}
        return "token"


def identity() -> IdentityContext:
    return IdentityContext(
        user_id="javier",
        workspace_id="personal_javier",
        authenticated=True,
        mode="identity_platform",
    )


class RecordingGmailReader(GoogleGmailReader):
    def __init__(self, responses: list[dict[str, object]]) -> None:
        super().__init__(FakeConnections())  # type: ignore[arg-type]
        self.responses = responses
        self.urls: list[str] = []

    def _json_request(self, url: str, token: str) -> dict[str, object]:
        assert token == "token"
        self.urls.append(url)
        return self.responses.pop(0)


class RecordingCalendarReader(GoogleCalendarReader):
    def __init__(self, response: dict[str, object]) -> None:
        super().__init__(FakeConnections())  # type: ignore[arg-type]
        self.response = response
        self.url = ""

    def _json_request(self, url: str, token: str) -> dict[str, object]:
        assert token == "token"
        self.url = url
        return self.response


def test_gmail_search_returns_compact_message_metadata() -> None:
    reader = RecordingGmailReader(
        [
            {"messages": [{"id": "18f123456789abcd"}], "resultSizeEstimate": 1},
            {
                "id": "18f123456789abcd",
                "threadId": "thread-1",
                "snippet": "Estado del proyecto",
                "payload": {
                    "headers": [
                        {"name": "From", "value": "ana@example.com"},
                        {"name": "Subject", "value": "Informe semanal"},
                        {"name": "Date", "value": "Mon, 24 Aug 2026 09:00:00 -0500"},
                    ]
                },
            },
        ]
    )

    result = reader.search(identity(), "from:ana newer_than:7d")

    assert parse_qs(urlsplit(reader.urls[0]).query)["q"] == ["from:ana newer_than:7d"]
    assert result["read_only"] is True
    assert result["messages"][0]["subject"] == "Informe semanal"  # type: ignore[index]


def test_gmail_read_decodes_plain_text_body() -> None:
    body = base64.urlsafe_b64encode(b"Contenido privado autorizado").decode().rstrip("=")
    reader = RecordingGmailReader(
        [
            {
                "id": "18f123456789abcd",
                "snippet": "Contenido privado",
                "payload": {
                    "headers": [{"name": "Subject", "value": "Contrato"}],
                    "mimeType": "text/plain",
                    "body": {"data": body},
                },
            }
        ]
    )

    result = reader.read(identity(), "18f123456789abcd")

    assert result["content"] == "Contenido privado autorizado"
    assert result["read_only"] is True


def test_calendar_lists_only_a_bounded_upcoming_window() -> None:
    reader = RecordingCalendarReader(
        {
            "timeZone": "America/Bogota",
            "items": [
                {
                    "id": "event-1",
                    "summary": "Revisión del agente",
                    "start": {"dateTime": "2026-08-25T10:00:00-05:00"},
                    "end": {"dateTime": "2026-08-25T11:00:00-05:00"},
                    "htmlLink": "https://calendar.google.com/event?eid=event-1",
                }
            ],
        }
    )

    result = reader.list_events(identity(), "agente", days=14, limit=5)

    query = parse_qs(urlsplit(reader.url).query)
    assert query["q"] == ["agente"]
    assert query["singleEvents"] == ["true"]
    assert query["orderBy"] == ["startTime"]
    assert result["events"][0]["title"] == "Revisión del agente"  # type: ignore[index]
    assert result["read_only"] is True
