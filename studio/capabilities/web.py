"""Grounded web research through Gemini's Google Search tool."""

from __future__ import annotations

import ipaddress
from datetime import date
from typing import Any, Protocol
from urllib.parse import urlsplit

from studio.domain.errors import DomainError


class WebResearcher(Protocol):
    def search(self, query: str) -> dict[str, Any]: ...

    def open_url(self, url: str) -> dict[str, Any]: ...


class VertexWebResearcher:
    def __init__(self, client: Any, model: str, *, max_output_tokens: int = 1_200) -> None:
        self._client = client
        self._model = model
        self._max_output_tokens = min(max_output_tokens, 1_500)

    def search(self, query: str) -> dict[str, Any]:
        clean = query.strip()
        if len(clean) < 3 or len(clean) > 240:
            raise DomainError("WEB_QUERY_INVALID", "La consulta web debe tener entre 3 y 240 caracteres.")
        try:
            from google.genai import types

            response = self._client.models.generate_content(
                model=self._model,
                contents=(
                    f"La fecha actual es {date.today().isoformat()}. Busca en Google información para "
                    "responder esta consulta y resume solo resultados respaldados por fuentes. Cuando la "
                    "consulta pida información reciente, prioriza acontecimientos del año actual y ordena "
                    f"por la fecha real del acontecimiento: {clean}"
                ),
                config=types.GenerateContentConfig(
                    tools=[types.Tool(google_search=types.GoogleSearch())],
                    max_output_tokens=self._max_output_tokens,
                ),
            )
        except Exception as error:
            raise DomainError("WEB_RESEARCH_UNAVAILABLE", "La investigación web no pudo completarse.") from error
        summary = getattr(response, "text", "")
        if not isinstance(summary, str) or not summary.strip():
            raise DomainError("WEB_RESEARCH_EMPTY", "La búsqueda no devolvió contenido utilizable.")
        sources: list[dict[str, str]] = []
        candidates = getattr(response, "candidates", None) or []
        metadata = getattr(candidates[0], "grounding_metadata", None) if candidates else None
        for chunk in getattr(metadata, "grounding_chunks", None) or []:
            web = getattr(chunk, "web", None)
            uri = getattr(web, "uri", None)
            title = getattr(web, "title", None)
            if isinstance(uri, str) and uri.startswith(("https://", "http://")):
                item = {"title": title if isinstance(title, str) and title else uri, "url": uri}
                if item not in sources:
                    sources.append(item)
            if len(sources) >= 8:
                break
        if not sources:
            raise DomainError(
                "WEB_RESEARCH_UNGROUNDED",
                "La búsqueda respondió sin fuentes verificables y fue descartada.",
            )
        return {
            "kind": "web_search",
            "query": clean,
            "summary": summary.strip()[:8_000],
            "sources": sources,
            "grounded": bool(sources),
        }

    def open_url(self, url: str) -> dict[str, Any]:
        clean = _public_url(url)
        try:
            from google.genai import types

            response = self._client.models.generate_content(
                model=self._model,
                contents=(
                    f"Lee directamente esta URL y resume únicamente su contenido verificable: {clean}"
                ),
                config=types.GenerateContentConfig(
                    tools=[types.Tool(url_context=types.UrlContext())],
                    max_output_tokens=self._max_output_tokens,
                ),
            )
        except DomainError:
            raise
        except Exception as error:
            raise DomainError("WEB_PAGE_UNAVAILABLE", "La URL no pudo consultarse.") from error
        summary = getattr(response, "text", "")
        candidates = getattr(response, "candidates", None) or []
        metadata = getattr(candidates[0], "url_context_metadata", None) if candidates else None
        entries = getattr(metadata, "url_metadata", None) or []
        successful = [
            entry
            for entry in entries
            if str(getattr(entry, "url_retrieval_status", ""))
            == "URL_RETRIEVAL_STATUS_SUCCESS"
        ]
        if not successful or not isinstance(summary, str) or not summary.strip():
            raise DomainError(
                "WEB_PAGE_UNVERIFIED",
                "La URL no devolvió contenido verificable y fue descartada.",
            )
        retrieved = str(getattr(successful[0], "retrieved_url", "") or clean)
        return {
            "kind": "web_page",
            "url": retrieved,
            "summary": summary.strip()[:8_000],
            "sources": [{"title": retrieved, "url": retrieved}],
            "grounded": True,
        }


def _public_url(value: str) -> str:
    clean = value.strip()
    if len(clean) < 10 or len(clean) > 500:
        raise DomainError("WEB_URL_INVALID", "La URL debe tener entre 10 y 500 caracteres.")
    parsed = urlsplit(clean)
    host = (parsed.hostname or "").casefold()
    if parsed.scheme not in {"http", "https"} or not host or parsed.username or parsed.password:
        raise DomainError("WEB_URL_INVALID", "La URL pública no es válida.")
    if host == "localhost" or host.endswith((".localhost", ".local", ".internal")):
        raise DomainError("WEB_URL_BLOCKED", "La URL local o privada está bloqueada.")
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        address = None
    if address is not None and (
        address.is_private
        or address.is_loopback
        or address.is_link_local
        or address.is_reserved
        or address.is_multicast
        or address.is_unspecified
    ):
        raise DomainError("WEB_URL_BLOCKED", "La dirección privada está bloqueada.")
    return clean
