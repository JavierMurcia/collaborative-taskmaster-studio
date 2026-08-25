"""Read-only Google Calendar capability backed by user OAuth."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from studio.application.connection_service import ConnectionService
from studio.domain.errors import DomainError
from studio.security import IdentityContext

_EVENTS_ENDPOINT = "https://www.googleapis.com/calendar/v3/calendars/primary/events"
_MAX_RESPONSE_BYTES = 2_000_000


class GoogleCalendarReader:
    """List calendar events without create, update, or delete authority."""

    def __init__(self, connections: ConnectionService) -> None:
        self._connections = connections

    def available(self, identity: IdentityContext) -> bool:
        return self._connections.connected(identity, "google.calendar")

    def list_events(
        self,
        identity: IdentityContext,
        query: str = "",
        *,
        days: int = 30,
        limit: int = 15,
    ) -> dict[str, object]:
        token = self._connections.access_token(identity, "google.calendar")
        now = datetime.now(UTC)
        parameters: dict[str, object] = {
            "timeMin": now.isoformat().replace("+00:00", "Z"),
            "timeMax": (now + timedelta(days=max(1, min(days, 366))))
            .isoformat()
            .replace("+00:00", "Z"),
            "singleEvents": "true",
            "orderBy": "startTime",
            "maxResults": max(1, min(limit, 25)),
        }
        if query.strip():
            parameters["q"] = query.strip()
        payload = self._json_request(f"{_EVENTS_ENDPOINT}?{urlencode(parameters)}", token)
        raw_items = payload.get("items", [])
        events = [_event_summary(item) for item in raw_items if isinstance(item, dict)] if isinstance(raw_items, list) else []
        return {
            "kind": "google_calendar_events",
            "query": query,
            "window_start": parameters["timeMin"],
            "window_end": parameters["timeMax"],
            "time_zone": str(payload.get("timeZone") or ""),
            "events": events,
            "read_only": True,
        }

    def _json_request(self, url: str, token: str) -> dict[str, Any]:
        request = Request(
            url,
            headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
        )
        try:
            with urlopen(request, timeout=15) as response:  # noqa: S310 - fixed Google endpoint
                raw = response.read(_MAX_RESPONSE_BYTES + 1)
        except HTTPError as error:
            if error.code in {401, 403}:
                raise DomainError(
                    "CALENDAR_ACCESS_DENIED",
                    "Google Calendar rechazó la credencial o el permiso solicitado.",
                ) from error
            raise DomainError("CALENDAR_REQUEST_FAILED", "No se pudo consultar Google Calendar.") from error
        except (URLError, TimeoutError) as error:
            raise DomainError("CALENDAR_UNAVAILABLE", "Google Calendar no respondió a tiempo.") from error
        if len(raw) > _MAX_RESPONSE_BYTES:
            raise DomainError("CALENDAR_RESPONSE_TOO_LARGE", "La agenda supera el límite seguro.")
        try:
            decoded = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise DomainError("CALENDAR_RESPONSE_INVALID", "Calendar devolvió una respuesta no válida.") from error
        if not isinstance(decoded, dict):
            raise DomainError("CALENDAR_RESPONSE_INVALID", "Calendar devolvió una respuesta no válida.")
        return decoded


def _event_summary(payload: dict[str, object]) -> dict[str, object]:
    start = payload.get("start") if isinstance(payload.get("start"), dict) else {}
    end = payload.get("end") if isinstance(payload.get("end"), dict) else {}
    organizer = payload.get("organizer") if isinstance(payload.get("organizer"), dict) else {}
    return {
        "id": str(payload.get("id") or ""),
        "title": str(payload.get("summary") or "(sin título)"),
        "start": str(start.get("dateTime") or start.get("date") or ""),
        "end": str(end.get("dateTime") or end.get("date") or ""),
        "status": str(payload.get("status") or ""),
        "location": str(payload.get("location") or ""),
        "organizer": str(organizer.get("email") or ""),
        "html_link": str(payload.get("htmlLink") or ""),
    }
