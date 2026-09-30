"""Drive a Gemini 3.8 Live session turn by turn and collect the spoken audio.

Turn semantics differ between the two 3.8 Live models. With Extended Thinking the
model can finish a spoken turn (`turn_complete`) while it is still reasoning or
running tools in the background (`interaction_status == IN_PROGRESS`) and then
speak again. A turn is therefore only finished once the status is no longer
IN_PROGRESS, which also works for the standard model where the status may be absent.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
from contextlib import AbstractAsyncContextManager, AsyncExitStack
from dataclasses import dataclass, field
from typing import Any, Protocol

from voice_creator_mcp.audio.pcm import PcmAudio, concatenate, decode_audio_payload
from voice_creator_mcp.errors import LiveConnectionError, LiveSessionError
from voice_creator_mcp.live.config import LiveSessionOptions, build_live_config

IN_PROGRESS = "IN_PROGRESS"

TurnCallback = Callable[[int, int], Awaitable[None]]
"""Awaited with (turns done, total turns) after each completed turn."""


@dataclass(frozen=True)
class LiveEvent:
    """SDK-independent view of one piece of a Live server message."""

    audio: bytes | None = None
    audio_mime_type: str | None = None
    output_transcript: str | None = None
    turn_complete: bool = False
    interaction_status: str | None = None
    interrupted: bool = False
    go_away: bool = False


class LiveSession(Protocol):
    @property
    def consent_signature(self) -> str | None: ...

    async def send_text(self, text: str) -> None: ...

    def receive(self) -> AsyncIterator[LiveEvent]:
        """Yield events until the server ends the current turn."""
        ...


class LiveConnector(Protocol):
    def connect(
        self, model: str, config: dict[str, Any]
    ) -> AbstractAsyncContextManager[LiveSession]: ...


@dataclass(frozen=True)
class LiveTurn:
    prompt: str
    transcript: str
    audio_seconds: float
    interrupted: bool


@dataclass(frozen=True)
class LiveDialogueResult:
    audio: PcmAudio
    turns: tuple[LiveTurn, ...]
    consent_signature: str | None
    warnings: tuple[str, ...]

    def transcript(self) -> str:
        return "\n".join(turn.transcript for turn in self.turns if turn.transcript)


@dataclass
class _TurnBuffer:
    clips: list[PcmAudio] = field(default_factory=list)
    transcript: list[str] = field(default_factory=list)
    interrupted: bool = False
    go_away: bool = False


class LiveDialogueRunner:
    def __init__(
        self,
        connector: LiveConnector,
        *,
        turn_timeout_s: float = 120.0,
        max_receive_passes: int = 50,
    ) -> None:
        self._connector = connector
        self._turn_timeout_s = turn_timeout_s
        self._max_receive_passes = max_receive_passes

    async def run(
        self,
        options: LiveSessionOptions,
        prompts: Sequence[str],
        *,
        gap_ms: int = 400,
        on_turn: TurnCallback | None = None,
    ) -> LiveDialogueResult:
        prompts = [prompt.strip() for prompt in prompts]
        if not prompts or not all(prompts):
            raise ValueError("At least one non-empty prompt is required")
        config = build_live_config(options)

        async with AsyncExitStack() as stack:
            try:
                session = await stack.enter_async_context(
                    self._connector.connect(options.model, config)
                )
            except Exception as exc:
                raise LiveConnectionError(f"Could not open a Live session: {exc}") from exc
            try:
                turns, buffers = await self._run_turns(session, prompts, on_turn)
            except LiveSessionError:
                raise
            except Exception as exc:
                raise LiveSessionError(f"Live session failed: {exc}") from exc
            signature = session.consent_signature

        return _assemble(turns, buffers, signature, gap_ms)

    async def _run_turns(
        self, session: LiveSession, prompts: list[str], on_turn: TurnCallback | None
    ) -> tuple[list[LiveTurn], list[_TurnBuffer]]:
        turns, buffers = [], []
        for number, prompt in enumerate(prompts, start=1):
            await session.send_text(prompt)
            try:
                buffer = await asyncio.wait_for(
                    self._collect_turn(session, number), self._turn_timeout_s
                )
            except TimeoutError as exc:
                raise LiveSessionError(
                    f"Live turn {number} timed out after {self._turn_timeout_s:g}s"
                ) from exc
            buffers.append(buffer)
            turns.append(
                LiveTurn(
                    prompt=prompt,
                    transcript="".join(buffer.transcript).strip(),
                    audio_seconds=round(sum(c.duration_seconds for c in buffer.clips), 3),
                    interrupted=buffer.interrupted,
                )
            )
            if on_turn:
                await on_turn(number, len(prompts))
        return turns, buffers

    async def _collect_turn(self, session: LiveSession, number: int) -> _TurnBuffer:
        buffer = _TurnBuffer()
        for _ in range(self._max_receive_passes):
            status: str | None = None
            async for event in session.receive():
                _apply(buffer, event)
                if event.turn_complete or event.interaction_status:
                    status = event.interaction_status
            if status != IN_PROGRESS:
                return buffer
        raise LiveSessionError(
            f"Live turn {number} did not reach IDLE after {self._max_receive_passes} passes"
        )


def _apply(buffer: _TurnBuffer, event: LiveEvent) -> None:
    if event.audio:
        buffer.clips.append(decode_audio_payload(event.audio, event.audio_mime_type))
    if event.output_transcript:
        buffer.transcript.append(event.output_transcript)
    buffer.interrupted = buffer.interrupted or event.interrupted
    buffer.go_away = buffer.go_away or event.go_away


def _assemble(
    turns: list[LiveTurn], buffers: list[_TurnBuffer], signature: str | None, gap_ms: int
) -> LiveDialogueResult:
    warnings = []
    for number, buffer in enumerate(buffers, start=1):
        if not buffer.clips:
            warnings.append(f"turn {number} returned no audio (the model may have stayed silent)")
        if buffer.interrupted:
            warnings.append(f"turn {number} was interrupted; its audio may be cut short")
        if buffer.go_away:
            warnings.append(f"turn {number}: server sent GoAway; the session was about to close")

    clips = [clip for buffer in buffers for clip in buffer.clips]
    if not clips:
        raise LiveSessionError("The Live session returned no audio")
    turn_audio = [concatenate(buffer.clips) for buffer in buffers if buffer.clips]
    return LiveDialogueResult(
        audio=concatenate(turn_audio, gap_ms=gap_ms),
        turns=tuple(turns),
        consent_signature=signature,
        warnings=tuple(warnings),
    )
