"""Server-to-server realtime relay over WebSocket (`/v1/live`)."""

from pathlib import Path

import pytest
from mcp.server.auth.provider import AccessToken
from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from voice_creator.config import Settings
from voice_creator.live.runner import LiveEvent
from voice_creator.studio.voice_library import VoiceLibrary
from voice_creator.voices.profiles import ProfileStore
from voice_creator_mcp.auth.config import HttpConfig
from voice_creator_mcp.auth.principal import Principal
from voice_creator_mcp.server.http import build_http_app

from conftest import make_tone, wav_bytes
from fakes import FakeBackend, FakeRealtimeConnector, FakeRealtimeSession, audio_event

ISSUER = "https://auth.example.com"
OAUTH_ENV = {
    "VOICE_CREATOR_OAUTH_ISSUER": ISSUER,
    "VOICE_CREATOR_RESOURCE_URL": "https://voice.example.com/mcp",
    "VOICE_CREATOR_OAUTH_JWKS_URL": "https://auth.example.com/jwks",
}
TOKENS = {"reader": "voice:read", "speaker": "voice:read dialogue:generate"}


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


@pytest.fixture
def session() -> FakeRealtimeSession:
    return FakeRealtimeSession(
        batches=[
            [audio_event(0.1), LiveEvent(output_transcript="Hola"), LiveEvent(turn_complete=True)]
        ]
    )


@pytest.fixture
def client(tmp_path: Path, session: FakeRealtimeSession) -> TestClient:
    backend = FakeBackend(connector=FakeRealtimeConnector([session]))
    principal = Principal(issuer=ISSUER, subject="alice", client_id="app", scopes=frozenset())
    VoiceLibrary(
        ProfileStore(tmp_path / "principals" / principal.workspace_id), backend
    ).register_profile(
        "me",
        display_name="Alice",
        sample_wav=wav_bytes(make_tone(15.0)),
        consent_wav=wav_bytes(make_tone(6.0)),
    )
    settings = Settings.from_env({"VOICE_CREATOR_DATA_DIR": str(tmp_path)}, home=tmp_path)
    app = build_http_app(
        settings, HttpConfig.from_env(OAUTH_ENV), verifier=Verifier(), backend=backend
    )
    return TestClient(app, base_url="https://voice.example.com")


def connect(client: TestClient, token: str | None):
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    return client.websocket_connect("/v1/live", headers=headers)


@pytest.mark.parametrize("token", [None, "forged", "reader"])
def test_handshake_requires_the_dialogue_scope(client: TestClient, token: str | None) -> None:
    with pytest.raises(WebSocketDisconnect) as closed, connect(client, token):
        pass
    assert closed.value.code == 1008


def test_relays_a_conversation(client: TestClient, session: FakeRealtimeSession) -> None:
    with connect(client, "speaker") as ws:
        ws.send_json({"type": "start", "profile": "me", "turn_detection": "manual"})
        started = ws.receive_json()
        ws.send_json({"type": "activity_start"})
        ws.send_bytes(b"\x00\x01" * 320)
        ws.send_json({"type": "activity_end"})
        audio = ws.receive_bytes()
        transcript = ws.receive_json()
        turn = ws.receive_json()
        ws.send_json({"type": "stop"})
        ended = ws.receive_json()

    assert started["type"] == "session"
    assert started["input_sample_rate"] == 16_000
    assert len(audio) == 2 * 2_400
    assert transcript == {"type": "output_transcript", "text": "Hola"}
    assert turn["type"] == "turn_complete"
    assert ended["type"] == "session_end"
    assert ("audio", b"\x00\x01" * 320, 16_000) in session.sent


def test_invalid_start_message_is_rejected(client: TestClient) -> None:
    with connect(client, "speaker") as ws:
        ws.send_json({"type": "start", "profile": "Not Valid"})
        error = ws.receive_json()
        with pytest.raises(WebSocketDisconnect) as closed:
            ws.receive_json()
    assert error["type"] == "error"
    assert closed.value.code == 1008


def test_unknown_profile_is_reported(client: TestClient) -> None:
    with connect(client, "speaker") as ws:
        ws.send_json({"type": "start", "profile": "ghost"})
        error = ws.receive_json()
    assert error == {
        "type": "error",
        "error": "not_found",
        "description": "Voice profile 'ghost' not found",
    }


def test_cross_site_websocket_hijacking_is_blocked(client: TestClient) -> None:
    headers = {"Authorization": "Bearer speaker", "Origin": "https://evil.example"}
    with (
        pytest.raises(WebSocketDisconnect) as closed,
        client.websocket_connect("/v1/live", headers=headers),
    ):
        pass
    assert closed.value.code == 1008
