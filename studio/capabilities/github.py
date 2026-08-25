"""Read-only GitHub capability backed by a user-scoped OAuth connection."""

from __future__ import annotations

import json
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from studio.application.connection_service import ConnectionService
from studio.domain.errors import DomainError
from studio.security import IdentityContext

_REPOSITORIES_ENDPOINT = "https://api.github.com/user/repos"
_MAX_RESPONSE_BYTES = 2_000_000
_MAX_PAGES = 10


class GitHubReader:
    """List repositories visible to the connected GitHub OAuth token."""

    def __init__(self, connections: ConnectionService) -> None:
        self._connections = connections

    def available(self, identity: IdentityContext) -> bool:
        return self._connections.connected(identity, "github")

    def list_repositories(
        self,
        identity: IdentityContext,
        query: str = "",
        *,
        limit: int = 100,
    ) -> dict[str, object]:
        token = self._connections.access_token(identity, "github")
        bounded_limit = max(1, min(limit, 1_000))
        repositories: list[dict[str, object]] = []
        page = 1
        while len(repositories) < bounded_limit and page <= _MAX_PAGES:
            parameters = {
                "affiliation": "owner",
                "visibility": "all",
                "sort": "updated",
                "direction": "desc",
                "per_page": min(100, bounded_limit - len(repositories)),
                "page": page,
            }
            payload = self._json_request(
                f"{_REPOSITORIES_ENDPOINT}?{urlencode(parameters)}", token
            )
            page_items = payload if isinstance(payload, list) else []
            if not page_items:
                break
            repositories.extend(
                _repository_summary(item)
                for item in page_items
                if isinstance(item, dict)
            )
            if len(page_items) < int(parameters["per_page"]):
                break
            page += 1

        normalized_query = query.strip().casefold()
        visible = repositories
        if normalized_query:
            visible = [
                item
                for item in repositories
                if normalized_query
                in " ".join(
                    (
                        str(item.get("name") or ""),
                        str(item.get("full_name") or ""),
                        str(item.get("description") or ""),
                    )
                ).casefold()
            ]
        return {
            "kind": "github_repositories",
            "query": query,
            "repositories": visible,
            "visible_repository_count": len(repositories),
            "matching_repository_count": len(visible),
            "count_is_complete": page <= _MAX_PAGES and len(repositories) < bounded_limit,
            "authorization_scope": "repositorios visibles para el token OAuth actual",
            "read_only": True,
        }

    @staticmethod
    def _json_request(url: str, token: str) -> list[dict[str, Any]]:
        request = Request(
            url,
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
                "User-Agent": "Collaborative-Taskmaster-Studio",
            },
        )
        try:
            with urlopen(request, timeout=15) as response:  # noqa: S310 - fixed GitHub endpoint
                raw = response.read(_MAX_RESPONSE_BYTES + 1)
        except HTTPError as error:
            if error.code in {401, 403}:
                raise DomainError(
                    "GITHUB_ACCESS_DENIED",
                    "GitHub rechazó la credencial o el permiso solicitado.",
                ) from error
            raise DomainError(
                "GITHUB_REQUEST_FAILED", "No se pudieron consultar los repositorios de GitHub."
            ) from error
        except (URLError, TimeoutError) as error:
            raise DomainError("GITHUB_UNAVAILABLE", "GitHub no respondió a tiempo.") from error
        if len(raw) > _MAX_RESPONSE_BYTES:
            raise DomainError(
                "GITHUB_RESPONSE_TOO_LARGE", "La respuesta de GitHub supera el límite seguro."
            )
        try:
            decoded = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise DomainError(
                "GITHUB_RESPONSE_INVALID", "GitHub devolvió una respuesta no válida."
            ) from error
        if not isinstance(decoded, list):
            raise DomainError(
                "GITHUB_RESPONSE_INVALID", "GitHub devolvió una respuesta no válida."
            )
        return decoded


def _repository_summary(payload: dict[str, Any]) -> dict[str, object]:
    owner = payload.get("owner") if isinstance(payload.get("owner"), dict) else {}
    return {
        "id": str(payload.get("id") or ""),
        "name": str(payload.get("name") or ""),
        "full_name": str(payload.get("full_name") or ""),
        "description": str(payload.get("description") or "")[:500],
        "private": bool(payload.get("private")),
        "fork": bool(payload.get("fork")),
        "archived": bool(payload.get("archived")),
        "default_branch": str(payload.get("default_branch") or ""),
        "language": str(payload.get("language") or ""),
        "updated_at": str(payload.get("updated_at") or ""),
        "html_url": str(payload.get("html_url") or ""),
        "owner": str(owner.get("login") or ""),
    }
