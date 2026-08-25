"""Read-only Gmail capability backed by a user-scoped OAuth connection."""

from __future__ import annotations

import base64
import json
import re
from html import unescape
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

from studio.application.connection_service import ConnectionService
from studio.domain.errors import DomainError
from studio.security import IdentityContext

_MESSAGES_ENDPOINT = "https://gmail.googleapis.com/gmail/v1/users/me/messages"
_MESSAGE_ID = re.compile(r"[A-Za-z0-9_-]{8,200}")
_MAX_RESPONSE_BYTES = 2_000_000
_MAX_CONTENT_CHARACTERS = 24_000


class GoogleGmailReader:
    """Search and read email without send, modify, archive, or delete authority."""

    def __init__(self, connections: ConnectionService) -> None:
        self._connections = connections

    def available(self, identity: IdentityContext) -> bool:
        return self._connections.connected(identity, "google.gmail")

    def search(
        self, identity: IdentityContext, query: str = "", *, limit: int = 10
    ) -> dict[str, object]:
        token = self._connections.access_token(identity, "google.gmail")
        parameters: dict[str, object] = {"maxResults": max(1, min(limit, 20))}
        if query.strip():
            parameters["q"] = query.strip()
        listing = self._json_request(f"{_MESSAGES_ENDPOINT}?{urlencode(parameters)}", token)
        message_refs = listing.get("messages", [])
        messages: list[dict[str, object]] = []
        if isinstance(message_refs, list):
            for item in message_refs[: max(1, min(limit, 20))]:
                message_id = str(item.get("id") or "") if isinstance(item, dict) else ""
                if not _MESSAGE_ID.fullmatch(message_id):
                    continue
                metadata = self._json_request(
                    f"{_MESSAGES_ENDPOINT}/{quote(message_id, safe='')}?"
                    + urlencode(
                        {
                            "format": "metadata",
                            "metadataHeaders": ["From", "To", "Subject", "Date"],
                        },
                        doseq=True,
                    ),
                    token,
                )
                messages.append(_message_summary(metadata))
        return {
            "kind": "google_gmail_search",
            "query": query,
            "messages": messages,
            "result_size_estimate": int(listing.get("resultSizeEstimate") or len(messages)),
            "read_only": True,
        }

    def read(self, identity: IdentityContext, message_id: str) -> dict[str, object]:
        normalized = message_id.strip().strip("\"'`“”‘’.,;:()[]{}")
        if not _MESSAGE_ID.fullmatch(normalized):
            raise DomainError("GMAIL_MESSAGE_INVALID", "El identificador del correo no es válido.")
        token = self._connections.access_token(identity, "google.gmail")
        payload = self._json_request(
            f"{_MESSAGES_ENDPOINT}/{quote(normalized, safe='')}?"
            + urlencode({"format": "full"}),
            token,
        )
        summary = _message_summary(payload)
        content = _message_text(payload.get("payload"))
        return {
            "kind": "google_gmail_message",
            "message": summary,
            "content": content[:_MAX_CONTENT_CHARACTERS],
            "content_truncated": len(content) > _MAX_CONTENT_CHARACTERS,
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
                    "GMAIL_ACCESS_DENIED", "Gmail rechazó la credencial o el permiso solicitado."
                ) from error
            if error.code == 404:
                raise DomainError("GMAIL_MESSAGE_NOT_FOUND", "El correo solicitado ya no está disponible.") from error
            raise DomainError("GMAIL_REQUEST_FAILED", "No se pudo consultar Gmail.") from error
        except (URLError, TimeoutError) as error:
            raise DomainError("GMAIL_UNAVAILABLE", "Gmail no respondió a tiempo.") from error
        if len(raw) > _MAX_RESPONSE_BYTES:
            raise DomainError("GMAIL_RESPONSE_TOO_LARGE", "La respuesta de Gmail supera el límite seguro.")
        try:
            decoded = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise DomainError("GMAIL_RESPONSE_INVALID", "Gmail devolvió una respuesta no válida.") from error
        if not isinstance(decoded, dict):
            raise DomainError("GMAIL_RESPONSE_INVALID", "Gmail devolvió una respuesta no válida.")
        return decoded


def _message_summary(payload: dict[str, object]) -> dict[str, object]:
    body = payload.get("payload")
    headers = body.get("headers", []) if isinstance(body, dict) else []
    normalized_headers = {
        str(item.get("name", "")).casefold(): str(item.get("value", ""))
        for item in headers
        if isinstance(item, dict)
    }
    return {
        "id": str(payload.get("id") or ""),
        "thread_id": str(payload.get("threadId") or ""),
        "from": normalized_headers.get("from", ""),
        "to": normalized_headers.get("to", ""),
        "subject": normalized_headers.get("subject", "(sin asunto)"),
        "date": normalized_headers.get("date", ""),
        "snippet": str(payload.get("snippet") or ""),
    }


def _message_text(payload: object) -> str:
    if not isinstance(payload, dict):
        return ""
    parts = payload.get("parts")
    candidates = parts if isinstance(parts, list) else [payload]
    plain: list[str] = []
    html: list[str] = []
    for item in candidates:
        if not isinstance(item, dict):
            continue
        nested = item.get("parts")
        if isinstance(nested, list):
            nested_text = _message_text({"parts": nested})
            if nested_text:
                plain.append(nested_text)
        body = item.get("body")
        encoded = str(body.get("data") or "") if isinstance(body, dict) else ""
        if not encoded:
            continue
        try:
            decoded = base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4)).decode(
                "utf-8", errors="replace"
            )
        except (ValueError, TypeError):
            continue
        if str(item.get("mimeType") or "").casefold() == "text/plain":
            plain.append(decoded)
        elif str(item.get("mimeType") or "").casefold() == "text/html":
            html.append(_html_to_text(decoded))
    return "\n\n".join(value.strip() for value in (plain or html) if value.strip())


def _html_to_text(value: str) -> str:
    without_scripts = re.sub(r"(?is)<(script|style).*?>.*?</\1>", " ", value)
    with_breaks = re.sub(r"(?i)<br\s*/?>|</p>|</div>|</li>", "\n", without_scripts)
    return re.sub(r"[ \t]+", " ", unescape(re.sub(r"(?s)<[^>]+>", " ", with_breaks))).strip()
