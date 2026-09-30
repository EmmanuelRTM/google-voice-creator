import pytest

from voice_creator.audio.pcm import PcmAudio, concatenate
from voice_creator.audio.quality import (
    CONSENT_RECORDING,
    LIVE_VOICE_SAMPLE,
    VOICES_API_SOURCE,
    assess,
)

from conftest import make_silence, make_tone


def test_clean_fifteen_second_clip_passes_live_requirements() -> None:
    report = assess(make_tone(15.0), LIVE_VOICE_SAMPLE)

    assert report.ok
    assert report.errors == ()
    assert report.duration_seconds == pytest.approx(15.0)


@pytest.mark.parametrize("seconds", [5.0, 25.0])
def test_live_sample_must_be_between_ten_and_twenty_seconds(seconds: float) -> None:
    report = assess(make_tone(seconds), LIVE_VOICE_SAMPLE)

    assert not report.ok
    assert any("10" in error and "20" in error for error in report.errors)


def test_voices_api_accepts_up_to_thirty_seconds() -> None:
    assert assess(make_tone(28.0), VOICES_API_SOURCE).ok


def test_wrong_sample_rate_is_an_error() -> None:
    report = assess(make_tone(15.0, sample_rate=16_000), LIVE_VOICE_SAMPLE)
    assert any("24000 Hz" in error for error in report.errors)


def test_stereo_is_an_error() -> None:
    report = assess(make_tone(15.0, channels=2), LIVE_VOICE_SAMPLE)
    assert any("mono" in error for error in report.errors)


def test_non_16_bit_audio_is_an_error_and_skips_signal_analysis() -> None:
    audio = PcmAudio(data=b"\x80" * 24_000 * 15, sample_rate=24_000, sample_width=1)
    report = assess(audio, LIVE_VOICE_SAMPLE)
    assert any("16-bit" in error for error in report.errors)


def test_clipping_is_a_warning() -> None:
    report = assess(make_tone(15.0, amplitude=1.0), LIVE_VOICE_SAMPLE)
    assert report.ok
    assert any("clipping" in warning for warning in report.warnings)


def test_very_quiet_audio_is_a_warning() -> None:
    report = assess(make_tone(15.0, amplitude=0.002), LIVE_VOICE_SAMPLE)
    assert any("quiet" in warning for warning in report.warnings)


def test_mostly_silent_audio_is_a_warning() -> None:
    audio = concatenate([make_tone(5.0), make_silence(10.0)])
    report = assess(audio, LIVE_VOICE_SAMPLE)
    assert report.silence_ratio > 0.5
    assert any("silence" in warning for warning in report.warnings)


def test_consent_recording_requirements_are_looser() -> None:
    assert assess(make_tone(6.0), CONSENT_RECORDING).ok
    assert not assess(make_tone(1.0), CONSENT_RECORDING).ok


def test_report_serialises_for_mcp_clients() -> None:
    payload = assess(make_tone(15.0), LIVE_VOICE_SAMPLE).to_dict()

    assert payload["ok"] is True
    assert payload["requirement"] == LIVE_VOICE_SAMPLE.label
    assert {"duration_seconds", "peak_dbfs", "rms_dbfs", "errors", "warnings"} <= set(payload)
