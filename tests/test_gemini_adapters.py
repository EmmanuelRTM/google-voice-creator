"""Adapters are tested against duck-typed fakes that mirror google-genai 2.25 shapes."""

import base64
from contextlib import asynccontextmanager
from types import SimpleNamespace as NS
from typing import Any

import pytest

from voice_creator.errors import SynthesisError
from voice_creator.gemini.live_gateway import GeminiLiveConnector, message_to_events
from voice_creator.gemini.tts_gateway import GeminiTtsSynthesizer
from voice_creator.gemini.voices_gateway import GeminiVoicesGateway
from voice_creator.live.runner import LiveEvent

from conftest import make_tone, wav_bytes

# --- Voices ---------------------------------------------------------------


class FakeVoiceModel:
    def __init__(self, **fields: Any) -> None:
        self._fields = fields

    def model_dump(self, **kwargs: Any) -> dict[str, Any]:
        assert kwargs == {"mode": "json", "exclude_none": True}
        return dict(self._fields)


class FakeVoicesResource:
    def __init__(self) -> None:
        self.calls: list[tuple[str, Any]] = []

    def create(self, *, voice: dict, store: bool) -> FakeVoiceModel:
        self.calls.append(("create", {"voice": voice, "store": store}))
        return FakeVoiceModel(id="voice_1", type="replicated")

    def list(self, **kwargs: Any) -> FakeVoiceModel:
        self.calls.append(("list", kwargs))
        return FakeVoiceModel(voices=[{"id": "Puck", "type": "prebuilt"}])

    def get(self, voice_id: str) -> FakeVoiceModel:
        self.calls.append(("get", voice_id))
        return FakeVoiceModel(id=voice_id, type="prompted")

    def delete(self, voice_id: str) -> None:
        self.calls.append(("delete", voice_id))


def test_voices_gateway_maps_calls_onto_the_sdk() -> None:
    resource = FakeVoicesResource()
    gateway = GeminiVoicesGateway(NS(voices=resource))

    assert gateway.create({"type": "prompted"}, store=True) == {
        "id": "voice_1",
        "type": "replicated",
    }
    assert gateway.list(types=["prebuilt"], search=None, page_size=5, page_token=None) == {
        "voices": [{"id": "Puck", "type": "prebuilt"}]
    }
    assert gateway.get("voice_1")["id"] == "voice_1"
    gateway.delete("voice_1")

    assert resource.calls[1] == (
        "list",
        {"type_": ["prebuilt"], "search": None, "page_size": 5, "page_token": None},
    )
    assert resource.calls[-1] == ("delete", "voice_1")


# --- TTS ------------------------------------------------------------------


class FakeInteractions:
    def __init__(self, output_audio: Any) -> None:
        self.output_audio = output_audio
        self.requests: list[dict[str, Any]] = []

    def create(self, **request: Any) -> Any:
        self.requests.append(request)
        return NS(output_audio=self.output_audio)


def test_tts_sends_style_as_speech_metadata_annotation() -> None:
    clip = make_tone(0.2)
    interactions = FakeInteractions(
        NS(
            data=base64.b64encode(wav_bytes(clip)).decode(),
            mime_type="audio/wav",
            sample_rate=24_000,
        )
    )
    synth = GeminiTtsSynthesizer(NS(interactions=interactions), model="gemini-3.8-flash-tts")

    audio = synth.synthesize("¡Hola, amigo!", voice="voice_1", style="warm", language="es-MX")

    assert audio == clip
    request = interactions.requests[0]
    assert request["model"] == "gemini-3.8-flash-tts"
    assert request["response_modalities"] == ["audio"]
    assert request["generation_config"] == {
        "speech_config": [{"voice": "voice_1", "language": "es-MX"}]
    }
    assert request["response_format"] == {"type": "audio", "mime_type": "audio/wav"}
    (content,) = request["input"]
    assert content["text"] == "¡Hola, amigo!"
    assert content["annotations"] == [
        {
            "type": "speech_metadata",
            "style": "warm",
            "start_index": 0,
            "end_index": len("¡Hola, amigo!".encode()),
        }
    ]


def test_tts_without_style_sends_plain_text() -> None:
    clip = make_tone(0.1)
    interactions = FakeInteractions(
        NS(data=base64.b64encode(clip.data).decode(), mime_type="audio/l16", sample_rate=24_000)
    )
    synth = GeminiTtsSynthesizer(NS(interactions=interactions), model="m")

    audio = synth.synthesize("Hi", voice="Kore", style=None, language=None)

    assert audio == clip
    assert interactions.requests[0]["input"] == [{"type": "text", "text": "Hi"}]
    assert interactions.requests[0]["generation_config"] == {"speech_config": [{"voice": "Kore"}]}


def test_tts_without_audio_raises() -> None:
    synth = GeminiTtsSynthesizer(NS(interactions=FakeInteractions(None)), model="m")
    with pytest.raises(SynthesisError, match="no audio"):
        synth.synthesize("Hi", voice="Kore", style=None, language=None)


# --- Live -----------------------------------------------------------------


def server_message(**server_content: Any) -> Any:
    defaults = dict(
        model_turn=None,
        input_transcription=None,
        output_transcription=None,
        interrupted=None,
        turn_complete=None,
        interaction_status=None,
    )
    defaults.update(server_content)
    return NS(server_content=NS(**defaults), go_away=None)


def test_message_to_events_maps_audio_transcript_and_status() -> None:
    part = NS(inline_data=NS(data=b"\x01\x00", mime_type="audio/pcm;rate=24000"))
    message = server_message(
        model_turn=NS(parts=[part, NS(inline_data=None)]),
        output_transcription=NS(text="Hola"),
        turn_complete=True,
        interaction_status=NS(value="IDLE"),
    )

    assert message_to_events(message) == [
        LiveEvent(audio=b"\x01\x00", audio_mime_type="audio/pcm;rate=24000"),
        LiveEvent(output_transcript="Hola"),
        LiveEvent(turn_complete=True, interaction_status="IDLE"),
    ]


def test_message_to_events_maps_interruptions_and_go_away() -> None:
    message = server_message(interrupted=True)
    message.go_away = NS(time_left="5s")
    assert message_to_events(message) == [LiveEvent(interrupted=True), LiveEvent(go_away=True)]


def test_message_without_server_content_yields_nothing() -> None:
    assert message_to_events(NS(server_content=None, go_away=None)) == []


class FakeSdkSession:
    def __init__(self) -> None:
        self.sent: list[str] = []
        self.setup_complete = NS(voice_consent_signature=NS(signature="sig-1"))

    async def send_realtime_input(self, *, text: str) -> None:
        self.sent.append(text)

    async def receive(self):
        yield server_message(turn_complete=True)


async def test_live_connector_wraps_the_sdk_session() -> None:
    sdk_session = FakeSdkSession()
    captured: dict[str, Any] = {}

    @asynccontextmanager
    async def connect(*, model: str, config: dict[str, Any]):
        captured.update(model=model, config=config)
        yield sdk_session

    client = NS(aio=NS(live=NS(connect=connect)))

    async with GeminiLiveConnector(client).connect("gemini-3.8-live", {"a": 1}) as session:
        assert session.consent_signature == "sig-1"
        await session.send_text("hello")
        events = [event async for event in session.receive()]

    assert captured == {"model": "gemini-3.8-live", "config": {"a": 1}}
    assert sdk_session.sent == ["hello"]
    assert events == [LiveEvent(turn_complete=True)]


def test_message_to_events_maps_input_transcripts_and_resumption() -> None:
    message = server_message(input_transcription=NS(text="Hola"))
    message.session_resumption_update = NS(new_handle="h-2", resumable=True)

    assert message_to_events(message) == [
        LiveEvent(input_transcript="Hola"),
        LiveEvent(resumption_handle="h-2"),
    ]


def test_non_resumable_updates_are_ignored() -> None:
    message = NS(
        server_content=None,
        go_away=None,
        session_resumption_update=NS(new_handle="", resumable=False),
    )
    assert message_to_events(message) == []


class RealtimeSdkSession(FakeSdkSession):
    def __init__(self) -> None:
        super().__init__()
        self.realtime: list[dict] = []

    async def send_realtime_input(self, **kwargs) -> None:  # type: ignore[override]
        self.realtime.append(kwargs)


async def test_live_session_streams_audio_and_activity_signals() -> None:
    sdk_session = RealtimeSdkSession()

    @asynccontextmanager
    async def connect(*, model: str, config: dict[str, Any]):
        yield sdk_session

    async with GeminiLiveConnector(NS(aio=NS(live=NS(connect=connect)))).connect("m", {}) as s:
        await s.send_audio(b"\x00\x01", 16_000)
        await s.send_activity_start()
        await s.send_activity_end()
        await s.send_audio_stream_end()

    audio = sdk_session.realtime[0]["audio"]
    assert (audio.data, audio.mime_type) == (b"\x00\x01", "audio/pcm;rate=16000")
    assert sdk_session.realtime[1:] == [
        {"activity_start": {}},
        {"activity_end": {}},
        {"audio_stream_end": True},
    ]


def test_token_connector_uses_the_ephemeral_token_on_v1alpha() -> None:
    from voice_creator.gemini.live_gateway import GeminiLiveConnector, connector_for_token
    from voice_creator.live.tokens import LiveToken

    token = LiveToken(
        token="auth_tokens/abc",
        model="gemini-3.8-live",
        api_version="v1alpha",
        uses=1,
        expire_time="2026-09-30T12:30:00+00:00",
        new_session_expire_time="2026-09-30T12:01:00+00:00",
    )

    connector = connector_for_token(token)

    assert isinstance(connector, GeminiLiveConnector)
    api_client = connector._live._api_client
    assert api_client.api_key == "auth_tokens/abc"
    assert api_client._http_options.api_version == "v1alpha"
