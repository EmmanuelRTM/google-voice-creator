from datetime import UTC, datetime
from pathlib import Path

import pytest

from voice_creator.errors import AudioFormatError, CastError
from voice_creator.studio.voice_library import VoiceLibrary
from voice_creator.voices.profiles import ProfileStore

from conftest import make_tone, wav_bytes
from fakes import FakeBackend, FakeVoicesGateway

NOW = datetime(2026, 9, 30, 12, 0, tzinfo=UTC)


@pytest.fixture
def backend() -> FakeBackend:
    return FakeBackend(
        voices=FakeVoicesGateway(create_response={"id": "voice_mario", "type": "replicated"})
    )


@pytest.fixture
def library(tmp_path: Path, backend: FakeBackend) -> VoiceLibrary:
    return VoiceLibrary(ProfileStore(tmp_path / "data"), backend, clock=lambda: NOW)


@pytest.fixture
def recordings() -> tuple[bytes, bytes]:
    return wav_bytes(make_tone(15.0)), wav_bytes(make_tone(6.0))


def register(library: VoiceLibrary, recordings: tuple[bytes, bytes], **kwargs):
    sample, consent = recordings
    return library.register_profile(
        "mario", display_name="Mario", sample_wav=sample, consent_wav=consent, **kwargs
    )


def test_validate_sample_reports_against_the_chosen_purpose(
    library: VoiceLibrary, recordings: tuple[bytes, bytes]
) -> None:
    sample, _ = recordings
    assert library.validate_recording(sample, "live").ok
    assert library.validate_recording(sample, "voices_api").ok


def test_validate_sample_rejects_unknown_purpose(
    library: VoiceLibrary, recordings: tuple[bytes, bytes]
) -> None:
    with pytest.raises(ValueError, match="purpose"):
        library.validate_recording(recordings[0], "podcast")  # type: ignore[arg-type]


def test_register_profile_stores_recordings_and_reports(
    library: VoiceLibrary, recordings: tuple[bytes, bytes]
) -> None:
    result = register(library, recordings, language_code="es-MX")

    assert result["profile"]["name"] == "mario"
    assert result["profile"]["language_code"] == "es-MX"
    assert result["sample_report"]["ok"] is True
    assert result["consent_report"]["ok"] is True
    assert [profile["name"] for profile in library.list_profiles()] == ["mario"]


def test_register_profile_rejects_invalid_sample(library: VoiceLibrary) -> None:
    with pytest.raises(AudioFormatError, match="voice sample"):
        library.register_profile(
            "mario",
            display_name="Mario",
            sample_wav=wav_bytes(make_tone(5.0)),
            consent_wav=wav_bytes(make_tone(6.0)),
        )
    assert library.list_profiles() == []


def test_replicate_profile_stores_the_remote_voice_id(
    library: VoiceLibrary, recordings: tuple[bytes, bytes], backend: FakeBackend
) -> None:
    register(library, recordings)

    profile = library.replicate_profile("mario")

    assert profile["voice_id"] == "voice_mario"
    assert backend.voices.calls[0][1]["voice"]["display_name"] == "Mario"
    assert library.resolve_voice("profile:mario") == "voice_mario"


def test_replicate_profile_stateless_stores_key_and_expiry(
    library: VoiceLibrary, recordings: tuple[bytes, bytes], backend: FakeBackend
) -> None:
    backend.voices.create_response = {
        "key": "voicekey_abc",
        "type": "replicated",
        "expire_time": "2026-10-07T12:00:00Z",
    }
    register(library, recordings)

    library.replicate_profile("mario", store=False)

    assert library.resolve_voice("profile:mario") == "voicekey_abc"


def test_expired_voice_key_cannot_be_used(
    tmp_path: Path, backend: FakeBackend, recordings: tuple[bytes, bytes]
) -> None:
    backend.voices.create_response = {
        "key": "voicekey_abc",
        "type": "replicated",
        "expire_time": "2026-09-01T00:00:00Z",
    }
    library = VoiceLibrary(ProfileStore(tmp_path / "data"), backend, clock=lambda: NOW)
    register(library, recordings)
    library.replicate_profile("mario", store=False)

    with pytest.raises(CastError, match="expired"):
        library.resolve_voice("profile:mario")


def test_profile_without_remote_voice_explains_next_step(
    library: VoiceLibrary, recordings: tuple[bytes, bytes]
) -> None:
    register(library, recordings)
    with pytest.raises(CastError, match="voice_replicate_profile"):
        library.resolve_voice("profile:mario")


@pytest.mark.parametrize(("ref", "expected"), [("Kore", "Kore"), (" voice_x ", "voice_x")])
def test_other_references_pass_through(library: VoiceLibrary, ref: str, expected: str) -> None:
    assert library.resolve_voice(ref) == expected


def test_blank_reference_is_rejected(library: VoiceLibrary) -> None:
    with pytest.raises(CastError):
        library.resolve_voice("  ")


def test_live_voice_uses_cached_signature(
    library: VoiceLibrary, recordings: tuple[bytes, bytes]
) -> None:
    register(library, recordings)
    library.remember_consent_signature("mario", "sig-1")

    voice = library.live_voice("mario")

    assert voice.consent_signature == "sig-1"
    assert voice.sample_wav == recordings[0]
    assert voice.consent_wav == recordings[1]

    library.forget_consent_signature("mario")
    assert library.live_voice("mario").consent_signature is None


def test_delete_profile_can_delete_the_remote_voice(
    library: VoiceLibrary, recordings: tuple[bytes, bytes], backend: FakeBackend
) -> None:
    register(library, recordings)
    library.replicate_profile("mario")

    result = library.delete_profile("mario", delete_remote=True)

    assert result == {"deleted": "mario", "remote_voice_deleted": "voice_mario"}
    assert backend.voices.calls[-1] == ("delete", "voice_mario")
    assert library.list_profiles() == []


def test_design_and_manage_remote_voices(library: VoiceLibrary, backend: FakeBackend) -> None:
    backend.voices.create_response = {"id": "voice_designed", "type": "prompted"}

    designed = library.design_voice("A warm radio host", display_name="Host")
    listed = library.list_voices(types=["prompted"])
    deleted = library.delete_voice("voices/voice_designed")

    assert designed["voice_id"] == "voice_designed"
    assert "voices" in listed
    assert deleted == {"deleted": "voice_designed"}
