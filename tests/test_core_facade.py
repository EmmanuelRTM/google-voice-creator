import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest

from voice_creator import VoiceCreator
from voice_creator.config import Settings

from conftest import make_tone, wav_bytes
from fakes import FakeBackend, FakeVoicesGateway

NOW = datetime(2026, 9, 30, 12, 0, tzinfo=UTC)


@pytest.fixture
def creator(tmp_path: Path) -> VoiceCreator:
    settings = Settings.from_env({"VOICE_CREATOR_DATA_DIR": str(tmp_path / "data")}, home=tmp_path)
    backend = FakeBackend(
        voices=FakeVoicesGateway(create_response={"id": "voice_me", "type": "replicated"})
    )
    return VoiceCreator(settings, backend=backend, clock=lambda: NOW)


def test_core_package_does_not_depend_on_mcp() -> None:
    code = (
        "import sys, voice_creator, voice_creator.studio.dialogue_studio, "
        "voice_creator.live.runner, voice_creator.gemini.live_gateway, voice_creator.cli; "
        "bad = [m for m in ('mcp', 'starlette', 'uvicorn', 'jwt') if m in sys.modules]; "
        "assert not bad, bad"
    )
    subprocess.run([sys.executable, "-c", code], check=True)


def test_facade_accepts_paths_or_bytes(creator: VoiceCreator, tmp_path: Path) -> None:
    sample = tmp_path / "sample.wav"
    sample.write_bytes(wav_bytes(make_tone(15.0)))

    assert creator.validate_recording(sample).ok
    created = creator.create_profile(
        "me", display_name="Me", sample=str(sample), consent=wav_bytes(make_tone(6.0))
    )
    replicated = creator.voices.replicate_profile("me")

    assert created["profile"]["name"] == "me"
    assert replicated["voice_id"] == "voice_me"


def test_facade_renders_dialogue(creator: VoiceCreator) -> None:
    result = creator.dialogue.render_dialogue("Ana: Hola.", {"Ana": "Kore"}, output_name="t")
    assert Path(result["audio_path"]).exists()


def test_facade_issues_live_tokens(creator: VoiceCreator) -> None:
    creator.create_profile(
        "me",
        display_name="Me",
        sample=wav_bytes(make_tone(15.0)),
        consent=wav_bytes(make_tone(6.0)),
    )
    token = creator.issue_live_token("me", ttl_minutes=5)
    assert token.token.startswith("auth_tokens/")


def test_live_speech_config_is_the_official_sdk_type(creator: VoiceCreator) -> None:
    from google.genai import types

    creator.create_profile(
        "me",
        display_name="Me",
        sample=wav_bytes(make_tone(15.0)),
        consent=wav_bytes(make_tone(6.0)),
        language_code="es-MX",
    )

    config = creator.live_speech_config("me")

    assert isinstance(config, types.SpeechConfig)
    replicated = config.voice_config.replicated_voice_config
    assert replicated.mime_type == "audio/wav"
    assert replicated.voice_sample_audio == creator.voices.live_voice("me").sample_wav
    assert replicated.consent_audio
    assert config.language_code is None  # unsupported by native-audio Live models

    creator.voices.remember_consent_signature("me", "sig-1")
    signed = creator.live_speech_config("me").voice_config.replicated_voice_config
    assert signed.voice_consent_signature.signature == "sig-1"
    assert signed.consent_audio is None
