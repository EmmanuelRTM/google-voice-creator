"""The HTTP API next to MCP: ephemeral Live tokens for browser/mobile clients."""

from pathlib import Path

import pytest
from mcp.server.auth.provider import AccessToken
from starlette.testclient import TestClient

from voice_creator.config import Settings
from voice_creator.studio.voice_library import VoiceLibrary
from voice_creator.voices.profiles import ProfileStore
from voice_creator_mcp.auth.config import HttpConfig
from voice_creator_mcp.auth.principal import Principal
from voice_creator_mcp.server.http import build_http_app

from conftest import make_tone, wav_bytes
from fakes import FakeBackend

ISSUER = "https://auth.example.com"
OAUTH_ENV = {
    "VOICE_CREATOR_OAUTH_ISSUER": ISSUER,
    "VOICE_CREATOR_RESOURCE_URL": "https://voice.example.com/mcp",
    "VOICE_CREATOR_OAUTH_JWKS_URL": "https://auth.example.com/jwks",
}
TOKENS = {
    "reader": "voice:read",
    "speaker": "voice:read dialogue:generate",
}


class Verifier:
    async def verify_token(self, token: str) -> AccessToken | None:
        if token not in TOKENS:
            return None
        return AccessToken(
            token=token,
            client_id="app",
            scopes=TOKENS[token].split(),
            subject="alice",
            claims={"iss": ISSUER},
        )


def seed_profile(data_dir: Path, backend: FakeBackend, workspace: Path | None = None) -> None:
    root = workspace or data_dir
    VoiceLibrary(ProfileStore(root), backend).register_profile(
        "me",
        display_name="Alice",
        sample_wav=wav_bytes(make_tone(15.0)),
        consent_wav=wav_bytes(make_tone(6.0)),
    )


@pytest.fixture
def backend() -> FakeBackend:
    return FakeBackend()


def oauth_client(tmp_path: Path, backend: FakeBackend, **env: str) -> TestClient:
    settings = Settings.from_env({"VOICE_CREATOR_DATA_DIR": str(tmp_path), **env}, home=tmp_path)
    principal = Principal(issuer=ISSUER, subject="alice", client_id="app", scopes=frozenset())
    seed_profile(tmp_path, backend, tmp_path / "principals" / principal.workspace_id)
    app = build_http_app(
        settings, HttpConfig.from_env(OAUTH_ENV), verifier=Verifier(), backend=backend
    )
    return TestClient(app, base_url="https://voice.example.com")


def post(client: TestClient, token: str | None, **body):
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    return client.post("/v1/live-tokens", headers=headers, json={"profile": "me", **body})


def test_requires_authentication(tmp_path: Path, backend: FakeBackend) -> None:
    response = post(oauth_client(tmp_path, backend), None)
    assert response.status_code == 401


def test_requires_the_dialogue_scope_with_a_step_up_challenge(tmp_path, backend) -> None:
    response = post(oauth_client(tmp_path, backend), "reader")

    assert response.status_code == 403
    assert 'scope="dialogue:generate voice:read"' in response.headers["www-authenticate"]


def test_issues_a_voice_locked_token(tmp_path: Path, backend: FakeBackend) -> None:
    response = post(oauth_client(tmp_path, backend), "speaker", mode="verbatim", ttl_minutes=5)

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    body = response.json()
    assert body["token"].startswith("auth_tokens/")
    assert body["api_version"] == "v1alpha"
    config = backend.tokens.requests[0]["live_connect_constraints"]["config"]
    assert "replicated_voice_config" in config["speech_config"]["voice_config"]


@pytest.mark.parametrize(
    ("body", "status"),
    [({"uses": 99}, 400), ({"profile": "ghost"}, 404), ({"unexpected": 1}, 400)],
)
def test_bad_requests(tmp_path: Path, backend: FakeBackend, body: dict, status: int) -> None:
    assert post(oauth_client(tmp_path, backend), "speaker", **body).status_code == status


def test_rate_limited(tmp_path: Path, backend: FakeBackend) -> None:
    client = oauth_client(tmp_path, backend, VOICE_CREATOR_RATE_LIMIT_PER_MINUTE="1")

    assert post(client, "speaker").status_code == 200
    limited = post(client, "speaker")

    assert limited.status_code == 429
    assert int(limited.headers["retry-after"]) > 0


def test_loopback_without_auth_uses_the_local_workspace(tmp_path: Path, backend) -> None:
    settings = Settings.from_env({"VOICE_CREATOR_DATA_DIR": str(tmp_path)}, home=tmp_path)
    seed_profile(tmp_path, backend)
    app = build_http_app(
        settings, HttpConfig.from_env({"VOICE_CREATOR_AUTH": "none"}), backend=backend
    )

    response = TestClient(app, base_url="http://127.0.0.1:8765").post(
        "/v1/live-tokens", json={"profile": "me"}
    )

    assert response.status_code == 200
