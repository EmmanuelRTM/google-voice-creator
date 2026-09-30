"""In-memory test doubles for the ports the application layer depends on."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any

from voice_creator.audio.pcm import PcmAudio
from voice_creator.live.runner import LiveEvent
from voice_creator.voices.catalog import VoiceCatalog

from conftest import make_tone

PCM_MIME = "audio/pcm;rate=24000"


def audio_event(seconds: float) -> LiveEvent:
    return LiveEvent(audio=make_tone(seconds).data, audio_mime_type=PCM_MIME)


@dataclass
class FakeVoicesGateway:
    create_response: dict[str, Any] = field(default_factory=dict)
    list_response: dict[str, Any] = field(default_factory=dict)
    calls: list[tuple[str, Any]] = field(default_factory=list)

    def create(self, voice: dict[str, Any], store: bool) -> dict[str, Any]:
        self.calls.append(("create", {"voice": voice, "store": store}))
        return self.create_response

    def list(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(("list", kwargs))
        return self.list_response

    def get(self, voice_id: str) -> dict[str, Any]:
        self.calls.append(("get", voice_id))
        return {"id": voice_id, "type": "prompted", "sample_audio": {"data": "AAAA"}}

    def delete(self, voice_id: str) -> None:
        self.calls.append(("delete", voice_id))


@dataclass
class FakeSynthesizer:
    """Returns 0.1 s of tone per word so segment timings are predictable."""

    sample_rate: int = 24_000
    calls: list[dict[str, Any]] = field(default_factory=list)

    def synthesize(
        self, text: str, *, voice: str, style: str | None, language: str | None
    ) -> PcmAudio:
        self.calls.append({"text": text, "voice": voice, "style": style, "language": language})
        return make_tone(0.1 * len(text.split()), sample_rate=self.sample_rate)


@dataclass
class FakeSession:
    """Plays back one scripted list of receive() passes per prompt."""

    passes_per_turn: list[list[list[LiveEvent]]]
    consent_signature: str | None = None
    sent: list[str] = field(default_factory=list)
    hang: bool = False

    async def send_text(self, text: str) -> None:
        self.sent.append(text)

    async def receive(self) -> AsyncIterator[LiveEvent]:
        if self.hang:
            await asyncio.sleep(10)
        turn = self.passes_per_turn[len(self.sent) - 1]
        for event in turn.pop(0):
            yield event


@dataclass
class FakeConnector:
    session: FakeSession
    connections: list[tuple[str, dict[str, Any]]] = field(default_factory=list)
    reject_signatures: bool = False

    @asynccontextmanager
    async def connect(self, model: str, config: dict[str, Any]):
        self.connections.append((model, config))
        replicated = (
            config.get("speech_config", {}).get("voice_config", {}).get("replicated_voice_config")
        )
        if self.reject_signatures and replicated and "voice_consent_signature" in replicated:
            raise RuntimeError("1007 invalid voice consent signature")
        yield self.session


@dataclass
class FakeTokenGateway:
    requests: list[dict[str, Any]] = field(default_factory=list)

    def create(self, config: dict[str, Any]) -> str:
        self.requests.append(config)
        return f"auth_tokens/token-{len(self.requests)}"


@dataclass
class FakeBackend:
    voices: FakeVoicesGateway = field(default_factory=FakeVoicesGateway)
    synth: FakeSynthesizer = field(default_factory=FakeSynthesizer)
    connector: FakeConnector | None = None
    tokens: FakeTokenGateway = field(default_factory=FakeTokenGateway)

    def token_gateway(self) -> FakeTokenGateway:
        return self.tokens

    def voice_catalog(self) -> VoiceCatalog:
        return VoiceCatalog(self.voices)

    def synthesizer(self) -> FakeSynthesizer:
        return self.synth

    def live_connector(self) -> FakeConnector:
        assert self.connector is not None, "configure FakeBackend.connector for Live tests"
        return self.connector


# --- Realtime ----------------------------------------------------------------------


@dataclass
class FakeRealtimeSession:
    """Each receive() call plays the next batch; with no batches left it idles until closed."""

    batches: list[list[LiveEvent]] = field(default_factory=list)
    consent_signature: str | None = None
    sent: list[tuple[Any, ...]] = field(default_factory=list)

    async def send_text(self, text: str) -> None:
        self.sent.append(("text", text))

    async def send_audio(self, pcm: bytes, sample_rate: int) -> None:
        self.sent.append(("audio", pcm, sample_rate))

    async def send_activity_start(self) -> None:
        self.sent.append(("activity_start",))

    async def send_activity_end(self) -> None:
        self.sent.append(("activity_end",))

    async def send_audio_stream_end(self) -> None:
        self.sent.append(("audio_stream_end",))

    async def receive(self) -> AsyncIterator[LiveEvent]:
        if self.batches:
            for event in self.batches.pop(0):
                yield event
            return
        await asyncio.Event().wait()  # idle: nothing more from the server


@dataclass
class FakeRealtimeConnector:
    sessions: list[FakeRealtimeSession]
    connections: list[tuple[str, dict[str, Any]]] = field(default_factory=list)
    reject_signatures: bool = False

    @asynccontextmanager
    async def connect(self, model: str, config: dict[str, Any]):
        self.connections.append((model, config))
        replicated = (
            config.get("speech_config", {}).get("voice_config", {}).get("replicated_voice_config")
        )
        if self.reject_signatures and replicated and "voice_consent_signature" in replicated:
            raise RuntimeError("invalid voice consent signature")
        yield self.sessions[min(len(self.connections), len(self.sessions)) - 1]


class FakeClientChannel:
    """Plays `messages`; afterwards waits until an event matching `close_when` arrives."""

    def __init__(self, messages: list[Any] | None = None, close_when: Any = None) -> None:
        self._messages = list(messages or [])
        self._close_when = close_when
        self._closed = asyncio.Event()
        self.events: list[Any] = []

    async def receive(self) -> Any:
        if self._messages:
            await asyncio.sleep(0)
            return self._messages.pop(0)
        await self._closed.wait()
        return None

    async def send(self, event: Any) -> None:
        self.events.append(event)
        if self._close_when and self._close_when(event):
            self._closed.set()

    def kinds(self) -> list[str]:
        return [event.kind for event in self.events]
