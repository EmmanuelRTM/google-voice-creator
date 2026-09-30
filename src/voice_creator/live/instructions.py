"""System instructions that turn a Live session into a voice actor or a persona."""

from __future__ import annotations

from typing import Literal

LiveMode = Literal["improvise", "verbatim"]

_VERBATIM = (
    "You are a voice actor recording lines. Speak the user's text exactly as written, "
    "word for word, in the same language. Do not answer, translate, summarise or comment, "
    "and do not add greetings or filler. Perform inline tags such as <laugh> or <sigh> as "
    "sounds instead of reading them aloud."
)

_IMPROVISE = (
    "You are {persona}. Speak in the first person as {persona} would in a natural "
    "conversation: short turns, contractions and everyday phrasing. Stay in character."
)


def build_system_instruction(
    mode: LiveMode, *, persona: str | None = None, extra: str | None = None
) -> str:
    if mode == "verbatim":
        instruction = _VERBATIM
    elif mode == "improvise":
        instruction = _IMPROVISE.format(persona=(persona or "").strip() or "the speaker")
    else:
        raise ValueError(f"Unknown mode {mode!r}; use 'improvise' or 'verbatim'")
    if extra and extra.strip():
        instruction = f"{instruction}\n\n{extra.strip()}"
    return instruction
