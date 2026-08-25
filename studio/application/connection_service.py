"""User-scoped connection offers and OAuth initiation contracts."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import RLock
from typing import Any, Literal
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from uuid import uuid4

from pydantic import BaseModel, ConfigDict

from studio.application.plugin_registry import PluginManifest, PluginRegistry
from studio.domain.errors import DomainError
from studio.security.credential_vault import CredentialVault, DisabledCredentialVault
from studio.security.identity import IdentityContext

ConnectionStatus = Literal["setup_required", "pending", "connected", "error", "revoked"]


class ConnectionRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str
    owner_id: str
    workspace_id: str
    plugin_id: str
    title: str
    provider: str
    status: ConnectionStatus
    scopes: tuple[str, ...] = ()
    account_label: str | None = None
    message: str
    updated_at: datetime


class ConnectionOffer(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    plugin_id: str
    title: str
    provider: str
    status: ConnectionStatus | Literal["not_connected"]
    permissions: tuple[str, ...]
    description: str
    action_label: str


class ConnectionStart(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    connection: ConnectionRecord
    authorization_url: str | None = None


@dataclass(frozen=True, slots=True)
class OAuthSettings:
    public_base_url: str = ""
    google_client_id: str = ""
    google_client_secret: str = ""
    github_client_id: str = ""
    github_client_secret: str = ""
    state_secret: str = ""

    @classmethod
    def from_environment(cls) -> OAuthSettings:
        return cls(
            public_base_url=os.getenv("STUDIO_PUBLIC_BASE_URL", "").strip().rstrip("/"),
            google_client_id=os.getenv("STUDIO_GOOGLE_OAUTH_CLIENT_ID", "").strip(),
            google_client_secret=os.getenv("STUDIO_GOOGLE_OAUTH_CLIENT_SECRET", "").strip(),
            github_client_id=os.getenv("STUDIO_GITHUB_OAUTH_CLIENT_ID", "").strip(),
            github_client_secret=os.getenv("STUDIO_GITHUB_OAUTH_CLIENT_SECRET", "").strip(),
            state_secret=os.getenv("STUDIO_OAUTH_STATE_SECRET", "").strip(),
        )


class ConnectionService:
    """Keep connection metadata isolated; credentials never enter this repository."""

    def __init__(
        self,
        root: Path,
        registry: PluginRegistry,
        *,
        vault: CredentialVault | None = None,
        settings: OAuthSettings | None = None,
    ) -> None:
        self._path = root / "connections.json"
        self._registry = registry
        self._vault = vault or DisabledCredentialVault()
        self._settings = settings or OAuthSettings.from_environment()
        self._lock = RLock()

    def list(self, identity: IdentityContext) -> tuple[ConnectionRecord, ...]:
        return tuple(
            item
            for item in self._load()
            if item.owner_id == identity.user_id and item.workspace_id == identity.workspace_id
        )

    def offers(
        self,
        identity: IdentityContext,
        message: str,
        external_actions: tuple[str, ...] = (),
    ) -> tuple[ConnectionOffer, ...]:
        text = " ".join((message, *external_actions)).casefold()
        explicit_connection = any(
            term in text for term in ("conecta", "conectar", "integración", "integracion")
        )
        selected = self._registry.select(
            purpose=message,
            workflow=(),
            inputs=(),
            outputs=(),
            external_actions=external_actions,
        )
        connected = {item.plugin_id: item for item in self.list(identity)}
        offers: list[ConnectionOffer] = []
        for selection in selected:
            manifest = self._registry.get(selection.plugin_id)
            if manifest is None or manifest.auth != "oauth":
                continue
            if not explicit_connection and manifest.id not in {
                value.casefold().replace("_", ".") for value in external_actions
            }:
                continue
            current = connected.get(manifest.id)
            status: ConnectionStatus | Literal["not_connected"] = (
                current.status if current else "not_connected"
            )
            offers.append(self._offer(manifest, status))
        return tuple(offers[:3])

    def begin(self, identity: IdentityContext, plugin_id: str) -> ConnectionStart:
        manifest = self._oauth_manifest(plugin_id)
        now = datetime.now(UTC)
        connection_id = f"conn_{uuid4().hex[:20]}"
        if not identity.authenticated:
            record = ConnectionRecord(
                id=connection_id,
                owner_id=identity.user_id,
                workspace_id=identity.workspace_id,
                plugin_id=manifest.id,
                title=manifest.title,
                provider=manifest.provider,
                status="setup_required",
                scopes=manifest.permissions,
                message="Inicia sesión con una identidad verificada antes de conectar una cuenta real.",
                updated_at=now,
            )
            self._save(record)
            return ConnectionStart(connection=record)

        if manifest.provider not in {"Google", "GitHub"}:
            record = ConnectionRecord(
                id=connection_id,
                owner_id=identity.user_id,
                workspace_id=identity.workspace_id,
                plugin_id=manifest.id,
                title=manifest.title,
                provider=manifest.provider,
                status="setup_required",
                scopes=manifest.permissions,
                message="OAuth real todavía no está implementado para este proveedor.",
                updated_at=now,
            )
            self._save(record)
            return ConnectionStart(connection=record)

        client_id, client_secret, authorization_endpoint = self._provider_configuration(manifest)
        if not (
            client_id
            and client_secret
            and self._settings.public_base_url
            and self._settings.state_secret
            and self._vault.available
        ):
            record = ConnectionRecord(
                id=connection_id,
                owner_id=identity.user_id,
                workspace_id=identity.workspace_id,
                plugin_id=manifest.id,
                title=manifest.title,
                provider=manifest.provider,
                status="setup_required",
                scopes=manifest.permissions,
                message=(
                    "El administrador debe completar Identity Platform, OAuth y la bóveda cifrada "
                    "antes de conectar una cuenta real."
                ),
                updated_at=now,
            )
            self._save(record)
            return ConnectionStart(connection=record)

        record = ConnectionRecord(
            id=connection_id,
            owner_id=identity.user_id,
            workspace_id=identity.workspace_id,
            plugin_id=manifest.id,
            title=manifest.title,
            provider=manifest.provider,
            status="pending",
            scopes=manifest.permissions,
            message="Continúa en la ventana oficial del proveedor.",
            updated_at=now,
        )
        self._save(record)
        callback = self._callback_url()
        verifier = secrets.token_urlsafe(64)
        challenge = _urlsafe(hashlib.sha256(verifier.encode()).digest())
        self._vault.put(
            self._pending_key(record.id),
            {
                "code_verifier": verifier,
                "created_at": now.isoformat(),
            },
        )
        parameters = {
            "client_id": client_id,
            "redirect_uri": callback,
            "response_type": "code",
            "scope": " ".join(manifest.permissions),
            "state": self._signed_state(record.id),
            "code_challenge": challenge,
            "code_challenge_method": "S256",
        }
        if manifest.provider == "Google":
            parameters.update(
                {"access_type": "offline", "include_granted_scopes": "true", "prompt": "consent"}
            )
        return ConnectionStart(
            connection=record,
            authorization_url=f"{authorization_endpoint}?{urlencode(parameters)}",
        )

    def complete_callback(
        self,
        *,
        state: str,
        code: str | None,
        oauth_error: str | None = None,
    ) -> ConnectionRecord:
        connection_id = self._verify_state(state)
        record = self._find(connection_id)
        if record.status != "pending":
            raise DomainError("OAUTH_STATE_INVALID", "La autorización ya no está pendiente.")
        if oauth_error or not code:
            return self._replace(
                record.model_copy(
                    update={
                        "status": "error",
                        "message": "La autorización fue cancelada o rechazada por el proveedor.",
                        "updated_at": datetime.now(UTC),
                    }
                )
            )
        pending = self._vault.get(self._pending_key(record.id))
        verifier = str((pending or {}).get("code_verifier") or "")
        if not verifier:
            raise DomainError("OAUTH_STATE_INVALID", "La autorización venció o perdió su contexto.")
        if record.provider == "Google":
            token_payload = self._exchange_google_code(code, verifier)
        elif record.provider == "GitHub":
            token_payload = self._exchange_github_code(code, verifier)
        else:
            raise DomainError("OAUTH_PROVIDER_UNSUPPORTED", "El retorno OAuth aún no admite este proveedor.")
        existing = self._vault.get(self._credential_key(record)) or {}
        refresh_token = token_payload.get("refresh_token") or existing.get("refresh_token")
        expires_in = int(
            token_payload.get("expires_in")
            or (3600 if record.provider == "Google" else 315_360_000)
        )
        credential = {
            "access_token": str(token_payload.get("access_token") or ""),
            "refresh_token": str(refresh_token or ""),
            "token_type": str(token_payload.get("token_type") or "Bearer"),
            "expires_at": (datetime.now(UTC) + timedelta(seconds=max(60, expires_in))).isoformat(),
            "scope": str(token_payload.get("scope") or " ".join(record.scopes)),
        }
        if not credential["access_token"] or (
            record.provider == "Google" and not credential["refresh_token"]
        ):
            raise DomainError(
                "OAUTH_REFRESH_TOKEN_MISSING",
                "Google no devolvió una credencial renovable. Revoca el acceso y vuelve a consentir.",
            )
        self._vault.put(self._credential_key(record), credential)
        self._vault.delete(self._pending_key(record.id))
        account_label = (
            self._google_account_label(str(credential["access_token"]))
            if record.provider == "Google"
            else self._github_account_label(str(credential["access_token"]))
        )
        return self._replace(
            record.model_copy(
                update={
                    "status": "connected",
                    "account_label": account_label,
                    "message": (
                        "Cuenta conectada con permisos de solo lectura."
                        if record.provider == "Google"
                        else "Cuenta de GitHub conectada con acceso mínimo."
                    ),
                    "updated_at": datetime.now(UTC),
                }
            )
        )

    def connected(self, identity: IdentityContext, plugin_id: str) -> bool:
        return any(item.plugin_id == plugin_id and item.status == "connected" for item in self.list(identity))

    def access_token(self, identity: IdentityContext, plugin_id: str) -> str:
        record = next(
            (item for item in self.list(identity) if item.plugin_id == plugin_id and item.status == "connected"),
            None,
        )
        if record is None:
            raise DomainError(
                "CONNECTION_REQUIRED",
                f"Conecta {self._connection_title(plugin_id)} antes de utilizarlo.",
            )
        credential = self._vault.get(self._credential_key(record))
        if not credential:
            raise DomainError("CONNECTION_CREDENTIAL_MISSING", "La conexión debe autorizarse nuevamente.")
        expires_at = _parse_datetime(credential.get("expires_at"))
        if expires_at <= datetime.now(UTC) + timedelta(seconds=60):
            if record.provider == "Google":
                credential = self._refresh_google_token(record, credential)
            elif record.provider == "GitHub" and credential.get("refresh_token"):
                credential = self._refresh_github_token(record, credential)
            else:
                raise DomainError("OAUTH_REFRESH_REQUIRED", "La conexión debe autorizarse nuevamente.")
        token = str(credential.get("access_token") or "")
        if not token:
            raise DomainError("CONNECTION_CREDENTIAL_MISSING", "La conexión debe autorizarse nuevamente.")
        return token

    def revoke(self, identity: IdentityContext, connection_id: str) -> ConnectionRecord:
        entries = list(self._load())
        for index, item in enumerate(entries):
            if item.id != connection_id:
                continue
            if item.owner_id != identity.user_id or item.workspace_id != identity.workspace_id:
                raise DomainError("CONNECTION_ACCESS_DENIED", "La conexión pertenece a otro usuario.")
            credential = self._vault.get(self._credential_key(item))
            token = str((credential or {}).get("refresh_token") or (credential or {}).get("access_token") or "")
            if token and item.provider == "Google":
                self._revoke_google_token(token)
            self._vault.delete(self._credential_key(item))
            self._vault.delete(self._pending_key(item.id))
            revoked = item.model_copy(
                update={
                    "status": "revoked",
                    "message": "Conexión revocada. El agente ya no puede utilizarla.",
                    "updated_at": datetime.now(UTC),
                }
            )
            entries[index] = revoked
            self._write(entries)
            return revoked
        raise DomainError("CONNECTION_NOT_FOUND", "La conexión solicitada no existe.")

    def _oauth_manifest(self, plugin_id: str) -> PluginManifest:
        manifest = self._registry.get(plugin_id)
        if manifest is None or manifest.auth != "oauth":
            raise DomainError("CONNECTION_UNSUPPORTED", "Este plugin no utiliza una conexión OAuth.")
        return manifest

    def _connection_title(self, plugin_id: str) -> str:
        manifest = self._registry.get(plugin_id)
        return manifest.title if manifest is not None else "el servicio externo"

    def _provider_configuration(self, manifest: PluginManifest) -> tuple[str, str, str]:
        if manifest.provider == "Google":
            return (
                self._settings.google_client_id,
                self._settings.google_client_secret,
                "https://accounts.google.com/o/oauth2/v2/auth",
            )
        if manifest.provider == "GitHub":
            return (
                self._settings.github_client_id,
                self._settings.github_client_secret,
                "https://github.com/login/oauth/authorize",
            )
        return "", "", ""

    def _callback_url(self) -> str:
        return f"{self._settings.public_base_url}/api/v1/collaborative/connections/oauth/callback"

    def _signed_state(self, connection_id: str) -> str:
        payload = json.dumps(
            {"connection_id": connection_id, "expires": int((datetime.now(UTC) + timedelta(minutes=10)).timestamp())},
            separators=(",", ":"),
        ).encode()
        encoded = _urlsafe(payload)
        signature = hmac.new(self._settings.state_secret.encode(), encoded.encode(), hashlib.sha256).digest()
        return f"{encoded}.{_urlsafe(signature)}"

    def _verify_state(self, state: str) -> str:
        if not self._settings.state_secret:
            raise DomainError("OAUTH_STATE_INVALID", "La firma OAuth no está configurada.")
        try:
            encoded, supplied_signature = state.split(".", 1)
            expected = hmac.new(self._settings.state_secret.encode(), encoded.encode(), hashlib.sha256).digest()
            if not hmac.compare_digest(_urlsafe(expected), supplied_signature):
                raise ValueError("signature")
            payload = json.loads(_decode_urlsafe(encoded))
            if int(payload["expires"]) < int(datetime.now(UTC).timestamp()):
                raise ValueError("expired")
            return str(payload["connection_id"])
        except (ValueError, KeyError, TypeError, json.JSONDecodeError) as error:
            raise DomainError("OAUTH_STATE_INVALID", "El retorno OAuth no superó la validación de seguridad.") from error

    def _exchange_google_code(self, code: str, verifier: str) -> dict[str, Any]:
        return self._form_post_json(
            "https://oauth2.googleapis.com/token",
            {
                "code": code,
                "client_id": self._settings.google_client_id,
                "client_secret": self._settings.google_client_secret,
                "redirect_uri": self._callback_url(),
                "grant_type": "authorization_code",
                "code_verifier": verifier,
            },
        )

    def _exchange_github_code(self, code: str, verifier: str) -> dict[str, Any]:
        payload = self._form_post_json(
            "https://github.com/login/oauth/access_token",
            {
                "code": code,
                "client_id": self._settings.github_client_id,
                "client_secret": self._settings.github_client_secret,
                "redirect_uri": self._callback_url(),
                "code_verifier": verifier,
            },
        )
        if payload.get("error"):
            raise DomainError(
                "OAUTH_EXCHANGE_FAILED",
                "GitHub rechazó la autorización. Revisa el cliente OAuth y vuelve a intentarlo.",
            )
        return payload

    def _refresh_google_token(self, record: ConnectionRecord, credential: dict[str, object]) -> dict[str, object]:
        refresh_token = str(credential.get("refresh_token") or "")
        if not refresh_token:
            raise DomainError("OAUTH_REFRESH_REQUIRED", "La conexión debe autorizarse nuevamente.")
        payload = self._form_post_json(
            "https://oauth2.googleapis.com/token",
            {
                "client_id": self._settings.google_client_id,
                "client_secret": self._settings.google_client_secret,
                "refresh_token": refresh_token,
                "grant_type": "refresh_token",
            },
        )
        updated = dict(credential)
        updated.update(
            access_token=str(payload.get("access_token") or ""),
            expires_at=(datetime.now(UTC) + timedelta(seconds=int(payload.get("expires_in") or 3600))).isoformat(),
        )
        self._vault.put(self._credential_key(record), updated)
        return updated

    def _refresh_github_token(self, record: ConnectionRecord, credential: dict[str, object]) -> dict[str, object]:
        refresh_token = str(credential.get("refresh_token") or "")
        if not refresh_token:
            raise DomainError("OAUTH_REFRESH_REQUIRED", "La conexión debe autorizarse nuevamente.")
        payload = self._form_post_json(
            "https://github.com/login/oauth/access_token",
            {
                "client_id": self._settings.github_client_id,
                "client_secret": self._settings.github_client_secret,
                "refresh_token": refresh_token,
                "grant_type": "refresh_token",
            },
        )
        updated = dict(credential)
        updated.update(
            access_token=str(payload.get("access_token") or ""),
            refresh_token=str(payload.get("refresh_token") or refresh_token),
            expires_at=(
                datetime.now(UTC) + timedelta(seconds=int(payload.get("expires_in") or 28_800))
            ).isoformat(),
        )
        self._vault.put(self._credential_key(record), updated)
        return updated

    def _google_account_label(self, token: str) -> str:
        try:
            request = Request(
                "https://www.googleapis.com/drive/v3/about?fields=user(displayName,emailAddress)",
                headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
            )
            with urlopen(request, timeout=10) as response:  # noqa: S310 - fixed Google HTTPS endpoint
                payload = json.loads(response.read(16_384).decode("utf-8"))
            user = payload.get("user", {}) if isinstance(payload, dict) else {}
            return str(user.get("emailAddress") or user.get("displayName") or "Cuenta de Google")[:320]
        except (HTTPError, URLError, TimeoutError, ValueError, TypeError):
            return "Cuenta de Google"

    def _github_account_label(self, token: str) -> str:
        try:
            request = Request(
                "https://api.github.com/user",
                headers={
                    "Authorization": f"Bearer {token}",
                    "Accept": "application/vnd.github+json",
                    "X-GitHub-Api-Version": "2022-11-28",
                    "User-Agent": "Collaborative-Taskmaster-Studio",
                },
            )
            with urlopen(request, timeout=10) as response:  # noqa: S310 - fixed GitHub HTTPS endpoint
                payload = json.loads(response.read(16_384).decode("utf-8"))
            return str(payload.get("login") or payload.get("name") or "Cuenta de GitHub")[:320]
        except (HTTPError, URLError, TimeoutError, ValueError, TypeError, AttributeError):
            return "Cuenta de GitHub"

    @staticmethod
    def _revoke_google_token(token: str) -> None:
        try:
            request = Request(
                "https://oauth2.googleapis.com/revoke",
                data=urlencode({"token": token}).encode(),
                headers={"Content-Type": "application/x-www-form-urlencoded"},
                method="POST",
            )
            with urlopen(request, timeout=10):  # noqa: S310 - fixed Google HTTPS endpoint
                pass
        except (HTTPError, URLError, TimeoutError):
            pass

    @staticmethod
    def _form_post_json(url: str, values: dict[str, str]) -> dict[str, Any]:
        request = Request(
            url,
            data=urlencode(values).encode(),
            headers={"Content-Type": "application/x-www-form-urlencoded", "Accept": "application/json"},
            method="POST",
        )
        try:
            with urlopen(request, timeout=15) as response:  # noqa: S310 - fixed provider HTTPS endpoint
                payload = json.loads(response.read(64_000).decode("utf-8"))
        except (HTTPError, URLError, TimeoutError, ValueError) as error:
            raise DomainError("OAUTH_EXCHANGE_FAILED", "El proveedor no pudo completar la autorización.") from error
        if not isinstance(payload, dict):
            raise DomainError("OAUTH_EXCHANGE_FAILED", "El proveedor devolvió una respuesta no válida.")
        return payload

    def _find(self, connection_id: str) -> ConnectionRecord:
        record = next((item for item in self._load() if item.id == connection_id), None)
        if record is None:
            raise DomainError("CONNECTION_NOT_FOUND", "La conexión solicitada no existe.")
        return record

    def _replace(self, record: ConnectionRecord) -> ConnectionRecord:
        entries = [item for item in self._load() if item.id != record.id]
        entries.append(record)
        self._write(entries)
        return record

    @staticmethod
    def _pending_key(connection_id: str) -> str:
        return f"oauth-pending:{connection_id}"

    @staticmethod
    def _credential_key(record: ConnectionRecord) -> str:
        return f"oauth:{record.workspace_id}:{record.owner_id}:{record.plugin_id}"

    @staticmethod
    def _offer(
        manifest: PluginManifest,
        status: ConnectionStatus | Literal["not_connected"],
    ) -> ConnectionOffer:
        return ConnectionOffer(
            plugin_id=manifest.id,
            title=manifest.title,
            provider=manifest.provider,
            status=status,
            permissions=manifest.permissions,
            description=manifest.description,
            action_label="Administrar" if status == "connected" else f"Conectar {manifest.title}",
        )

    def _load(self) -> tuple[ConnectionRecord, ...]:
        with self._lock:
            if not self._path.exists():
                return ()
            try:
                payload = json.loads(self._path.read_text(encoding="utf-8"))
                return tuple(ConnectionRecord.model_validate(item) for item in payload)
            except (OSError, ValueError):
                return ()

    def _save(self, record: ConnectionRecord) -> None:
        entries = [
            item
            for item in self._load()
            if not (
                item.owner_id == record.owner_id
                and item.workspace_id == record.workspace_id
                and item.plugin_id == record.plugin_id
            )
        ]
        entries.append(record)
        self._write(entries)

    def _write(self, entries: Sequence[ConnectionRecord]) -> None:
        with self._lock:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self._path.with_suffix(".tmp")
            temporary.write_text(
                json.dumps(
                    [item.model_dump(mode="json") for item in entries],
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )
            temporary.replace(self._path)


def _urlsafe(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _decode_urlsafe(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def _parse_datetime(value: object) -> datetime:
    try:
        parsed = datetime.fromisoformat(str(value))
    except ValueError:
        return datetime.fromtimestamp(0, UTC)
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)
