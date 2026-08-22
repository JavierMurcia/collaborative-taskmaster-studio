"""Encrypted, user-scoped storage for OAuth grants.

The browser and connection catalog only receive metadata. Token payloads are
encrypted before they reach Firestore and are never written to conversations.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
from collections.abc import Mapping
from datetime import UTC, datetime
from threading import RLock
from typing import Any, Protocol

from cryptography.exceptions import InvalidTag

from studio.domain.errors import DomainError


class CredentialVault(Protocol):
    @property
    def available(self) -> bool: ...

    def put(self, key: str, value: Mapping[str, object]) -> None: ...

    def get(self, key: str) -> dict[str, object] | None: ...

    def delete(self, key: str) -> None: ...


class DisabledCredentialVault:
    @property
    def available(self) -> bool:
        return False

    def put(self, key: str, value: Mapping[str, object]) -> None:
        del key, value
        raise DomainError("TOKEN_VAULT_UNAVAILABLE", "La bóveda OAuth no está configurada.")

    def get(self, key: str) -> dict[str, object] | None:
        del key
        return None

    def delete(self, key: str) -> None:
        del key


class InMemoryCredentialVault:
    """Test-only vault; never selected automatically in a deployed service."""

    def __init__(self) -> None:
        self._values: dict[str, dict[str, object]] = {}
        self._lock = RLock()

    @property
    def available(self) -> bool:
        return True

    def put(self, key: str, value: Mapping[str, object]) -> None:
        with self._lock:
            self._values[key] = dict(value)

    def get(self, key: str) -> dict[str, object] | None:
        with self._lock:
            value = self._values.get(key)
            return dict(value) if value is not None else None

    def delete(self, key: str) -> None:
        with self._lock:
            self._values.pop(key, None)


class FirestoreEncryptedCredentialVault:
    """Persist AES-GCM ciphertext in Firestore using a server-only envelope key."""

    def __init__(self, client: Any, encryption_key: bytes) -> None:
        if len(encryption_key) != 32:
            raise ValueError("OAuth encryption key must contain exactly 32 bytes.")
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM

        self._client = client
        self._cipher = AESGCM(encryption_key)

    @property
    def available(self) -> bool:
        return True

    def put(self, key: str, value: Mapping[str, object]) -> None:
        nonce = os.urandom(12)
        plaintext = json.dumps(dict(value), ensure_ascii=False, separators=(",", ":")).encode()
        ciphertext = self._cipher.encrypt(nonce, plaintext, key.encode())
        self._document(key).set(
            {
                "nonce": base64.b64encode(nonce).decode("ascii"),
                "ciphertext": base64.b64encode(ciphertext).decode("ascii"),
                "updated_at": datetime.now(UTC).isoformat(),
            }
        )

    def get(self, key: str) -> dict[str, object] | None:
        snapshot = self._document(key).get()
        if not snapshot.exists:
            return None
        payload = snapshot.to_dict() or {}
        try:
            plaintext = self._cipher.decrypt(
                base64.b64decode(str(payload["nonce"])),
                base64.b64decode(str(payload["ciphertext"])),
                key.encode(),
            )
            result = json.loads(plaintext.decode("utf-8"))
        except (InvalidTag, KeyError, ValueError, TypeError, json.JSONDecodeError) as error:
            raise DomainError(
                "TOKEN_VAULT_CORRUPT",
                "La credencial almacenada no pudo verificarse.",
            ) from error
        if not isinstance(result, dict):
            raise DomainError("TOKEN_VAULT_CORRUPT", "La credencial almacenada no es válida.")
        return result

    def delete(self, key: str) -> None:
        self._document(key).delete()

    def _document(self, key: str) -> Any:
        document_id = hashlib.sha256(key.encode()).hexdigest()
        return self._client.collection("oauth_credentials").document(document_id)


def build_credential_vault(firestore_client: Any | None) -> CredentialVault:
    """Fail closed unless durable storage and a 256-bit envelope key are present."""

    encoded = os.getenv("STUDIO_OAUTH_ENCRYPTION_KEY", "").strip()
    if firestore_client is None or not encoded:
        return DisabledCredentialVault()
    try:
        key = base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4))
        return FirestoreEncryptedCredentialVault(firestore_client, key)
    except (ValueError, TypeError) as error:
        raise DomainError(
            "TOKEN_VAULT_CONFIGURATION_INVALID",
            "STUDIO_OAUTH_ENCRYPTION_KEY debe ser una clave Base64 URL-safe de 32 bytes.",
        ) from error
