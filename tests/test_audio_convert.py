import struct

import pytest

from voice_creator.audio.convert import chunk_pcm, to_live_input
from voice_creator.audio.pcm import PcmAudio
from voice_creator.errors import AudioFormatError

from conftest import make_tone


def samples(audio: PcmAudio) -> tuple[int, ...]:
    return struct.unpack(f"<{len(audio.data) // 2}h", audio.data)


def test_resamples_24k_to_16k_mono() -> None:
    converted = to_live_input(make_tone(1.0))

    assert (converted.sample_rate, converted.channels, converted.sample_width) == (16_000, 1, 2)
    assert converted.frame_count == 16_000
    assert converted.duration_seconds == pytest.approx(1.0)


def test_constant_signal_survives_resampling() -> None:
    constant = PcmAudio(data=struct.pack("<480h", *([1000] * 480)), sample_rate=48_000)
    assert set(samples(to_live_input(constant))) == {1000}


def test_stereo_is_downmixed() -> None:
    stereo = PcmAudio(data=struct.pack("<4h", 1000, 3000, -2000, 0), sample_rate=16_000, channels=2)
    assert samples(to_live_input(stereo)) == (2000, -1000)


def test_already_matching_audio_is_returned_unchanged() -> None:
    audio = make_tone(0.1, sample_rate=16_000)
    assert to_live_input(audio) is audio


def test_rejects_non_16_bit_audio() -> None:
    with pytest.raises(AudioFormatError, match="16-bit"):
        to_live_input(PcmAudio(data=b"\x00" * 30, sample_rate=16_000, sample_width=3))


def test_chunks_are_whole_samples_and_cover_the_audio() -> None:
    audio = make_tone(0.25, sample_rate=16_000)

    chunks = list(chunk_pcm(audio, milliseconds=100))

    assert [len(chunk) for chunk in chunks] == [3200, 3200, 1600]
    assert b"".join(chunks) == audio.data
