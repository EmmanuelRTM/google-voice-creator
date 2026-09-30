"""Shared test helpers: synthetic PCM audio that needs no fixtures on disk."""

from __future__ import annotations

import io
import math
import struct
import wave
from collections.abc import Callable

import pytest

from voice_creator.audio.pcm import PcmAudio


def make_tone(
    seconds: float,
    *,
    sample_rate: int = 24_000,
    channels: int = 1,
    amplitude: float = 0.3,
    frequency: float = 220.0,
) -> PcmAudio:
    """Build a 16-bit sine tone; amplitude is a fraction of full scale."""
    frames = int(seconds * sample_rate)
    peak = int(amplitude * 32767)
    samples = []
    for i in range(frames):
        value = int(peak * math.sin(2 * math.pi * frequency * i / sample_rate))
        samples.extend([value] * channels)
    data = struct.pack(f"<{len(samples)}h", *samples)
    return PcmAudio(data=data, sample_rate=sample_rate, channels=channels)


def make_silence(seconds: float, *, sample_rate: int = 24_000) -> PcmAudio:
    frames = int(seconds * sample_rate)
    return PcmAudio(data=b"\x00\x00" * frames, sample_rate=sample_rate)


def wav_bytes(audio: PcmAudio) -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as writer:
        writer.setnchannels(audio.channels)
        writer.setsampwidth(audio.sample_width)
        writer.setframerate(audio.sample_rate)
        writer.writeframes(audio.data)
    return buffer.getvalue()


@pytest.fixture
def tone() -> Callable[..., PcmAudio]:
    return make_tone


@pytest.fixture
def valid_sample_wav() -> bytes:
    """A 15 s, 24 kHz mono clip that satisfies the Live replication rules."""
    return wav_bytes(make_tone(15.0))


@pytest.fixture
def valid_consent_wav() -> bytes:
    return wav_bytes(make_tone(6.0, frequency=330.0))
