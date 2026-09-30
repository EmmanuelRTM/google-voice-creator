import pytest
from mcp.server.auth.provider import AccessToken
from starlette.testclient import TestClient

from voice_creator_mcp.auth.config import HttpConfig
from voice_creator_mcp.config import Settings
from voice_creator_mcp.errors import ConfigError
from voice_creator_mcp.server.http import build_http_app

OAUTH_ENV = {
    "VOICE_CREATOR_OAUTH_ISSUER": "https://auth.example.com",
    "VOICE_CREATOR_RESOURCE_URL": "https://voice.example.com/mcp",
    "VOICE_CREATOR_OAUTH_JWKS_URL": "https://auth.example.com/jwks",
}


class AcceptAll:
    async def verify_token(self, token: str) -> AccessToken | None:
        return AccessToken(token=token, client_id="c", scopes=["voice:read"], subject="alice")


@pytest.fixture
def settings(tmp_path) -> Settings:
    return Settings.from_env({"VOICE_CREATOR_DATA_DIR": str(tmp_path)}, home=tmp_path)


def test_oauth_app_serves_metadata_and_challenges(settings: Settings) -> None:
    app = build_http_app(settings, HttpConfig.from_env(OAUTH_ENV), verifier=AcceptAll())
    client = TestClient(app, base_url="https://voice.example.com")

    metadata = client.get("/.well-known/oauth-protected-resource/mcp").json()
    unauthenticated = client.post("/mcp", json={})

    assert metadata["resource"] == "https://voice.example.com/mcp"
    assert metadata["authorization_servers"] == ["https://auth.example.com"]
    assert unauthenticated.status_code == 401
    assert "resource_metadata=" in unauthenticated.headers["www-authenticate"]


def test_authorised_requests_reach_the_mcp_transport(settings: Settings) -> None:
    app = build_http_app(settings, HttpConfig.from_env(OAUTH_ENV), verifier=AcceptAll())
    with TestClient(app, base_url="https://voice.example.com") as client:
        response = client.post("/mcp", headers={"Authorization": "Bearer t"}, json={})
    assert response.status_code not in (401, 403)


def test_dns_rebinding_protection_rejects_foreign_hosts(settings: Settings) -> None:
    app = build_http_app(settings, HttpConfig.from_env({"VOICE_CREATOR_AUTH": "none"}))
    with TestClient(app, base_url="http://attacker.example") as client:
        response = client.post("/mcp", json={})
    assert response.status_code in (400, 403, 421)


def test_insecure_configurations_refuse_to_start(settings: Settings) -> None:
    exposed = HttpConfig.from_env(
        {"VOICE_CREATOR_AUTH": "none", "VOICE_CREATOR_HTTP_HOST": "0.0.0.0"}
    )
    with pytest.raises(ConfigError, match="non-loopback"):
        build_http_app(settings, exposed)
    with pytest.raises(ConfigError, match="OAuth"):
        build_http_app(settings, HttpConfig.from_env({}))
