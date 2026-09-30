import pytest

from voice_creator.errors import LiveConfigError
from voice_creator.live.config import (
    GEMINI_38_LIVE,
    GEMINI_38_LIVE_EXTENDED_THINKING,
    LiveSessionOptions,
    ReplicatedVoice,
    build_live_config,
)

VOICE = ReplicatedVoice(sample_wav=b"sample", consent_wav=b"consent")


def test_standard_model_with_replicated_voice() -> None:
    config = build_live_config(
        LiveSessionOptions(
            model=GEMINI_38_LIVE,
            voice=VOICE,
            system_instruction="Be brief.",
            language_code="es-MX",
        )
    )

    assert config == {
        "response_modalities": ["AUDIO"],
        "speech_config": {
            "voice_config": {
                "replicated_voice_config": {
                    "mime_type": "audio/wav",
                    "voice_sample_audio": b"sample",
                    "consent_audio": b"consent",
                }
            },
            "language_code": "es-MX",
        },
        "system_instruction": "Be brief.",
        "output_audio_transcription": {},
    }


def test_cached_consent_signature_replaces_consent_audio() -> None:
    voice = ReplicatedVoice(sample_wav=b"sample", consent_wav=b"consent", consent_signature="sig")

    replicated = build_live_config(LiveSessionOptions(voice=voice))["speech_config"][
        "voice_config"
    ]["replicated_voice_config"]

    assert replicated["voice_consent_signature"] == {"signature": "sig"}
    assert "consent_audio" not in replicated


def test_replicated_voice_needs_consent_audio_or_signature() -> None:
    with pytest.raises(LiveConfigError, match="consent"):
        ReplicatedVoice(sample_wav=b"sample")


def test_replicated_voice_needs_a_sample() -> None:
    with pytest.raises(LiveConfigError, match="sample"):
        ReplicatedVoice(sample_wav=b"", consent_wav=b"consent")


def test_prebuilt_voice_by_name() -> None:
    config = build_live_config(LiveSessionOptions(voice="Kore"))
    assert config["speech_config"] == {
        "voice_config": {"prebuilt_voice_config": {"voice_name": "Kore"}}
    }


def test_no_voice_means_no_speech_config() -> None:
    assert "speech_config" not in build_live_config(LiveSessionOptions())


def test_standard_model_rejects_thinking_level() -> None:
    with pytest.raises(LiveConfigError, match="extended-thinking"):
        build_live_config(LiveSessionOptions(model=GEMINI_38_LIVE, thinking_level="low"))


@pytest.mark.parametrize("level", ["low", "MEDIUM", "High"])
def test_extended_thinking_accepts_low_medium_high(level: str) -> None:
    config = build_live_config(
        LiveSessionOptions(model=GEMINI_38_LIVE_EXTENDED_THINKING, thinking_level=level)
    )
    assert config["thinking_config"] == {"thinking_level": level.upper()}


def test_extended_thinking_rejects_minimal() -> None:
    with pytest.raises(LiveConfigError, match="minimal"):
        build_live_config(
            LiveSessionOptions(model=GEMINI_38_LIVE_EXTENDED_THINKING, thinking_level="minimal")
        )


def test_extended_thinking_without_level_uses_server_default() -> None:
    config = build_live_config(LiveSessionOptions(model=GEMINI_38_LIVE_EXTENDED_THINKING))
    assert "thinking_config" not in config


def test_unknown_models_pass_thinking_level_through() -> None:
    config = build_live_config(LiveSessionOptions(model="gemini-9-live", thinking_level="minimal"))
    assert config["thinking_config"] == {"thinking_level": "MINIMAL"}


def test_transcription_can_be_disabled() -> None:
    config = build_live_config(LiveSessionOptions(output_transcription=False))
    assert "output_audio_transcription" not in config
