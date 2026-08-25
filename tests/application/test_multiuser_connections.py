from __future__ import annotations

from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest

from studio.application.connection_service import ConnectionService, OAuthSettings
from studio.application.plugin_registry import PluginRegistry
from studio.domain.errors import DomainError
from studio.security import IdentityContext, IdentitySettings, IdentityVerifier
from studio.security.credential_vault import InMemoryCredentialVault


def local_identity(user_id: str) -> IdentityContext:
    return IdentityContext(
        user_id=user_id,
        workspace_id=f"personal_{user_id}",
        authenticated=False,
        mode="local",
    )


def cloud_identity(user_id: str) -> IdentityContext:
    return IdentityContext(
        user_id=user_id,
        workspace_id=f"personal_{user_id}",
        email=f"{user_id}@example.com",
        authenticated=True,
        mode="identity_platform",
    )


def oauth_settings() -> OAuthSettings:
    return OAuthSettings(
        public_base_url="https://studio.example",
        google_client_id="client.apps.googleusercontent.com",
        google_client_secret="client-secret",
        github_client_id="github-client-id",
        github_client_secret="github-client-secret",
        state_secret="state-secret-with-enough-entropy-for-tests",
    )


def test_local_identity_preserves_existing_isolation_key() -> None:
    verifier = IdentityVerifier(IdentitySettings(mode="local"))

    identity = verifier.verify(None, "browser_owner")

    assert identity.user_id == "browser_owner"
    assert identity.isolation_key == "browser_owner"
    assert identity.authenticated is False


def test_cloud_identity_never_accepts_browser_session_as_identity() -> None:
    verifier = IdentityVerifier(
        IdentitySettings(mode="identity_platform", project_id="studio-project")
    )

    with pytest.raises(DomainError) as captured:
        verifier.verify(None, "forged_owner")

    assert captured.value.code == "AUTHENTICATION_REQUIRED"


def test_connection_metadata_is_isolated_between_users(tmp_path: Path) -> None:
    service = ConnectionService(tmp_path, PluginRegistry())
    javier = local_identity("javier")
    ana = local_identity("ana_user")

    started = service.begin(javier, "google.drive")

    assert started.connection.status == "setup_required"
    assert service.list(javier) == (started.connection,)
    assert service.list(ana) == ()
    with pytest.raises(DomainError) as captured:
        service.revoke(ana, started.connection.id)
    assert captured.value.code == "CONNECTION_ACCESS_DENIED"


def test_chat_can_offer_relevant_oauth_connection(tmp_path: Path) -> None:
    service = ConnectionService(tmp_path, PluginRegistry())

    offers = service.offers(local_identity("javier"), "Conecta mi Google Drive")

    assert [offer.plugin_id for offer in offers] == ["google.drive"]
    assert offers[0].status == "not_connected"


def test_oauth_callback_is_signed_and_credentials_remain_out_of_metadata(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    vault = InMemoryCredentialVault()
    service = ConnectionService(
        tmp_path,
        PluginRegistry(),
        vault=vault,
        settings=oauth_settings(),
    )
    identity = cloud_identity("javier")
    started = service.begin(identity, "google.drive")
    state = parse_qs(urlsplit(started.authorization_url or "").query)["state"][0]

    monkeypatch.setattr(
        service,
        "_exchange_google_code",
        lambda code, verifier: {
            "access_token": f"access-{code}",
            "refresh_token": f"refresh-{verifier[:8]}",
            "expires_in": 3600,
            "token_type": "Bearer",
        },
    )
    monkeypatch.setattr(service, "_google_account_label", lambda token: "javier@example.com")

    connected = service.complete_callback(state=state, code="authorization-code")

    assert connected.status == "connected"
    assert connected.account_label == "javier@example.com"
    assert service.access_token(identity, "google.drive") == "access-authorization-code"
    assert "access_token" not in connected.model_dump(mode="json")
    assert "refresh_token" not in connected.model_dump(mode="json")


def test_oauth_callback_rejects_tampered_state(tmp_path: Path) -> None:
    service = ConnectionService(
        tmp_path,
        PluginRegistry(),
        vault=InMemoryCredentialVault(),
        settings=oauth_settings(),
    )
    started = service.begin(cloud_identity("javier"), "google.drive")
    state = parse_qs(urlsplit(started.authorization_url or "").query)["state"][0]

    with pytest.raises(DomainError) as captured:
        service.complete_callback(state=f"{state}tampered", code="authorization-code")

    assert captured.value.code == "OAUTH_STATE_INVALID"


def test_github_oauth_connects_with_minimal_scope_and_without_refresh_token(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    vault = InMemoryCredentialVault()
    service = ConnectionService(
        tmp_path,
        PluginRegistry(),
        vault=vault,
        settings=oauth_settings(),
    )
    identity = cloud_identity("javier")

    started = service.begin(identity, "github")
    query = parse_qs(urlsplit(started.authorization_url or "").query)
    state = query["state"][0]

    assert started.connection.status == "pending"
    assert query["scope"] == ["read:user"]

    monkeypatch.setattr(
        service,
        "_exchange_github_code",
        lambda code, verifier: {
            "access_token": f"github-{code}",
            "token_type": "bearer",
            "scope": "read:user",
        },
    )
    monkeypatch.setattr(service, "_github_account_label", lambda token: "JavierMurcia")

    connected = service.complete_callback(state=state, code="authorization-code")

    assert connected.status == "connected"
    assert connected.account_label == "JavierMurcia"
    assert service.access_token(identity, "github") == "github-authorization-code"
