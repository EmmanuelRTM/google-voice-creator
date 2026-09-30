import pytest

from voice_creator_mcp.errors import LiveSessionError
from voice_creator_mcp.live.config import (
    GEMINI_38_LIVE_EXTENDED_THINKING,
    LiveSessionOptions,
    ReplicatedVoice,
)
from voice_creator_mcp.live.runner import LiveDialogueRunner, LiveEvent

from fakes import FakeConnector, FakeSession, audio_event

OPTIONS = LiveSessionOptions(voice=ReplicatedVoice(sample_wav=b"s", consent_wav=b"c"))


async def test_runs_each_prompt_and_joins_audio() -> None:
    session = FakeSession(
        passes_per_turn=[
            [
                [
                    audio_event(0.5),
                    LiveEvent(output_transcript="Hola, "),
                    audio_event(0.5),
                    LiveEvent(output_transcript="soy Mario.", turn_complete=True),
                ]
            ],
            [[audio_event(1.0), LiveEvent(output_transcript="Adiós.", turn_complete=True)]],
        ],
        consent_signature="sig-from-server",
    )
    connector = FakeConnector(session)

    result = await LiveDialogueRunner(connector).run(
        OPTIONS, ["Preséntate", "Despídete"], gap_ms=500
    )

    assert session.sent == ["Preséntate", "Despídete"]
    model, config = connector.connections[0]
    assert model == "gemini-3.8-live"
    assert "replicated_voice_config" in config["speech_config"]["voice_config"]
    assert result.audio.duration_seconds == pytest.approx(2.5)
    assert [turn.transcript for turn in result.turns] == ["Hola, soy Mario.", "Adiós."]
    assert result.transcript() == "Hola, soy Mario.\nAdiós."
    assert result.consent_signature == "sig-from-server"
    assert result.warnings == ()


async def test_extended_thinking_keeps_listening_until_idle() -> None:
    session = FakeSession(
        passes_per_turn=[
            [
                [audio_event(0.5), LiveEvent(turn_complete=True, interaction_status="IN_PROGRESS")],
                [audio_event(1.0), LiveEvent(turn_complete=True, interaction_status="IDLE")],
            ]
        ]
    )
    options = LiveSessionOptions(model=GEMINI_38_LIVE_EXTENDED_THINKING, thinking_level="high")

    result = await LiveDialogueRunner(FakeConnector(session)).run(options, ["Plan my week"])

    assert result.audio.duration_seconds == pytest.approx(1.5)


async def test_turn_without_audio_is_a_warning() -> None:
    session = FakeSession(
        passes_per_turn=[
            [[audio_event(0.5), LiveEvent(turn_complete=True)]],
            [[LiveEvent(turn_complete=True)]],
        ]
    )

    result = await LiveDialogueRunner(FakeConnector(session)).run(OPTIONS, ["one", "two"])

    assert any("turn 2" in warning and "no audio" in warning for warning in result.warnings)


async def test_no_audio_at_all_is_an_error() -> None:
    session = FakeSession(passes_per_turn=[[[LiveEvent(turn_complete=True)]]])
    with pytest.raises(LiveSessionError, match="no audio"):
        await LiveDialogueRunner(FakeConnector(session)).run(OPTIONS, ["hello"])


async def test_interruptions_and_go_away_are_reported() -> None:
    session = FakeSession(
        passes_per_turn=[
            [
                [
                    audio_event(0.2),
                    LiveEvent(interrupted=True),
                    LiveEvent(go_away=True),
                    LiveEvent(turn_complete=True),
                ]
            ]
        ]
    )

    result = await LiveDialogueRunner(FakeConnector(session)).run(OPTIONS, ["hello"])

    assert result.turns[0].interrupted is True
    assert any("interrupted" in warning for warning in result.warnings)
    assert any("GoAway" in warning for warning in result.warnings)


async def test_turn_timeout_raises() -> None:
    session = FakeSession(passes_per_turn=[[[]]], hang=True)
    runner = LiveDialogueRunner(FakeConnector(session), turn_timeout_s=0.05)

    with pytest.raises(LiveSessionError, match="timed out"):
        await runner.run(OPTIONS, ["hello"])


async def test_stream_that_never_completes_is_bounded() -> None:
    passes = [[LiveEvent(turn_complete=True, interaction_status="IN_PROGRESS")]] * 100
    session = FakeSession(passes_per_turn=[list(passes)])
    runner = LiveDialogueRunner(FakeConnector(session), max_receive_passes=5)

    with pytest.raises(LiveSessionError, match="did not reach IDLE"):
        await runner.run(OPTIONS, ["hello"])


async def test_transport_failures_mid_turn_are_reported_with_their_reason() -> None:
    class ClosingSession(FakeSession):
        async def send_text(self, text: str) -> None:
            raise ConnectionError("1008 policy violation")

    runner = LiveDialogueRunner(FakeConnector(ClosingSession(passes_per_turn=[])))

    with pytest.raises(LiveSessionError, match="1008 policy violation"):
        await runner.run(OPTIONS, ["hello"])


@pytest.mark.parametrize("prompts", [[], ["  "]])
async def test_prompts_are_required(prompts: list[str]) -> None:
    runner = LiveDialogueRunner(FakeConnector(FakeSession(passes_per_turn=[])))
    with pytest.raises(ValueError, match="prompt"):
        await runner.run(OPTIONS, prompts)


async def test_turn_progress_is_reported() -> None:
    session = FakeSession(
        passes_per_turn=[
            [[audio_event(0.2), LiveEvent(turn_complete=True)]],
            [[audio_event(0.2), LiveEvent(turn_complete=True)]],
        ]
    )
    progress: list[tuple[int, int]] = []

    async def on_turn(done: int, total: int) -> None:
        progress.append((done, total))

    await LiveDialogueRunner(FakeConnector(session)).run(OPTIONS, ["a", "b"], on_turn=on_turn)

    assert progress == [(1, 2), (2, 2)]
