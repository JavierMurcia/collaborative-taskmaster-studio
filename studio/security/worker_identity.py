"""OIDC identity boundary for Cloud Tasks build delivery."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from studio.domain.errors import DomainError


@dataclass(frozen=True, slots=True)
class WorkerIdentitySettings:
    enabled: bool
    audience: str
    service_account_email: str
    queue_name: str


class WorkerTokenVerifier:
    """Accept only Google OIDC tokens issued for the declared worker identity."""

    def __init__(
        self,
        settings: WorkerIdentitySettings,
        *,
        token_verifier: Callable[[str, str], dict[str, Any]] | None = None,
    ) -> None:
        self.settings = settings
        self._token_verifier = token_verifier or _verify_google_token

    def verify(
        self,
        authorization: str | None,
        task_name: str | None,
        queue_name: str | None,
    ) -> dict[str, Any]:
        if not self.settings.enabled:
            raise DomainError("WORKER_DISABLED", "El worker externo no está habilitado.")
        if not authorization or not authorization.startswith("Bearer "):
            raise DomainError("WORKER_AUTH_REQUIRED", "La tarea no presentó identidad OIDC.")
        if not task_name or not queue_name or queue_name != self.settings.queue_name:
            raise DomainError("WORKER_TASK_INVALID", "La entrega no pertenece a la cola declarada.")
        token = authorization.removeprefix("Bearer ").strip()
        try:
            claims = self._token_verifier(token, self.settings.audience)
        except Exception as error:
            raise DomainError("WORKER_AUTH_INVALID", "La identidad del worker no es válida.") from error
        issuer = str(claims.get("iss", ""))
        email = str(claims.get("email", ""))
        if (
            issuer not in {"accounts.google.com", "https://accounts.google.com"}
            or email != self.settings.service_account_email
            or claims.get("email_verified") is not True
        ):
            raise DomainError("WORKER_AUTH_INVALID", "La identidad del worker no es válida.")
        return claims


def _verify_google_token(token: str, audience: str) -> dict[str, Any]:
    from google.auth.transport.requests import Request as GoogleRequest
    from google.oauth2 import id_token

    claims = id_token.verify_oauth2_token(token, GoogleRequest(), audience=audience)
    if not isinstance(claims, dict):
        raise ValueError("Invalid worker token claims.")
    return claims
