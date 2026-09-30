"""Uncompressed PCM audio: reading, writing, joining and decoding API payloads.

Only the standard library is used so the server has no native audio dependencies.
Gemini returns 16-bit little-endian PCM (24 kHz for TTS and Live output); that is
also what the Live API expects for a replicated voice sample.
"""

from __future__ import annotations

import io
import re
import wave
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from voice_creator.errors import AudioFormatError

DEFAULT_SAMPLE_RATE = 24_000
_RAW_PCM_MIME_TYPES = ("audio/pcm", "audio/l16", "audio/x-raw")
_WAV_MIME_TYPES = ("audio/wav", "audio/x-wav", "audio/wave")
_RATE_PATTERN = re.compile(r"rate\s*=\s*(\d+)", re.IGNORECASE)


@dataclass(frozen=True)
class PcmAudio:
    data: bytes
    sample_rate: int
    channels: int = 1
    sample_width: int = 2

    def __post_init__(self) -> None:
        for name in ("sample_rate", "channels", "sample_width"):
            if getattr(self, name) <= 0:
                raise AudioFormatError(f"{name} must be positive")
        if len(self.data) % self.frame_size:
            raise AudioFormatError("PCM data must contain whole frames")

    @property
    def frame_size(self) -> int:
        return self.channels * self.sample_width

    @property
    def frame_count(self) -> int:
        return len(self.data) // self.frame_size

    @property
    def duration_seconds(self) -> float:
        return self.frame_count / self.sample_rate

    def same_format(self, other: PcmAudio) -> bool:
        return (self.sample_rate, self.channels, self.sample_width) == (
            other.sample_rate,
            other.channels,
            other.sample_width,
        )


def read_wav(path: Path) -> PcmAudio:
    path = Path(path)
    if not path.is_file():
        raise AudioFormatError(f"Audio file not found: {path}")
    return read_wav_bytes(path.read_bytes(), source=str(path))


def read_wav_bytes(data: bytes, *, source: str = "audio") -> PcmAudio:
    try:
        with wave.open(io.BytesIO(data), "rb") as reader:
            return PcmAudio(
                data=reader.readframes(reader.getnframes()),
                sample_rate=reader.getframerate(),
                channels=reader.getnchannels(),
                sample_width=reader.getsampwidth(),
            )
    except (wave.Error, EOFError) as exc:
        raise AudioFormatError(
            f"{source} is not an uncompressed PCM WAV file ({exc}). Convert it first, e.g. "
            "`ffmpeg -i input -ac 1 -ar 24000 -sample_fmt s16 output.wav`."
        ) from exc


def to_wav_bytes(audio: PcmAudio) -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as writer:
        writer.setnchannels(audio.channels)
        writer.setsampwidth(audio.sample_width)
        writer.setframerate(audio.sample_rate)
        writer.writeframes(audio.data)
    return buffer.getvalue()


def write_wav(path: Path, audio: PcmAudio) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(to_wav_bytes(audio))
    return path


def silence(
    duration_ms: int, *, sample_rate: int, channels: int = 1, sample_width: int = 2
) -> PcmAudio:
    frames = int(sample_rate * duration_ms / 1000)
    return PcmAudio(
        data=b"\x00" * frames * channels * sample_width,
        sample_rate=sample_rate,
        channels=channels,
        sample_width=sample_width,
    )


def concatenate(clips: Sequence[PcmAudio], gap_ms: int = 0) -> PcmAudio:
    """Join clips in order, inserting `gap_ms` of silence between consecutive clips."""
    if not clips:
        raise AudioFormatError("Nothing to concatenate")
    first = clips[0]
    for clip in clips[1:]:
        if not clip.same_format(first):
            raise AudioFormatError(
                "Cannot join clips with different formats "
                f"(sample rate {clip.sample_rate} vs {first.sample_rate}, "
                f"{clip.channels} vs {first.channels} channels)"
            )
    gap = silence(
        gap_ms,
        sample_rate=first.sample_rate,
        channels=first.channels,
        sample_width=first.sample_width,
    ).data
    return PcmAudio(
        data=gap.join(clip.data for clip in clips),
        sample_rate=first.sample_rate,
        channels=first.channels,
        sample_width=first.sample_width,
    )


def parse_pcm_rate(mime_type: str | None, *, default: int = DEFAULT_SAMPLE_RATE) -> int:
    match = _RATE_PATTERN.search(mime_type or "")
    return int(match.group(1)) if match else default


def decode_audio_payload(
    data: bytes, mime_type: str | None, *, default_rate: int = DEFAULT_SAMPLE_RATE
) -> PcmAudio:
    """Decode audio returned by Gemini.

    Raw `audio/pcm` / `audio/l16` payloads from Gemini are 16-bit little-endian mono;
    the sample rate travels in the MIME type (`audio/pcm;rate=24000`).
    """
    base = (mime_type or "audio/pcm").split(";", 1)[0].strip().lower()
    if base in _WAV_MIME_TYPES:
        return read_wav_bytes(data, source="API audio")
    if base in _RAW_PCM_MIME_TYPES:
        usable = len(data) - len(data) % 2
        return PcmAudio(
            data=data[:usable], sample_rate=parse_pcm_rate(mime_type, default=default_rate)
        )
    raise AudioFormatError(f"Unsupported audio payload type {base}; request audio/wav or PCM")
