from urllib.parse import parse_qs, urlsplit

import pytest

from studio.domain.errors import DomainError
from studio.security.browser_oauth import BrowserOAuthService, BrowserOAuthSettings


def settings() -> BrowserOAuthSettings:
    return BrowserOAuthSettings(
        public_base_url="https://studio.example",
        google_client_id="client.apps.googleusercontent.com",
        google_client_secret="client-secret",
        firebase_api_key="public-firebase-key",
        state_secret="state-secret-with-enough-entropy-for-tests",
    )


def test_browser_oauth_uses_registered_connection_callback_and_pkce() -> None:
    service = BrowserOAuthService(settings())

    started = service.begin()
    query = parse_qs(urlsplit(started.authorization_url).query)

    assert query["redirect_uri"] == [
        "https://studio.example/api/v1/collaborative/connections/oauth/callback"
    ]
    assert query["scope"] == ["openid email profile"]
    assert query["code_challenge_method"] == ["S256"]
    assert query["state"][0].startswith(service.STATE_PREFIX)
    assert len(started.verifier) > 40


def test_browser_oauth_exchanges_google_identity_for_firebase_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = BrowserOAuthService(settings())
    started = service.begin()
    state = parse_qs(urlsplit(started.authorization_url).query)["state"][0]
    monkeypatch.setattr(
        service,
        "_form_post_json",
        lambda url, values: {"id_token": "google-id-token"},
    )
    monkeypatch.setattr(
        service,
        "_json_post",
        lambda url, payload: {
            "idToken": "firebase-id-token",
            "refreshToken": "firebase-refresh-token",
        },
    )

    tokens = service.complete(
        state=state,
        code="authorization-code",
        verifier=started.verifier,
    )

    assert tokens.id_token == "firebase-id-token"
    assert tokens.refresh_token == "firebase-refresh-token"


def test_browser_oauth_rejects_tampered_state() -> None:
    service = BrowserOAuthService(settings())
    started = service.begin()
    state = parse_qs(urlsplit(started.authorization_url).query)["state"][0]

    with pytest.raises(DomainError) as captured:
        service.complete(
            state=f"{state}tampered",
            code="authorization-code",
            verifier=started.verifier,
        )

    assert captured.value.code == "OAUTH_STATE_INVALID"


def test_browser_oauth_refreshes_identity_platform_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = BrowserOAuthService(settings())
    captured: dict[str, object] = {}

    def fake_post(url: str, values: dict[str, str]) -> dict[str, str]:
        captured.update({"url": url, "values": values})
        return {"id_token": "fresh-id-token", "refresh_token": "rotated-refresh-token"}

    monkeypatch.setattr(service, "_form_post_json", fake_post)

    tokens = service.refresh("stored-refresh-token-with-enough-length")

    assert tokens.id_token == "fresh-id-token"
    assert tokens.refresh_token == "rotated-refresh-token"
    assert "securetoken.googleapis.com" in str(captured["url"])
    assert captured["values"] == {
        "grant_type": "refresh_token",
        "refresh_token": "stored-refresh-token-with-enough-length",
    }
