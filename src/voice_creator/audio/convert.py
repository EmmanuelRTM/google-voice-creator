"""Convert recordings into the Live API's realtime input format (16-bit PCM mono, 16 kHz)."""

from __future__ import annotations

import sys
from array import array
from collections.abc import Iterator

from voice_creator.audio.pcm import PcmAudio
from voice_creator.errors import AudioFormatError

LIVE_INPUT_RATE = 16_000


def to_live_input(audio: PcmAudio, rate: int = LIVE_INPUT_RATE) -> PcmAudio:
    """Downmix to mono and resample (linear interpolation) to `rate`."""
    if audio.sample_width != 2:
        raise AudioFormatError("Realtime input must be 16-bit PCM")
    if audio.channels == 1 and audio.sample_rate == rate:
        return audio
    mono = _downmix(_samples(audio.data), audio.channels)
    resampled = _resample(mono, audio.sample_rate, rate)
    return PcmAudio(data=_bytes(resampled), sample_rate=rate)


def chunk_pcm(audio: PcmAudio, *, milliseconds: int = 100) -> Iterator[bytes]:
    """Split audio into whole-frame chunks of about `milliseconds` each."""
    frames = max(1, audio.sample_rate * milliseconds // 1000)
    step = frames * audio.frame_size
    for start in range(0, len(audio.data), step):
        yield audio.data[start : start + step]


def _samples(data: bytes) -> array:
    samples = array("h")
    samples.frombytes(data)
    if sys.byteorder != "little":
        samples.byteswap()
    return samples


def _bytes(samples: array) -> bytes:
    if sys.byteorder != "little":
        samples = array("h", samples)
        samples.byteswap()
    return samples.tobytes()


def _downmix(samples: array, channels: int) -> array:
    if channels == 1:
        return samples
    return array(
        "h",
        (
            round(sum(samples[i : i + channels]) / channels)
            for i in range(0, len(samples), channels)
        ),
    )


def _resample(samples: array, source_rate: int, target_rate: int) -> array:
    if source_rate == target_rate or not samples:
        return samples
    count = int(len(samples) * target_rate / source_rate)
    ratio = source_rate / target_rate
    last = len(samples) - 1
    out = array("h", bytes(2 * count))
    for i in range(count):
        position = i * ratio
        left = int(position)
        right = min(left + 1, last)
        fraction = position - left
        out[i] = round(samples[left] + (samples[right] - samples[left]) * fraction)
    return out
