"""Read-only Google Drive capability backed by a user-scoped OAuth connection."""

from __future__ import annotations

import json
import re
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

from studio.application.connection_service import ConnectionService
from studio.capabilities.documents import extract_document_text
from studio.domain.errors import DomainError
from studio.security import IdentityContext

_FILES_ENDPOINT = "https://www.googleapis.com/drive/v3/files"
_FOLDER_MIME_TYPE = "application/vnd.google-apps.folder"
_DRIVE_FILE_ID = re.compile(r"[A-Za-z0-9_-]{15,200}")
_GOOGLE_EXPORTS = {
    "application/vnd.google-apps.document": "text/plain",
    "application/vnd.google-apps.spreadsheet": "text/csv",
    "application/vnd.google-apps.presentation": "text/plain",
}
_DIRECT_MIME_PREFIXES = ("text/", "application/json", "application/xml")
_BINARY_DOCUMENTS = {
    "application/pdf": ".pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": ".xlsx",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation": ".pptx",
}
_MAX_CHAT_CHARACTERS = 24_000


class GoogleDriveReader:
    def __init__(self, connections: ConnectionService, *, max_read_bytes: int = 8_388_608) -> None:
        self._connections = connections
        self._max_read_bytes = max(1_000, min(max_read_bytes, 8_388_608))

    def available(self, identity: IdentityContext) -> bool:
        return self._connections.connected(identity, "google.drive")

    def search(self, identity: IdentityContext, query: str, *, limit: int = 10) -> dict[str, object]:
        token = self._connections.access_token(identity, "google.drive")
        safe_query = query.strip().replace("\\", "\\\\").replace("'", "\\'")
        filters = ["trashed = false"]
        if safe_query:
            filters.append(f"(name contains '{safe_query}' or fullText contains '{safe_query}')")
        parameters = urlencode(
            {
                "q": " and ".join(filters),
                "pageSize": max(1, min(limit, 25)),
                "orderBy": "modifiedTime desc",
                "fields": "files(id,name,mimeType,modifiedTime,size,webViewLink,parents)",
                "spaces": "drive",
                "includeItemsFromAllDrives": "true",
                "supportsAllDrives": "true",
            }
        )
        payload = self._json_request(f"{_FILES_ENDPOINT}?{parameters}", token)
        files = payload.get("files", [])
        return {
            "kind": "google_drive_search",
            "query": query,
            "search_mode": "name_and_indexed_content",
            "files": files if isinstance(files, list) else [],
            "read_only": True,
        }

    def list_folders(
        self,
        identity: IdentityContext,
        query: str = "",
        *,
        limit: int = 500,
        display_limit: int = 75,
    ) -> dict[str, object]:
        """List folders across My Drive, including nested folders and parent references."""

        token = self._connections.access_token(identity, "google.drive")
        safe_query = query.strip().replace("\\", "\\\\").replace("'", "\\'")
        filters = ["trashed = false", f"mimeType = '{_FOLDER_MIME_TYPE}'"]
        if safe_query:
            filters.append(f"name contains '{safe_query}'")

        bounded_limit = max(1, min(limit, 1_000))
        bounded_display_limit = max(1, min(display_limit, 100))
        folders: list[object] = []
        total_scanned = 0
        page_token = ""
        while total_scanned < bounded_limit:
            parameters: dict[str, object] = {
                "q": " and ".join(filters),
                "pageSize": min(100, bounded_limit - total_scanned),
                "orderBy": "name_natural",
                "fields": "nextPageToken,files(id,name,modifiedTime,parents)",
                "spaces": "drive",
            }
            if page_token:
                parameters["pageToken"] = page_token
            payload = self._json_request(f"{_FILES_ENDPOINT}?{urlencode(parameters)}", token)
            page = payload.get("files", [])
            if isinstance(page, list):
                accepted = page[: bounded_limit - total_scanned]
                total_scanned += len(accepted)
                remaining_display = bounded_display_limit - len(folders)
                if remaining_display > 0:
                    folders.extend(accepted[:remaining_display])
            page_token = str(payload.get("nextPageToken") or "")
            if not page_token or not page:
                break

        return {
            "kind": "google_drive_folders",
            "query": query,
            "folders": folders,
            "count": total_scanned,
            "shown": len(folders),
            "truncated": total_scanned > len(folders) or bool(page_token),
            "scan_limit_reached": bool(page_token) and total_scanned >= bounded_limit,
            "read_only": True,
        }

    def read(self, identity: IdentityContext, file_id: str) -> dict[str, object]:
        normalized_id = _normalize_file_id(file_id)
        if not normalized_id:
            raise DomainError("DRIVE_FILE_INVALID", "El identificador de Drive no es válido.")
        token = self._connections.access_token(identity, "google.drive")
        metadata = self._json_request(
            f"{_FILES_ENDPOINT}/{quote(normalized_id, safe='')}?"
            + urlencode(
                {
                    "fields": "id,name,mimeType,modifiedTime,size,webViewLink",
                    "supportsAllDrives": "true",
                }
            ),
            token,
        )
        mime_type = str(metadata.get("mimeType") or "")
        if mime_type in _GOOGLE_EXPORTS:
            url = (
                f"{_FILES_ENDPOINT}/{quote(normalized_id, safe='')}/export?"
                + urlencode({"mimeType": _GOOGLE_EXPORTS[mime_type]})
            )
            content = self._bytes_request(url, token).decode("utf-8", errors="replace")
        elif mime_type.startswith(_DIRECT_MIME_PREFIXES):
            url = (
                f"{_FILES_ENDPOINT}/{quote(normalized_id, safe='')}?"
                + urlencode({"alt": "media", "supportsAllDrives": "true"})
            )
            content = self._bytes_request(url, token).decode("utf-8", errors="replace")
        elif mime_type in _BINARY_DOCUMENTS:
            url = (
                f"{_FILES_ENDPOINT}/{quote(normalized_id, safe='')}?"
                + urlencode({"alt": "media", "supportsAllDrives": "true"})
            )
            filename = str(metadata.get("name") or f"documento{_BINARY_DOCUMENTS[mime_type]}")
            if not filename.casefold().endswith(_BINARY_DOCUMENTS[mime_type]):
                filename += _BINARY_DOCUMENTS[mime_type]
            content = extract_document_text(filename, self._bytes_request(url, token))
        else:
            raise DomainError(
                "DRIVE_FILE_TYPE_UNSUPPORTED",
                "Este tipo de archivo no puede leerse como texto dentro del chat.",
            )
        return {
            "kind": "google_drive_file",
            "metadata": metadata,
            "content": content[:_MAX_CHAT_CHARACTERS],
            "content_truncated": len(content) > _MAX_CHAT_CHARACTERS,
            "read_only": True,
        }

    def _json_request(self, url: str, token: str) -> dict[str, Any]:
        raw = self._bytes_request(url, token)
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise DomainError("DRIVE_RESPONSE_INVALID", "Drive devolvió una respuesta no válida.") from error
        if not isinstance(payload, dict):
            raise DomainError("DRIVE_RESPONSE_INVALID", "Drive devolvió una respuesta no válida.")
        return payload

    def _bytes_request(self, url: str, token: str) -> bytes:
        request = Request(url, headers={"Authorization": f"Bearer {token}", "Accept": "application/json"})
        try:
            with urlopen(request, timeout=15) as response:  # noqa: S310 - fixed Google HTTPS endpoints
                payload = response.read(self._max_read_bytes + 1)
        except HTTPError as error:
            if error.code in {401, 403}:
                raise DomainError("DRIVE_ACCESS_DENIED", "Drive rechazó la credencial o el permiso solicitado.") from error
            if error.code == 404:
                raise DomainError(
                    "DRIVE_FILE_NOT_FOUND",
                    "El archivo ya no existe o no está disponible para esta cuenta.",
                ) from error
            raise DomainError("DRIVE_REQUEST_FAILED", "No se pudo consultar Google Drive.") from error
        except (URLError, TimeoutError) as error:
            raise DomainError("DRIVE_UNAVAILABLE", "Google Drive no respondió a tiempo.") from error
        if len(payload) > self._max_read_bytes:
            raise DomainError("DRIVE_FILE_TOO_LARGE", "El archivo supera el límite de lectura segura.")
        return bytes(payload)


def _normalize_file_id(raw_file_id: str) -> str:
    """Recover one exact Drive id from structured-model punctuation without guessing names."""

    candidate = raw_file_id.strip().strip("\"'`“”‘’.,;:()[]{}")
    if _DRIVE_FILE_ID.fullmatch(candidate):
        return candidate
    matches = _DRIVE_FILE_ID.findall(candidate)
    return matches[0] if len(matches) == 1 else ""
