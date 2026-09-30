import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from voice_creator import VoiceCreator
from voice_creator.config import Settings
from voice_creator.diagnostics import VerifyPlan, verify_gemini
from voice_creator.live.config import GEMINI_38_LIVE_EXTENDED_THINKING

from conftest import make_tone, wav_bytes
from fakes import FakeBackend, FakeReplyingConnector, FakeVoicesGateway

NOW = datetime(2026, 9, 30, 12, 0, tzinfo=UTC)
API_KEY = "test-api-key-123"
PLAN = VerifyPlan(
    profile="me",
    display_name="Me",
    sample=wav_bytes(make_tone(12.0)),
    consent=wav_bytes(make_tone(6.0)),
)


@pytest.fixture
def backend() -> FakeBackend:
    return FakeBackend(
        voices=FakeVoicesGateway(
            create_response={
                "key": "voicekey_me",
                "type": "replicated",
                "expire_time": "2026-10-07T12:00:00Z",
            },
            list_response={"voices": [{"id": "voice_a"}]},
        ),
        connector=FakeReplyingConnector(),
    )


def make_creator(tmp_path: Path, backend: FakeBackend, *, api_key: str | None = API_KEY):
    env = {"VOICE_CREATOR_DATA_DIR": str(tmp_path / "data")}
    if api_key:
        env["GEMINI_API_KEY"] = api_key
    return VoiceCreator(Settings.from_env(env, home=tmp_path), backend=backend, clock=lambda: NOW)


def statuses(report) -> dict[str, str]:
    return {result.name: result.status for result in report.results}


def result(report, name: str):
    return next(r for r in report.results if r.name == name)


async def verify(creator, backend, plan=PLAN):
    return await verify_gemini(creator, plan, token_connector=lambda _token: backend.connector)


async def test_full_verification_passes(tmp_path: Path, backend: FakeBackend) -> None:
    creator = make_creator(tmp_path, backend)
    plan = VerifyPlan(**{**PLAN.__dict__, "replicate": True})

    report = await verify(creator, backend, plan)

    assert statuses(report) == {
        "credentials": "pass",
        "recordings": "pass",
        "profile": "pass",
        "voices_api": "pass",
        "live_standard": "pass",
        "live_signature_reuse": "pass",
        "live_extended_thinking": "pass",
        "live_realtime": "pass",
        "ephemeral_token": "pass",
        "tts_cloned_voice": "pass",
    }
    assert report.ok
    assert Path(result(report, "live_standard").details["audio_path"]).exists()
    assert Path(result(report, "tts_cloned_voice").details["audio_path"]).exists()


async def test_live_checks_use_the_right_models_and_voice(tmp_path, backend) -> None:
    creator = make_creator(tmp_path, backend)
    await verify(creator, backend)

    models = [model for model, _config in backend.connector.connections]
    assert GEMINI_38_LIVE_EXTENDED_THINKING in models
    et_config = backend.connector.connections[models.index(GEMINI_38_LIVE_EXTENDED_THINKING)][1]
    assert et_config["thinking_config"] == {"thinking_level": "LOW"}
    reuse_config = backend.connector.connections[1][1]
    replicated = reuse_config["speech_config"]["voice_config"]["replicated_voice_config"]
    assert replicated["voice_consent_signature"] == {"signature": "sig-1"}


async def test_realtime_streams_16k_audio_and_reports_the_transcript(tmp_path, backend) -> None:
    creator = make_creator(tmp_path, backend)
    report = await verify(creator, backend)

    realtime = result(report, "live_realtime")
    session = next(s for s in backend.connector.sessions if ("audio_stream_end",) in s.sent)
    rates = {entry[2] for entry in session.sent if entry[0] == "audio"}
    assert rates == {16_000}
    assert realtime.details["heard"] == "hola"
    assert realtime.details["reply_audio_seconds"] > 0


async def test_ephemeral_token_is_used_but_never_reported(tmp_path, backend) -> None:
    creator = make_creator(tmp_path, backend)
    report = await verify(creator, backend)

    token_result = result(report, "ephemeral_token")
    assert token_result.details["connected_with_token"] is True
    assert "auth_tokens/" not in json.dumps(report.to_dict())
    locked = backend.tokens.requests[0]["live_connect_constraints"]["config"]
    assert "replicated_voice_config" in locked["speech_config"]["voice_config"]
    assert backend.connector.connections[-1][1] == {"response_modalities": ["AUDIO"]}


async def test_missing_api_key_skips_everything_that_calls_gemini(tmp_path, backend) -> None:
    creator = make_creator(tmp_path, backend, api_key=None)
    report = await verify(creator, backend)

    assert result(report, "credentials").status == "fail"
    assert result(report, "profile").status == "pass"
    assert result(report, "live_standard").status == "skip"
    assert backend.connector.connections == []


async def test_without_recordings_an_existing_profile_is_required(tmp_path, backend) -> None:
    creator = make_creator(tmp_path, backend)
    report = await verify(creator, backend, VerifyPlan(profile="ghost"))

    assert result(report, "recordings").status == "skip"
    assert result(report, "profile").status == "fail"
    assert "--sample" in result(report, "profile").summary
    assert result(report, "live_standard").status == "skip"


async def test_rejected_signature_is_a_warning(tmp_path, backend) -> None:
    backend.connector.reject_signatures = True
    creator = make_creator(tmp_path, backend)

    report = await verify(creator, backend)

    reuse = result(report, "live_signature_reuse")
    assert reuse.status == "warn"
    assert "rejected" in reuse.summary


async def test_checks_can_be_skipped_and_tts_needs_replicate(tmp_path, backend) -> None:
    creator = make_creator(tmp_path, backend)
    plan = VerifyPlan(**{**PLAN.__dict__, "skip": frozenset({"live_realtime"})})

    report = await verify(creator, backend, plan)

    assert result(report, "live_realtime").status == "skip"
    assert "--replicate" in result(report, "tts_cloned_voice").summary
    assert backend.voices.calls[0][0] == "list"


async def test_tts_reuses_an_existing_voice_instead_of_replacing_it(tmp_path, backend) -> None:
    backend.voices.create_response = {"id": "voice_me", "type": "replicated"}
    creator = make_creator(tmp_path, backend)
    creator.create_profile("me", display_name="Me", sample=PLAN.sample, consent=PLAN.consent)
    creator.voices.replicate_profile("me")  # a stored voice the user relies on

    report = await verify(creator, backend, VerifyPlan(profile="me", replicate=True))

    assert result(report, "tts_cloned_voice").status == "pass"
    assert creator.voices.profile("me").voice_id == "voice_me"
    assert [call for call in backend.voices.calls if call[0] == "create"] == [
        backend.voices.calls[0]
    ]
    assert backend.synth.calls[-1]["voice"] == "voice_me"


async def test_tts_without_a_voice_needs_replicate(tmp_path, backend) -> None:
    creator = make_creator(tmp_path, backend)
    report = await verify(creator, backend)
    assert result(report, "tts_cloned_voice").status == "skip"
    assert backend.voices.calls == [("list", backend.voices.calls[0][1])]


async def test_recordings_never_overwrite_an_existing_real_profile(tmp_path, backend) -> None:
    backend.voices.create_response = {"id": "voice_me", "type": "replicated"}
    creator = make_creator(tmp_path, backend)
    creator.create_profile("me", display_name="Me", sample=PLAN.sample, consent=PLAN.consent)
    creator.voices.replicate_profile("me")

    report = await verify(creator, backend, PLAN)  # PLAN targets "me" with recordings

    profile = result(report, "profile")
    assert profile.status == "fail"
    assert "already exists" in profile.summary
    assert creator.voices.profile("me").voice_id == "voice_me"


async def test_the_scratch_profile_is_recreated_from_recordings(tmp_path, backend) -> None:
    creator = make_creator(tmp_path, backend)
    plan = VerifyPlan(sample=PLAN.sample, consent=PLAN.consent)

    await verify(creator, backend, plan)
    report = await verify(creator, backend, plan)

    assert result(report, "profile").status == "pass"
