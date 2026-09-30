from pathlib import Path

import pytest

from voice_creator.audio.pcm import (
    PcmAudio,
    concatenate,
    decode_audio_payload,
    parse_pcm_rate,
    read_wav,
    read_wav_bytes,
    silence,
    to_wav_bytes,
    write_wav,
)
from voice_creator.errors import AudioFormatError

from conftest import make_tone, wav_bytes


def test_duration_and_frame_count() -> None:
    audio = make_tone(1.5, sample_rate=16_000)
    assert audio.frame_count == 24_000
    assert audio.duration_seconds == pytest.approx(1.5)


def test_rejects_data_that_is_not_whole_frames() -> None:
    with pytest.raises(AudioFormatError, match="whole frames"):
        PcmAudio(data=b"\x00\x00\x00", sample_rate=24_000)


@pytest.mark.parametrize("field", ["sample_rate", "channels", "sample_width"])
def test_rejects_non_positive_format_fields(field: str) -> None:
    kwargs = {"data": b"", "sample_rate": 24_000, "channels": 1, "sample_width": 2}
    kwargs[field] = 0
    with pytest.raises(AudioFormatError):
        PcmAudio(**kwargs)


def test_wav_round_trip_through_disk(tmp_path: Path) -> None:
    original = make_tone(0.5, channels=2)
    path = write_wav(tmp_path / "nested" / "clip.wav", original)

    assert path.exists()
    assert read_wav(path) == original


def test_wav_round_trip_through_bytes() -> None:
    original = make_tone(0.25)
    assert read_wav_bytes(to_wav_bytes(original)) == original


def test_read_wav_bytes_rejects_non_wav() -> None:
    with pytest.raises(AudioFormatError, match="WAV"):
        read_wav_bytes(b"ID3 this is an mp3")


def test_read_wav_reports_missing_file(tmp_path: Path) -> None:
    with pytest.raises(AudioFormatError, match="not found"):
        read_wav(tmp_path / "missing.wav")


def test_silence_has_requested_length() -> None:
    gap = silence(250, sample_rate=24_000)
    assert gap.frame_count == 6_000
    assert set(gap.data) == {0}


def test_concatenate_inserts_gaps_between_clips_only() -> None:
    first = make_tone(1.0)
    second = make_tone(0.5)

    joined = concatenate([first, second], gap_ms=100)

    assert joined.frame_count == first.frame_count + 2_400 + second.frame_count
    assert joined.data.startswith(first.data)
    assert joined.data.endswith(second.data)


def test_concatenate_rejects_mismatched_formats() -> None:
    with pytest.raises(AudioFormatError, match="sample rate"):
        concatenate([make_tone(0.1, sample_rate=24_000), make_tone(0.1, sample_rate=16_000)])


def test_concatenate_requires_at_least_one_clip() -> None:
    with pytest.raises(AudioFormatError):
        concatenate([])


@pytest.mark.parametrize(
    ("mime", "expected"),
    [
        ("audio/pcm;rate=16000", 16_000),
        ("audio/L16; codec=pcm; rate=44100", 44_100),
        ("audio/pcm", 24_000),
        (None, 24_000),
    ],
)
def test_parse_pcm_rate(mime: str | None, expected: int) -> None:
    assert parse_pcm_rate(mime, default=24_000) == expected


def test_decode_raw_pcm_payload_uses_rate_from_mime() -> None:
    raw = make_tone(0.1, sample_rate=16_000).data
    audio = decode_audio_payload(raw, "audio/pcm;rate=16000")
    assert audio.sample_rate == 16_000
    assert audio.data == raw


def test_decode_wav_payload() -> None:
    original = make_tone(0.1)
    assert decode_audio_payload(wav_bytes(original), "audio/wav") == original


def test_decode_rejects_compressed_formats() -> None:
    with pytest.raises(AudioFormatError, match="audio/mp3"):
        decode_audio_payload(b"...", "audio/mp3")
