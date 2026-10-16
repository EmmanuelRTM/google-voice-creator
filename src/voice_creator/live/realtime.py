"""Server-to-server realtime voice: relay a user's microphone to Gemini Live and back.

This is Google's recommended production architecture for mobile apps, telephony and
enterprise tools. The client streams audio to your backend, and the backend holds
the Live API connection, so the API key never leaves the server. The bridge follows
the Live API's session guidance:

* input is 16-bit PCM mono at 16 kHz, output is 24 kHz PCM;
* `interrupted` is forwarded so the client flushes buffered playback (barge-in);
* automatic VAD by default, or push-to-talk activity signals (`turn_detection`);
* session resumption handles are tracked, and on `GoAway` or a dropped connection
  the bridge reconnects with the latest handle, without the client noticing;
* sliding-window context compression lifts the ~15 minute audio session limit;
* a hard duration cap bounds cost per conversation.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import AsyncExitStack
from dataclasses import asdict, dataclass, field, replace
from typing import Any, Literal, Protocol

from voice_creator.audio.pcm import DEFAULT_SAMPLE_RATE, parse_pcm_rate
from voice_creator.errors import LiveConnectionError
from voice_creator.live.config import LiveSessionOptions, TurnDetection, build_live_config
from voice_creator.live.runner import LiveConnector, LiveEvent

logger = logging.getLogger(__name__)

INPUT_SAMPLE_RATE = 16_000
OUTPUT_SAMPLE_RATE = DEFAULT_SAMPLE_RATE
MAX_AUDIO_CHUNK_BYTES = 64 * 1024

ClientKind = Literal["audio", "text", "activity_start", "activity_end", "audio_stream_end", "stop"]
EventKind = Literal[
    "session",
    "audio",
    "input_transcript",
    "output_transcript",
    "interrupted",
    "turn_complete",
    "error",
]
Ending = Literal["client", "server_closed", "time_limit"]


@dataclass(frozen=True)
class ClientMessage:
    kind: ClientKind
    audio: bytes = b""
    text: str = ""


@dataclass(frozen=True)
class ServerEvent:
    kind: EventKind
    audio: bytes = b""
    data: dict[str, Any] = field(default_factory=dict)


class ClientChannel(Protocol):
    """The user's side: a WebSocket, a telephony stream, a local microphone..."""

    async def receive(self) -> ClientMessage | None:
        """The next client message, or None once the client has gone away."""
        ...

    async def send(self, event: ServerEvent) -> None: ...


class RealtimeSession(Protocol):
    @property
    def consent_signature(self) -> str | None: ...

    async def send_text(self, text: str) -> None: ...

    async def send_audio(self, pcm: bytes, sample_rate: int) -> None: ...

    async def send_activity_start(self) -> None: ...

    async def send_activity_end(self) -> None: ...

    async def send_audio_stream_end(self) -> None: ...

    def receive(self) -> AsyncIterator[LiveEvent]: ...


@dataclass(frozen=True)
class RealtimeSummary:
    ended: Ending
    reconnects: int
    consent_signature: str | None

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data.pop("consent_signature")
        return data


class RealtimeBridge:
    def __init__(
        self,
        connector: LiveConnector,
        *,
        max_duration_s: float = 15 * 60,
        max_reconnects: int = 3,
    ) -> None:
        self._connector = connector
        self._max_duration_s = max_duration_s
        self._max_reconnects = max_reconnects

    async def run(self, options: LiveSessionOptions, client: ClientChannel) -> RealtimeSummary:
        """Relay until the client stops, the server ends the session, or time runs out.

        Raises LiveConnectionError only when the first connection fails, so callers
        can retry (for example with the consent recording instead of a signature).
        """
        options = replace(options, resumable=True)
        state = _State(handle=options.resumption_handle)
        try:
            async with asyncio.timeout(self._max_duration_s):
                return await self._relay(options, client, state)
        except TimeoutError:
            await client.send(_error("time_limit", "The conversation reached its time limit"))
            return state.summary("time_limit")

    async def _relay(
        self, options: LiveSessionOptions, client: ClientChannel, state: _State
    ) -> RealtimeSummary:
        while True:
            resumed = state.handle is not None and (
                state.connected or bool(options.resumption_handle)
            )
            opts = replace(options, resumption_handle=state.handle)
            async with AsyncExitStack() as stack:
                try:
                    session = await stack.enter_async_context(
                        self._connector.connect(opts.model, build_live_config(opts))
                    )
                except Exception as exc:
                    if not state.connected:
                        raise LiveConnectionError(f"Could not open a Live session: {exc}") from exc
                    await client.send(_error("reconnect_failed", "Lost the Live session"))
                    return state.summary("server_closed")
                state.connected = True
                state.signature = session.consent_signature or state.signature
                await client.send(
                    ServerEvent(
                        "session",
                        data={
                            "model": opts.model,
                            "resumed": resumed,
                            "input_sample_rate": INPUT_SAMPLE_RATE,
                            "output_sample_rate": OUTPUT_SAMPLE_RATE,
                        },
                    )
                )
                outcome = await self._pump(session, client, state, opts.turn_detection)
            if outcome == "client":
                return state.summary("client")
            if state.handle and state.reconnects < self._max_reconnects:
                state.reconnects += 1
                continue
            await client.send(_error("session_ended", "The Live session ended"))
            return state.summary("server_closed")

    async def _pump(
        self,
        session: RealtimeSession,
        client: ClientChannel,
        state: _State,
        turn_detection: TurnDetection | None,
    ) -> Literal["client", "server"]:
        upstream = asyncio.create_task(self._upstream(session, client, turn_detection))
        downstream = asyncio.create_task(self._downstream(session, client, state))
        done, pending = await asyncio.wait(
            {upstream, downstream}, return_when=asyncio.FIRST_COMPLETED
        )
        for task in pending:
            task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)
        return "client" if upstream in done else "server"

    async def _upstream(
        self, session: RealtimeSession, client: ClientChannel, turn_detection: TurnDetection | None
    ) -> None:
        while True:
            message = await client.receive()
            if message is None or message.kind == "stop":
                return
            problem = _validate(message, turn_detection)
            if problem:
                code = "invalid_audio" if message.kind == "audio" else "invalid_signal"
                await client.send(_error(code, problem))
                continue
            if message.kind == "audio":
                await session.send_audio(message.audio, INPUT_SAMPLE_RATE)
            elif message.kind == "text":
                await session.send_text(message.text)
            elif message.kind == "activity_start":
                await session.send_activity_start()
            elif message.kind == "activity_end":
                await session.send_activity_end()
            elif message.kind == "audio_stream_end":
                await session.send_audio_stream_end()

    async def _downstream(
        self, session: RealtimeSession, client: ClientChannel, state: _State
    ) -> None:
        try:
            while True:
                received = False
                async for event in session.receive():
                    received = True
                    if event.resumption_handle:
                        state.handle = event.resumption_handle
                    if event.go_away:
                        return  # reconnect with the latest handle before the server drops us
                    for outgoing in _events_for(event):
                        await client.send(outgoing)
                if not received:
                    return  # the server closed the stream
        except Exception as exc:  # a dropped connection is resumable
            logger.info("Live stream ended: %s", type(exc).__name__)


@dataclass
class _State:
    handle: str | None = None
    reconnects: int = 0
    connected: bool = False
    signature: str | None = None

    def summary(self, ended: Ending) -> RealtimeSummary:
        return RealtimeSummary(
            ended=ended, reconnects=self.reconnects, consent_signature=self.signature
        )


def _validate(message: ClientMessage, turn_detection: TurnDetection | None) -> str | None:
    # Per the Live API guide: activity signals replace automatic VAD (push-to-talk), and
    # audio_stream_end only flushes cached audio while automatic VAD is on.
    manual = turn_detection == "manual"
    if message.kind in ("activity_start", "activity_end") and not manual:
        return f"{message.kind} needs turn_detection 'manual'; automatic VAD is on"
    if message.kind == "audio_stream_end" and manual:
        return "audio_stream_end is for automatic VAD; send activity_end in manual mode"
    if message.kind != "audio":
        return None
    if not message.audio or len(message.audio) % 2:
        return "Audio must be 16-bit PCM mono at 16 kHz (an even number of bytes)"
    if len(message.audio) > MAX_AUDIO_CHUNK_BYTES:
        return f"Audio chunks must be at most {MAX_AUDIO_CHUNK_BYTES} bytes"
    return None


def _events_for(event: LiveEvent) -> list[ServerEvent]:
    events = []
    if event.input_transcript:
        events.append(ServerEvent("input_transcript", data={"text": event.input_transcript}))
    if event.audio:
        rate = parse_pcm_rate(event.audio_mime_type, default=OUTPUT_SAMPLE_RATE)
        events.append(ServerEvent("audio", audio=event.audio, data={"sample_rate": rate}))
    if event.output_transcript:
        events.append(ServerEvent("output_transcript", data={"text": event.output_transcript}))
    if event.interrupted:
        events.append(ServerEvent("interrupted"))
    if event.turn_complete or event.interaction_status:
        events.append(ServerEvent("turn_complete", data={"status": event.interaction_status}))
    return events


def _error(code: str, description: str) -> ServerEvent:
    return ServerEvent("error", data={"error": code, "description": description})
