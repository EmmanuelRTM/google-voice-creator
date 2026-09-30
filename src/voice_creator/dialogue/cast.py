"""Build a cast (speaker -> voice) from pairs, rejecting blanks and duplicate speakers."""

from __future__ import annotations

from collections.abc import Iterable


def build_cast(pairs: Iterable[tuple[str, str]]) -> dict[str, str]:
    cast: dict[str, str] = {}
    seen: set[str] = set()
    for raw_speaker, raw_voice in pairs:
        speaker, voice = raw_speaker.strip(), raw_voice.strip()
        if not speaker or not voice:
            raise ValueError("Every cast entry needs a speaker and a voice")
        if speaker.casefold() in seen:
            raise ValueError(f"Speaker {speaker!r} is cast twice")
        seen.add(speaker.casefold())
        cast[speaker] = voice
    return cast
