"""A scripted `ClientChannel`: plays recorded input and waits for the spoken reply."""

from __future__ import annotations

import asyncio
from collections import deque
from collections.abc import Iterable

from voice_creator.audio.convert import chunk_pcm, to_live_input
from voice_creator.audio.pcm import PcmAudio, silence
from voice_creator.live.realtime import ClientMessage, ServerEvent


class ScriptedChannel:
    def __init__(
        self,
        messages: Iterable[ClientMessage],
        *,
        reply_timeout_s: float = 60.0,
        pace_s: float = 0.0,
    ) -> None:
        self._pending = deque(messages)
        self._reply_timeout_s = reply_timeout_s
        self._pace_s = pace_s
        self._finished = asyncio.Event()
        self.events: list[ServerEvent] = []
        self.timed_out = False

    @classmethod
    def speaking(cls, recording: PcmAudio, **options: float) -> ScriptedChannel:
        """Stream `recording` as 16 kHz chunks, a second of silence, then end the stream."""
        audio = to_live_input(recording)
        tail = silence(1000, sample_rate=audio.sample_rate)
        messages = [
            ClientMessage("audio", audio=chunk) for chunk in (*chunk_pcm(audio), *chunk_pcm(tail))
        ]
        return cls([*messages, ClientMessage("audio_stream_end")], **options)

    async def receive(self) -> ClientMessage | None:
        if self._pending:
            await asyncio.sleep(self._pace_s)
            return self._pending.popleft()
        try:
            async with asyncio.timeout(self._reply_timeout_s):
                await self._finished.wait()
        except TimeoutError:
            self.timed_out = True
        return None

    async def send(self, event: ServerEvent) -> None:
        self.events.append(event)
        replied = any(e.kind == "audio" for e in self.events)
        if event.kind == "error" or (event.kind == "turn_complete" and replied):
            self._finished.set()

    def of_kind(self, kind: str) -> list[ServerEvent]:
        return [event for event in self.events if event.kind == kind]
