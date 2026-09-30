import pytest

from voice_creator.live.instructions import build_system_instruction


def test_verbatim_mode_asks_for_word_for_word_delivery() -> None:
    instruction = build_system_instruction("verbatim")
    assert "exactly" in instruction
    assert "Do not answer" in instruction


def test_improvise_mode_speaks_as_the_persona() -> None:
    instruction = build_system_instruction("improvise", persona="Mario, a friendly chef")
    assert "Mario, a friendly chef" in instruction
    assert "first person" in instruction


def test_improvise_without_persona_uses_generic_wording() -> None:
    assert "the speaker" in build_system_instruction("improvise")


def test_extra_instructions_are_appended() -> None:
    instruction = build_system_instruction("verbatim", extra="Speak slowly.")
    assert instruction.endswith("Speak slowly.")


def test_unknown_mode_is_rejected() -> None:
    with pytest.raises(ValueError, match="mode"):
        build_system_instruction("sing")  # type: ignore[arg-type]
