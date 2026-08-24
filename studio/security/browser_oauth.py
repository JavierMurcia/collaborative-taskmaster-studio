"""Same-origin Google sign-in that exchanges the provider token for a Firebase ID token."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from studio.domain.errors import DomainError


def _urlsafe(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode().rstrip("=")


def _decode_urlsafe(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


@dataclass(frozen=True, slots=True)
class BrowserOAuthSettings:
    public_base_url: str = ""
    google_client_id: str = ""
    google_client_secret: str = ""
    firebase_api_key: str = ""
    state_secret: str = ""

    @classmethod
    def from_environment(cls) -> BrowserOAuthSettings:
        return cls(
            public_base_url=os.getenv("STUDIO_PUBLIC_BASE_URL", "").strip().rstrip("/"),
            google_client_id=os.getenv("STUDIO_GOOGLE_OAUTH_CLIENT_ID", "").strip(),
            google_client_secret=os.getenv("STUDIO_GOOGLE_OAUTH_CLIENT_SECRET", "").strip(),
            firebase_api_key=os.getenv("STUDIO_FIREBASE_API_KEY", "").strip(),
            state_secret=os.getenv("STUDIO_OAUTH_STATE_SECRET", "").strip(),
        )


@dataclass(frozen=True, slots=True)
class BrowserOAuthStart:
    authorization_url: str
    verifier: str


@dataclass(frozen=True, slots=True)
class BrowserOAuthTokens:
    id_token: str
    refresh_token: str


class BrowserOAuthService:
    """Run Google OAuth on the server and return a server-verifiable Firebase token."""

    STATE_PREFIX = "studio-identity."

    def __init__(self, settings: BrowserOAuthSettings | None = None) -> None:
        self.settings = settings or BrowserOAuthSettings.from_environment()

    @property
    def configured(self) -> bool:
        settings = self.settings
        return all(
            (
                settings.public_base_url,
                settings.google_client_id,
                settings.google_client_secret,
                settings.firebase_api_key,
                settings.state_secret,
            )
        )

    def begin(self) -> BrowserOAuthStart:
        if not self.configured:
            raise DomainError(
                "AUTHENTICATION_NOT_CONFIGURED",
                "El inicio de sesión con Google todavía no está configurado.",
            )
        verifier = secrets.token_urlsafe(64)
        challenge = _urlsafe(hashlib.sha256(verifier.encode()).digest())
        state = self._signed_state()
        parameters = {
            "client_id": self.settings.google_client_id,
            "redirect_uri": self.callback_url,
            "response_type": "code",
            "scope": "openid email profile",
            "state": state,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "prompt": "select_account",
        }
        return BrowserOAuthStart(
            authorization_url=(
                "https://accounts.google.com/o/oauth2/v2/auth?" + urlencode(parameters)
            ),
            verifier=verifier,
        )

    def is_identity_state(self, state: str) -> bool:
        return state.startswith(self.STATE_PREFIX)

    def complete(
        self,
        *,
        state: str,
        code: str | None,
        verifier: str | None,
        oauth_error: str | None = None,
    ) -> BrowserOAuthTokens:
        self._verify_state(state)
        if oauth_error or not code or not verifier:
            raise DomainError(
                "AUTHENTICATION_CANCELLED",
                "El inicio de sesión fue cancelado o perdió su contexto seguro.",
            )
        provider_tokens = self._form_post_json(
            "https://oauth2.googleapis.com/token",
            {
                "code": code,
                "client_id": self.settings.google_client_id,
                "client_secret": self.settings.google_client_secret,
                "redirect_uri": self.callback_url,
                "grant_type": "authorization_code",
                "code_verifier": verifier,
            },
        )
        google_id_token = str(provider_tokens.get("id_token") or "")
        if not google_id_token:
            raise DomainError(
                "AUTHENTICATION_EXCHANGE_FAILED",
                "Google no devolvió una identidad verificable.",
            )
        firebase = self._json_post(
            "https://identitytoolkit.googleapis.com/v1/accounts:signInWithIdp?"
            + urlencode({"key": self.settings.firebase_api_key}),
            {
                "postBody": urlencode(
                    {"id_token": google_id_token, "providerId": "google.com"}
                ),
                "requestUri": self.callback_url,
                "returnIdpCredential": True,
                "returnSecureToken": True,
            },
        )
        id_token = str(firebase.get("idToken") or "")
        refresh_token = str(firebase.get("refreshToken") or "")
        if not id_token or not refresh_token:
            raise DomainError(
                "AUTHENTICATION_EXCHANGE_FAILED",
                "Identity Platform no devolvió una sesión válida.",
            )
        return BrowserOAuthTokens(id_token=id_token, refresh_token=refresh_token)

    @property
    def callback_url(self) -> str:
        return (
            f"{self.settings.public_base_url}"
            "/api/v1/collaborative/connections/oauth/callback"
        )

    def _signed_state(self) -> str:
        payload = json.dumps(
            {
                "purpose": "studio_identity",
                "nonce": secrets.token_urlsafe(24),
                "expires": int((datetime.now(UTC) + timedelta(minutes=10)).timestamp()),
            },
            separators=(",", ":"),
        ).encode()
        encoded = _urlsafe(payload)
        signature = hmac.new(
            self.settings.state_secret.encode(), encoded.encode(), hashlib.sha256
        ).digest()
        return f"{self.STATE_PREFIX}{encoded}.{_urlsafe(signature)}"

    def _verify_state(self, state: str) -> None:
        if not self.settings.state_secret or not self.is_identity_state(state):
            raise DomainError("OAUTH_STATE_INVALID", "El retorno de identidad no es válido.")
        try:
            encoded, supplied = state.removeprefix(self.STATE_PREFIX).split(".", 1)
            expected = hmac.new(
                self.settings.state_secret.encode(), encoded.encode(), hashlib.sha256
            ).digest()
            if not hmac.compare_digest(_urlsafe(expected), supplied):
                raise ValueError("signature")
            payload = json.loads(_decode_urlsafe(encoded))
            if payload.get("purpose") != "studio_identity":
                raise ValueError("purpose")
            if int(payload["expires"]) < int(datetime.now(UTC).timestamp()):
                raise ValueError("expired")
        except (ValueError, KeyError, TypeError, json.JSONDecodeError) as error:
            raise DomainError(
                "OAUTH_STATE_INVALID",
                "El retorno de identidad no superó la validación de seguridad.",
            ) from error

    @staticmethod
    def _form_post_json(url: str, values: dict[str, str]) -> dict[str, Any]:
        return BrowserOAuthService._request_json(
            Request(
                url,
                data=urlencode(values).encode(),
                headers={
                    "Content-Type": "application/x-www-form-urlencoded",
                    "Accept": "application/json",
                },
                method="POST",
            )
        )

    @staticmethod
    def _json_post(url: str, payload: dict[str, Any]) -> dict[str, Any]:
        return BrowserOAuthService._request_json(
            Request(
                url,
                data=json.dumps(payload).encode(),
                headers={"Content-Type": "application/json", "Accept": "application/json"},
                method="POST",
            )
        )

    @staticmethod
    def _request_json(request: Request) -> dict[str, Any]:
        try:
            with urlopen(request, timeout=20) as response:  # noqa: S310 - fixed Google endpoints
                payload = json.loads(response.read(128_000).decode("utf-8"))
        except (HTTPError, URLError, TimeoutError, ValueError) as error:
            raise DomainError(
                "AUTHENTICATION_EXCHANGE_FAILED",
                "Google no pudo completar el intercambio seguro de identidad.",
            ) from error
        if not isinstance(payload, dict):
            raise DomainError(
                "AUTHENTICATION_EXCHANGE_FAILED",
                "Google devolvió una respuesta de identidad no válida.",
            )
        return payload
