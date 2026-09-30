import pytest

from voice_creator_mcp.dialogue.script import (
    DialogueLine,
    parse_script,
    speakers,
    strip_vocal_tags,
)
from voice_creator_mcp.errors import ScriptParseError


def test_parses_speakers_text_and_optional_style() -> None:
    lines = parse_script(
        """
        Mario (warm, amused): Hey! <laugh> You made it.
        Ana: I did.
        """.replace("        ", "")
    )

    assert lines == [
        DialogueLine(speaker="Mario", text="Hey! <laugh> You made it.", style="warm, amused"),
        DialogueLine(speaker="Ana", text="I did."),
    ]


def test_ignores_comments_and_blank_lines() -> None:
    lines = parse_script("# Scene 1\n\nMario: Hi.\n\n# end\n")
    assert lines == [DialogueLine(speaker="Mario", text="Hi.")]


def test_indented_lines_continue_the_previous_line() -> None:
    lines = parse_script("Mario: This is a long\n  thought that continues.\nAna: Ok.")
    assert lines[0].text == "This is a long thought that continues."
    assert lines[1] == DialogueLine(speaker="Ana", text="Ok.")


def test_colons_inside_text_are_preserved() -> None:
    lines = parse_script("Mario: The meeting is at 10:30: bring coffee.")
    assert lines[0].text == "The meeting is at 10:30: bring coffee."


def test_empty_style_parentheses_mean_no_style() -> None:
    assert parse_script("Mario (): Hi.")[0].style is None


def test_line_without_speaker_reports_line_number() -> None:
    with pytest.raises(ScriptParseError, match="line 2"):
        parse_script("Mario: Hi.\nthis line has no speaker")


def test_continuation_before_any_speaker_is_an_error() -> None:
    with pytest.raises(ScriptParseError, match="line 1"):
        parse_script("   dangling text")


def test_speaker_with_no_text_is_an_error() -> None:
    with pytest.raises(ScriptParseError, match="Mario"):
        parse_script("Mario:\nAna: Hi.")


def test_empty_script_is_an_error() -> None:
    with pytest.raises(ScriptParseError, match="no dialogue"):
        parse_script("# only a comment\n")


def test_speakers_are_unique_in_order_of_appearance() -> None:
    lines = parse_script("Ana: a\nMario: b\nAna: c")
    assert speakers(lines) == ["Ana", "Mario"]


@pytest.mark.parametrize(
    ("raw", "clean"),
    [
        ("Hey! <laugh> You made it.", "Hey! You made it."),
        ("<short pause>Well <throat-clearing> okay", "Well okay"),
        ("No tags here.", "No tags here."),
        ("Math: 1 < 2 and 3 > 2", "Math: 1 < 2 and 3 > 2"),
    ],
)
def test_strip_vocal_tags(raw: str, clean: str) -> None:
    assert strip_vocal_tags(raw) == clean
