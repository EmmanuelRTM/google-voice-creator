import pytest

from voice_creator_mcp.dialogue.renderer import DialogueRenderer
from voice_creator_mcp.dialogue.script import DialogueLine
from voice_creator_mcp.errors import AudioFormatError, CastError

from conftest import make_tone
from fakes import FakeSynthesizer

LINES = [
    DialogueLine(speaker="Mario", text="Hello there friend", style="warm"),
    DialogueLine(speaker="Ana", text="Hi <laugh>"),
]


def test_renders_each_line_with_its_speakers_voice_and_style() -> None:
    synth = FakeSynthesizer()
    renderer = DialogueRenderer(synth)

    renderer.render(LINES, {"Mario": "voice_mario", "Ana": "Kore"}, language="en-US")

    assert synth.calls == [
        {
            "text": "Hello there friend",
            "voice": "voice_mario",
            "style": "warm",
            "language": "en-US",
        },
        {"text": "Hi <laugh>", "voice": "Kore", "style": None, "language": "en-US"},
    ]


def test_segments_carry_timings_including_gaps() -> None:
    renderer = DialogueRenderer(FakeSynthesizer())

    result = renderer.render(LINES, {"Mario": "a", "Ana": "b"}, gap_ms=500)

    first, second = result.segments
    assert first.start_seconds == pytest.approx(0.0)
    assert first.end_seconds == pytest.approx(0.3)
    assert second.start_seconds == pytest.approx(0.8)
    assert second.end_seconds == pytest.approx(1.0)
    assert result.audio.duration_seconds == pytest.approx(1.0)


def test_cast_lookup_is_case_insensitive() -> None:
    synth = FakeSynthesizer()
    DialogueRenderer(synth).render(LINES, {"mario": "a", "ANA": "b"})
    assert [call["voice"] for call in synth.calls] == ["a", "b"]


def test_missing_cast_members_fail_before_any_synthesis() -> None:
    synth = FakeSynthesizer()

    with pytest.raises(CastError, match="Ana"):
        DialogueRenderer(synth).render(LINES, {"Mario": "a"})

    assert synth.calls == []


def test_empty_dialogue_is_rejected() -> None:
    with pytest.raises(CastError, match="no lines"):
        DialogueRenderer(FakeSynthesizer()).render([], {"Mario": "a"})


@pytest.mark.parametrize("gap_ms", [-1, 10_001])
def test_gap_must_be_reasonable(gap_ms: int) -> None:
    with pytest.raises(ValueError, match="gap_ms"):
        DialogueRenderer(FakeSynthesizer()).render(LINES, {"Mario": "a", "Ana": "b"}, gap_ms=gap_ms)


def test_mixed_sample_rates_are_reported() -> None:
    class MixedSynth(FakeSynthesizer):
        def synthesize(self, text, *, voice, style, language):  # type: ignore[override]
            rate = 24_000 if voice == "a" else 16_000
            return make_tone(0.1, sample_rate=rate)

    with pytest.raises(AudioFormatError):
        DialogueRenderer(MixedSynth()).render(LINES, {"Mario": "a", "Ana": "b"})


def test_transcript_strips_vocal_tags() -> None:
    result = DialogueRenderer(FakeSynthesizer()).render(LINES, {"Mario": "a", "Ana": "b"})
    assert result.transcript() == "Mario: Hello there friend\nAna: Hi"


def test_progress_is_reported_after_each_line() -> None:
    progress: list[tuple[int, int]] = []

    DialogueRenderer(FakeSynthesizer()).render(
        LINES,
        {"Mario": "a", "Ana": "b"},
        on_progress=lambda done, total: progress.append((done, total)),
    )

    assert progress == [(1, 2), (2, 2)]
