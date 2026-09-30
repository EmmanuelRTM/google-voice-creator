from datetime import UTC, datetime
from pathlib import Path

import pytest

from voice_creator.audio.pcm import read_wav
from voice_creator.studio.outputs import OutputWriter

from conftest import make_tone

NOW = datetime(2026, 9, 30, 12, 34, 56, tzinfo=UTC)


@pytest.fixture
def writer(tmp_path: Path) -> OutputWriter:
    return OutputWriter(tmp_path / "out", clock=lambda: NOW)


def test_writes_audio_and_transcript(writer: OutputWriter, tmp_path: Path) -> None:
    audio = make_tone(0.2)

    written = writer.write("scene-1", audio, "Mario: Hola")

    assert written.audio_path == tmp_path / "out" / "scene-1.wav"
    assert written.transcript_path == tmp_path / "out" / "scene-1.txt"
    assert read_wav(written.audio_path) == audio
    assert written.transcript_path.read_text(encoding="utf-8") == "Mario: Hola\n"


def test_default_name_uses_prefix_and_timestamp(writer: OutputWriter) -> None:
    written = writer.write(None, make_tone(0.1), "x", prefix="dialogue")
    assert written.audio_path.name == "dialogue-20260930T123456Z.wav"


def test_unsafe_characters_are_replaced(writer: OutputWriter, tmp_path: Path) -> None:
    written = writer.write("../../etc/passwd scene", make_tone(0.1), "x")
    assert written.audio_path.parent == tmp_path / "out"
    assert written.audio_path.name == "etc_passwd_scene.wav"


def test_never_overwrites_existing_renders(writer: OutputWriter) -> None:
    first = writer.write("take", make_tone(0.1), "one")
    second = writer.write("take", make_tone(0.1), "two")

    assert first.audio_path.name == "take.wav"
    assert second.audio_path.name == "take-2.wav"
    assert second.transcript_path.name == "take-2.txt"


def test_name_that_sanitises_to_nothing_is_rejected(writer: OutputWriter) -> None:
    with pytest.raises(ValueError, match="output_name"):
        writer.write("../..", make_tone(0.1), "x")
