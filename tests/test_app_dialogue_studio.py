from datetime import UTC, datetime
from pathlib import Path

import pytest

from voice_creator.audio.pcm import read_wav
from voice_creator.errors import LiveConfigError
from voice_creator.live.runner import LiveEvent
from voice_creator.studio.dialogue_studio import DialogueStudio
from voice_creator.studio.outputs import OutputWriter
from voice_creator.studio.voice_library import VoiceLibrary
from voice_creator.voices.profiles import ProfileStore

from conftest import make_tone, wav_bytes
from fakes import FakeBackend, FakeConnector, FakeSession, FakeVoicesGateway, audio_event

NOW = datetime(2026, 9, 30, 12, 0, tzinfo=UTC)
SCRIPT = "Mario (playful): Hola <laugh> Ana.\nAna: Hi Mario."


def one_turn_session(**kwargs) -> FakeSession:
    return FakeSession(
        passes_per_turn=[
            [[audio_event(1.0), LiveEvent(output_transcript="Hola.", turn_complete=True)]]
        ],
        **kwargs,
    )


@pytest.fixture
def backend() -> FakeBackend:
    return FakeBackend(
        voices=FakeVoicesGateway(create_response={"id": "voice_mario", "type": "replicated"}),
        connector=FakeConnector(one_turn_session(consent_signature="sig-new")),
    )


@pytest.fixture
def library(tmp_path: Path, backend: FakeBackend) -> VoiceLibrary:
    library = VoiceLibrary(ProfileStore(tmp_path / "data"), backend, clock=lambda: NOW)
    library.register_profile(
        "mario",
        display_name="Mario",
        sample_wav=wav_bytes(make_tone(15.0)),
        consent_wav=wav_bytes(make_tone(6.0)),
    )
    return library


@pytest.fixture
def studio(tmp_path: Path, library: VoiceLibrary, backend: FakeBackend) -> DialogueStudio:
    return DialogueStudio(
        library,
        backend,
        OutputWriter(tmp_path / "out", clock=lambda: NOW),
        default_live_model="gemini-3.8-live",
    )


def test_preview_script(studio: DialogueStudio) -> None:
    preview = studio.preview_script(SCRIPT)
    assert preview["speakers"] == ["Mario", "Ana"]
    assert preview["lines"][0] == {
        "speaker": "Mario",
        "text": "Hola <laugh> Ana.",
        "style": "playful",
    }


def test_render_dialogue_resolves_profiles_and_writes_files(
    studio: DialogueStudio, library: VoiceLibrary, backend: FakeBackend
) -> None:
    library.replicate_profile("mario")

    result = studio.render_dialogue(
        SCRIPT, {"Mario": "profile:mario", "Ana": "Kore"}, output_name="scene", gap_ms=0
    )

    assert [call["voice"] for call in backend.synth.calls] == ["voice_mario", "Kore"]
    assert result["render_id"] == "scene"
    assert Path(result["audio_path"]).name == "scene.wav"
    assert Path(result["transcript_path"]).read_text(encoding="utf-8") == (
        "Mario: Hola Ana.\nAna: Hi Mario.\n"
    )
    assert result["duration_seconds"] == pytest.approx(0.5)
    assert [segment["speaker"] for segment in result["segments"]] == ["Mario", "Ana"]


async def test_live_dialogue_speaks_in_the_profile_voice(
    studio: DialogueStudio, library: VoiceLibrary, backend: FakeBackend
) -> None:
    result = await studio.live_dialogue("mario", ["Introduce yourself"], output_name="intro")

    model, config = backend.connector.connections[0]
    replicated = config["speech_config"]["voice_config"]["replicated_voice_config"]
    assert model == "gemini-3.8-live"
    assert replicated["consent_audio"] == library.live_voice("mario").consent_wav
    assert "first person" in config["system_instruction"]
    assert read_wav(Path(result["audio_path"])).duration_seconds == pytest.approx(1.0)
    assert result["transcript"] == "Hola."
    assert result["model"] == "gemini-3.8-live"
    assert library.live_voice("mario").consent_signature == "sig-new"


async def test_live_dialogue_retries_with_consent_audio_when_signature_rejected(
    studio: DialogueStudio, library: VoiceLibrary, backend: FakeBackend
) -> None:
    library.remember_consent_signature("mario", "sig-expired")
    backend.connector.reject_signatures = True

    result = await studio.live_dialogue("mario", ["Hi"], mode="verbatim")

    first, second = backend.connector.connections
    assert (
        "voice_consent_signature"
        in first[1]["speech_config"]["voice_config"]["replicated_voice_config"]
    )
    assert "consent_audio" in second[1]["speech_config"]["voice_config"]["replicated_voice_config"]
    assert any("signature" in warning for warning in result["warnings"])
    assert library.live_voice("mario").consent_signature == "sig-new"


async def test_live_dialogue_validates_thinking_level_for_model(studio: DialogueStudio) -> None:
    with pytest.raises(LiveConfigError):
        await studio.live_dialogue("mario", ["Hi"], thinking_level="high")


def test_live_token_uses_the_profile_voice_and_cached_signature(
    studio: DialogueStudio, library: VoiceLibrary, backend: FakeBackend
) -> None:
    library.remember_consent_signature("mario", "sig-1")

    token = studio.issue_live_token("mario", mode="verbatim", uses=2)

    request = backend.tokens.requests[0]
    config = request["live_connect_constraints"]["config"]
    replicated = config["speech_config"]["voice_config"]["replicated_voice_config"]
    assert replicated["voice_consent_signature"] == {"signature": "sig-1"}
    assert "consent_audio" not in replicated
    assert "exactly" in config["system_instruction"]
    assert request["uses"] == 2
    assert token.model == "gemini-3.8-live"


def test_live_token_without_signature_carries_the_consent_recording(
    studio: DialogueStudio, backend: FakeBackend
) -> None:
    studio.issue_live_token("mario")
    config = backend.tokens.requests[0]["live_connect_constraints"]["config"]
    assert "consent_audio" in config["speech_config"]["voice_config"]["replicated_voice_config"]


async def test_realtime_conversation_remembers_the_consent_signature(
    studio: DialogueStudio, library: VoiceLibrary, backend: FakeBackend
) -> None:
    from voice_creator.live.realtime import ClientMessage

    from fakes import FakeClientChannel, FakeRealtimeConnector, FakeRealtimeSession

    backend.connector = FakeRealtimeConnector([FakeRealtimeSession(consent_signature="sig-rt")])

    summary = await studio.realtime_conversation(
        "mario", FakeClientChannel([ClientMessage("stop")]), turn_detection="manual"
    )

    _model, config = backend.connector.connections[0]
    assert config["realtime_input_config"] == {"automatic_activity_detection": {"disabled": True}}
    assert config["context_window_compression"] == {"sliding_window": {}}
    assert config["input_audio_transcription"] == {}
    assert summary["ended"] == "client"
    assert library.live_voice("mario").consent_signature == "sig-rt"


async def test_realtime_conversation_retries_with_consent_audio(
    studio: DialogueStudio, library: VoiceLibrary, backend: FakeBackend
) -> None:
    from voice_creator.live.realtime import ClientMessage

    from fakes import FakeClientChannel, FakeRealtimeConnector, FakeRealtimeSession

    library.remember_consent_signature("mario", "expired")
    backend.connector = FakeRealtimeConnector(
        [FakeRealtimeSession(consent_signature="fresh")], reject_signatures=True
    )

    summary = await studio.realtime_conversation(
        "mario", FakeClientChannel([ClientMessage("stop")])
    )

    assert len(backend.connector.connections) == 2
    assert summary["warnings"] == [
        "cached consent signature was rejected; re-verified with the consent recording"
    ]
    assert library.live_voice("mario").consent_signature == "fresh"
