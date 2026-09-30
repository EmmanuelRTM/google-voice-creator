"""A small screenplay format for dialogue.

    # comments and blank lines are ignored
    Mario (warm, amused): Hey! <laugh> You made it.
    Ana: I did.
      Indented lines continue the previous line.

`(style)` becomes the per-line delivery direction sent to Gemini TTS; inline
`<tags>` such as `<laugh>` or `<short pause>` are spoken as vocal effects.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass

from voice_creator.errors import ScriptParseError

_SPEAKER_LINE = re.compile(
    r"^(?P<speaker>[^\W\d_][\w .'\-]{0,39}?)\s*(?:\((?P<style>[^)]*)\))?\s*:\s*(?P<text>.*)$"
)
_VOCAL_TAG = re.compile(r"<[a-z][a-z\- ]*>")
_WHITESPACE = re.compile(r"\s+")


@dataclass(frozen=True)
class DialogueLine:
    speaker: str
    text: str
    style: str | None = None


def parse_script(source: str) -> list[DialogueLine]:
    lines: list[DialogueLine] = []
    for number, raw in enumerate(source.splitlines(), start=1):
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        if raw[0].isspace():
            if not lines:
                raise ScriptParseError(f"line {number}: continuation before any speaker line")
            previous = lines[-1]
            joined = f"{previous.text} {raw.strip()}".strip()
            lines[-1] = DialogueLine(previous.speaker, joined, previous.style)
            continue
        match = _SPEAKER_LINE.match(raw.strip())
        if not match:
            raise ScriptParseError(
                f"line {number}: expected `Speaker: text` or `Speaker (style): text`, "
                f"got {raw.strip()[:60]!r}"
            )
        style = (match.group("style") or "").strip() or None
        lines.append(
            DialogueLine(match.group("speaker").strip(), match.group("text").strip(), style)
        )

    if not lines:
        raise ScriptParseError("Script contains no dialogue lines")
    for line in lines:
        if not line.text:
            raise ScriptParseError(f"{line.speaker} has a line with no text")
    return lines


def speakers(lines: Iterable[DialogueLine]) -> list[str]:
    return list(dict.fromkeys(line.speaker for line in lines))


def strip_vocal_tags(text: str) -> str:
    """Remove `<laugh>`-style tags so transcripts and captions read cleanly."""
    return _WHITESPACE.sub(" ", _VOCAL_TAG.sub(" ", text)).strip()
