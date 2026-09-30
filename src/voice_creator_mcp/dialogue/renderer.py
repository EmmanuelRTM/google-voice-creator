"""Render a multi-speaker dialogue line by line and stitch it into one track.

Line-by-line synthesis works for any number of speakers and any voice type
(prebuilt, designed or replicated), unlike single-request two-speaker TTS.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Protocol

from voice_creator_mcp.audio.pcm import PcmAudio, concatenate
from voice_creator_mcp.dialogue.script import DialogueLine, speakers, strip_vocal_tags
from voice_creator_mcp.errors import CastError

MAX_GAP_MS = 10_000

ProgressCallback = Callable[[int, int], None]
"""Called with (lines done, total lines) after each synthesized line."""


class SpeechSynthesizer(Protocol):
    def synthesize(
        self, text: str, *, voice: str, style: str | None, language: str | None
    ) -> PcmAudio: ...


@dataclass(frozen=True)
class RenderedSegment:
    index: int
    speaker: str
    text: str
    style: str | None
    start_seconds: float
    end_seconds: float


@dataclass(frozen=True)
class RenderedDialogue:
    audio: PcmAudio
    segments: tuple[RenderedSegment, ...]

    def transcript(self) -> str:
        return "\n".join(
            f"{segment.speaker}: {strip_vocal_tags(segment.text)}" for segment in self.segments
        )


class DialogueRenderer:
    def __init__(self, synthesizer: SpeechSynthesizer) -> None:
        self._synthesizer = synthesizer

    def render(
        self,
        lines: Sequence[DialogueLine],
        cast: Mapping[str, str],
        *,
        gap_ms: int = 300,
        language: str | None = None,
        on_progress: ProgressCallback | None = None,
    ) -> RenderedDialogue:
        if not lines:
            raise CastError("Dialogue has no lines to render")
        if not 0 <= gap_ms <= MAX_GAP_MS:
            raise ValueError(f"gap_ms must be between 0 and {MAX_GAP_MS}")
        voices = _resolve_cast(lines, cast)

        clips = []
        for done, line in enumerate(lines, start=1):
            clips.append(
                self._synthesizer.synthesize(
                    line.text, voice=voices[line.speaker], style=line.style, language=language
                )
            )
            if on_progress:
                on_progress(done, len(lines))
        audio = concatenate(clips, gap_ms=gap_ms)
        return RenderedDialogue(audio=audio, segments=_segments(lines, clips, gap_ms))


def _resolve_cast(lines: Sequence[DialogueLine], cast: Mapping[str, str]) -> dict[str, str]:
    by_name = {name.casefold(): voice for name, voice in cast.items()}
    resolved = {name: by_name.get(name.casefold()) for name in speakers(lines)}
    missing = [name for name, voice in resolved.items() if not voice]
    if missing:
        raise CastError(
            f"No voice cast for speaker(s): {', '.join(missing)}. "
            "Map each speaker to a voice in `cast`."
        )
    return {name: voice for name, voice in resolved.items() if voice}


def _segments(
    lines: Sequence[DialogueLine], clips: Sequence[PcmAudio], gap_ms: int
) -> tuple[RenderedSegment, ...]:
    segments = []
    cursor = 0.0
    for index, (line, clip) in enumerate(zip(lines, clips, strict=True)):
        end = cursor + clip.duration_seconds
        segments.append(
            RenderedSegment(
                index, line.speaker, line.text, line.style, round(cursor, 3), round(end, 3)
            )
        )
        cursor = end + gap_ms / 1000
    return tuple(segments)
