"""Write rendered audio and transcripts to the output directory, never overwriting."""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from voice_creator.audio.pcm import PcmAudio, write_wav

_UNSAFE = re.compile(r"[^A-Za-z0-9_-]+")
_MAX_STEM = 80


@dataclass(frozen=True)
class WrittenOutput:
    audio_path: Path
    transcript_path: Path


class OutputWriter:
    def __init__(self, directory: Path, *, clock: Callable[[], datetime] | None = None) -> None:
        self._directory = Path(directory)
        self._clock = clock or (lambda: datetime.now(UTC))

    def write(
        self, name: str | None, audio: PcmAudio, transcript: str, *, prefix: str = "render"
    ) -> WrittenOutput:
        stem = self._stem(name, prefix)
        audio_path, transcript_path = self._free_paths(stem)
        write_wav(audio_path, audio)
        transcript_path.write_text(transcript.rstrip("\n") + "\n", encoding="utf-8")
        return WrittenOutput(audio_path=audio_path, transcript_path=transcript_path)

    def _stem(self, name: str | None, prefix: str) -> str:
        if name is None:
            return f"{prefix}-{self._clock().strftime('%Y%m%dT%H%M%SZ')}"
        stem = _UNSAFE.sub("_", name).strip("_-")[:_MAX_STEM]
        if not stem:
            raise ValueError(f"output_name {name!r} has no usable characters")
        return stem

    def _free_paths(self, stem: str) -> tuple[Path, Path]:
        self._directory.mkdir(parents=True, exist_ok=True)
        candidate, counter = stem, 1
        while (self._directory / f"{candidate}.wav").exists():
            counter += 1
            candidate = f"{stem}-{counter}"
        return self._directory / f"{candidate}.wav", self._directory / f"{candidate}.txt"
