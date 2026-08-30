"""Verified identity boundary with an explicit local-development mode."""

from __future__ import annotations

import hashlib
import os
import re
from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from studio.domain.errors import DomainError

_LOCAL_ID = re.compile(r"^[A-Za-z0-9_-]{3,128}$")


class IdentityContext(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    user_id: str = Field(min_length=1, max_length=128)
    workspace_id: str = Field(min_length=1, max_length=180)
    tenant_id: str | None = Field(default=None, max_length=128)
    email: str | None = Field(default=None, max_length=320)
    display_name: str | None = Field(default=None, max_length=200)
    picture_url: str | None = Field(default=None, max_length=2_048)
    role: Literal["owner", "admin", "builder", "operator", "viewer"] = "owner"
    authenticated: bool
    mode: Literal["local", "identity_platform"]

    @property
    def isolation_key(self) -> str:
        if self.mode == "local":
            return self.user_id
        raw = f"{self.workspace_id}:{self.user_id}".encode()
        return f"user_{hashlib.sha256(raw).hexdigest()[:40]}"


@dataclass(frozen=True, slots=True)
class IdentitySettings:
    mode: Literal["local", "identity_platform"] = "local"
    project_id: str = ""

    @classmethod
    def from_environment(cls) -> IdentitySettings:
        raw_mode = os.getenv("STUDIO_AUTH_MODE", "local").strip().casefold()
        mode: Literal["local", "identity_platform"] = (
            "identity_platform" if raw_mode == "identity_platform" else "local"
        )
        return cls(
            mode=mode,
            project_id=os.getenv(
                "STUDIO_IDENTITY_PROJECT", os.getenv("GOOGLE_CLOUD_PROJECT", "")
            ).strip(),
        )


class IdentityVerifier:
    """Resolve a trusted user context; never trust a client owner id in cloud mode."""

    def __init__(self, settings: IdentitySettings | None = None) -> None:
        self.settings = settings or IdentitySettings.from_environment()

    def verify(self, authorization: str | None, local_session: str | None) -> IdentityContext:
        if self.settings.mode == "local":
            session = (local_session or "").strip()
            if not _LOCAL_ID.fullmatch(session):
                raise DomainError(
                    "LOCAL_IDENTITY_REQUIRED",
                    "La sesión local no es válida. Recarga la aplicación para crear una nueva.",
                )
            return IdentityContext(
                user_id=session,
                workspace_id=f"personal_{session}",
                authenticated=False,
                mode="local",
            )

        if not authorization or not authorization.startswith("Bearer "):
            raise DomainError(
                "AUTHENTICATION_REQUIRED",
                "Inicia sesión para acceder a tus conversaciones, agentes y conexiones.",
            )
        token = authorization.removeprefix("Bearer ").strip()
        if not token or not self.settings.project_id:
            raise DomainError("AUTHENTICATION_REQUIRED", "No fue posible verificar la identidad.")
        claims = self._verify_identity_platform_token(token)
        subject = str(claims.get("sub") or claims.get("user_id") or "").strip()
        if not subject:
            raise DomainError("AUTHENTICATION_INVALID", "El token no identifica a un usuario.")
        firebase_claim = claims.get("firebase")
        firebase: dict[str, Any] = firebase_claim if isinstance(firebase_claim, dict) else {}
        tenant = str(firebase.get("tenant") or claims.get("tenant_id") or "").strip() or None
        workspace = f"tenant_{tenant}" if tenant else f"personal_{subject}"
        return IdentityContext(
            user_id=subject,
            workspace_id=workspace,
            tenant_id=tenant,
            email=str(claims.get("email") or "").strip() or None,
            display_name=str(claims.get("name") or "").strip() or None,
            picture_url=(
                picture
                if (picture := str(claims.get("picture") or "").strip()).startswith("https://")
                else None
            ),
            authenticated=True,
            mode="identity_platform",
        )

    def _verify_identity_platform_token(self, token: str) -> dict[str, Any]:
        try:
            from google.auth.transport.requests import Request as GoogleRequest
            from google.oauth2 import id_token

            claims = id_token.verify_firebase_token(  # type: ignore[no-untyped-call]
                token,
                GoogleRequest(),
                audience=self.settings.project_id,
            )
        except Exception as error:
            raise DomainError(
                "AUTHENTICATION_INVALID",
                "La sesión venció o no pudo verificarse. Inicia sesión nuevamente.",
            ) from error
        if not isinstance(claims, dict):
            raise DomainError("AUTHENTICATION_INVALID", "El token de identidad no es válido.")
        return claims
