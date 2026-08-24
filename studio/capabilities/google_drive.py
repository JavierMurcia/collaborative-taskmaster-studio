"""Read-only Google Drive capability backed by a user-scoped OAuth connection."""

from __future__ import annotations

import json
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

from studio.application.connection_service import ConnectionService
from studio.domain.errors import DomainError
from studio.security import IdentityContext

_FILES_ENDPOINT = "https://www.googleapis.com/drive/v3/files"
_FOLDER_MIME_TYPE = "application/vnd.google-apps.folder"
_GOOGLE_EXPORTS = {
    "application/vnd.google-apps.document": "text/plain",
    "application/vnd.google-apps.spreadsheet": "text/csv",
    "application/vnd.google-apps.presentation": "text/plain",
}
_DIRECT_MIME_PREFIXES = ("text/", "application/json", "application/xml")


class GoogleDriveReader:
    def __init__(self, connections: ConnectionService, *, max_read_bytes: int = 250_000) -> None:
        self._connections = connections
        self._max_read_bytes = max(1_000, min(max_read_bytes, 1_000_000))

    def available(self, identity: IdentityContext) -> bool:
        return self._connections.connected(identity, "google.drive")

    def search(self, identity: IdentityContext, query: str, *, limit: int = 10) -> dict[str, object]:
        token = self._connections.access_token(identity, "google.drive")
        safe_query = query.strip().replace("\\", "\\\\").replace("'", "\\'")
        filters = ["trashed = false"]
        if safe_query:
            filters.append(f"name contains '{safe_query}'")
        parameters = urlencode(
            {
                "q": " and ".join(filters),
                "pageSize": max(1, min(limit, 25)),
                "orderBy": "modifiedTime desc",
                "fields": "files(id,name,mimeType,modifiedTime,size,webViewLink,parents)",
                "spaces": "drive",
            }
        )
        payload = self._json_request(f"{_FILES_ENDPOINT}?{parameters}", token)
        files = payload.get("files", [])
        return {
            "kind": "google_drive_search",
            "query": query,
            "files": files if isinstance(files, list) else [],
            "read_only": True,
        }

    def list_folders(
        self,
        identity: IdentityContext,
        query: str = "",
        *,
        limit: int = 100,
    ) -> dict[str, object]:
        """List folders across My Drive, including nested folders and parent references."""

        token = self._connections.access_token(identity, "google.drive")
        safe_query = query.strip().replace("\\", "\\\\").replace("'", "\\'")
        filters = ["trashed = false", f"mimeType = '{_FOLDER_MIME_TYPE}'"]
        if safe_query:
            filters.append(f"name contains '{safe_query}'")

        bounded_limit = max(1, min(limit, 200))
        folders: list[object] = []
        page_token = ""
        while len(folders) < bounded_limit:
            parameters: dict[str, object] = {
                "q": " and ".join(filters),
                "pageSize": min(100, bounded_limit - len(folders)),
                "orderBy": "name_natural",
                "fields": "nextPageToken,files(id,name,mimeType,modifiedTime,webViewLink,parents)",
                "spaces": "drive",
            }
            if page_token:
                parameters["pageToken"] = page_token
            payload = self._json_request(f"{_FILES_ENDPOINT}?{urlencode(parameters)}", token)
            page = payload.get("files", [])
            if isinstance(page, list):
                folders.extend(page[: bounded_limit - len(folders)])
            page_token = str(payload.get("nextPageToken") or "")
            if not page_token or not page:
                break

        return {
            "kind": "google_drive_folders",
            "query": query,
            "folders": folders,
            "count": len(folders),
            "read_only": True,
        }

    def read(self, identity: IdentityContext, file_id: str) -> dict[str, object]:
        if not file_id or len(file_id) > 200:
            raise DomainError("DRIVE_FILE_INVALID", "El identificador de Drive no es válido.")
        token = self._connections.access_token(identity, "google.drive")
        metadata = self._json_request(
            f"{_FILES_ENDPOINT}/{quote(file_id, safe='')}?fields=id,name,mimeType,modifiedTime,size,webViewLink",
            token,
        )
        mime_type = str(metadata.get("mimeType") or "")
        if mime_type in _GOOGLE_EXPORTS:
            url = (
                f"{_FILES_ENDPOINT}/{quote(file_id, safe='')}/export?"
                + urlencode({"mimeType": _GOOGLE_EXPORTS[mime_type]})
            )
        elif mime_type.startswith(_DIRECT_MIME_PREFIXES):
            url = f"{_FILES_ENDPOINT}/{quote(file_id, safe='')}?alt=media"
        else:
            raise DomainError(
                "DRIVE_FILE_TYPE_UNSUPPORTED",
                "Este tipo de archivo no puede leerse como texto dentro del chat.",
            )
        content = self._bytes_request(url, token).decode("utf-8", errors="replace")
        return {"kind": "google_drive_file", "metadata": metadata, "content": content, "read_only": True}

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
            raise DomainError("DRIVE_REQUEST_FAILED", "No se pudo consultar Google Drive.") from error
        except (URLError, TimeoutError) as error:
            raise DomainError("DRIVE_UNAVAILABLE", "Google Drive no respondió a tiempo.") from error
        if len(payload) > self._max_read_bytes:
            raise DomainError("DRIVE_FILE_TOO_LARGE", "El archivo supera el límite de lectura segura.")
        return bytes(payload)
