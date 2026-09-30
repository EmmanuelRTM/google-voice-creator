import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from voice_creator import VoiceCreator
from voice_creator.cli import main, parse_cast
from voice_creator.config import Settings

from conftest import make_tone, wav_bytes
from fakes import FakeBackend, FakeVoicesGateway

NOW = datetime(2026, 9, 30, 12, 0, tzinfo=UTC)


@pytest.fixture
def creator(tmp_path: Path) -> VoiceCreator:
    settings = Settings.from_env({"VOICE_CREATOR_DATA_DIR": str(tmp_path / "data")}, home=tmp_path)
    backend = FakeBackend(
        voices=FakeVoicesGateway(create_response={"id": "voice_me", "type": "replicated"})
    )
    return VoiceCreator(settings, backend=backend, clock=lambda: NOW)


@pytest.fixture
def recordings(tmp_path: Path) -> tuple[str, str]:
    sample, consent = tmp_path / "sample.wav", tmp_path / "consent.wav"
    sample.write_bytes(wav_bytes(make_tone(15.0)))
    consent.write_bytes(wav_bytes(make_tone(6.0)))
    return str(sample), str(consent)


def run(creator: VoiceCreator, capsys: pytest.CaptureFixture[str], *argv: str) -> dict:
    assert main(list(argv), creator=creator) == 0
    return json.loads(capsys.readouterr().out)


def test_validate_prints_the_report(creator, recordings, capsys) -> None:
    report = run(creator, capsys, "validate", recordings[0])
    assert report["ok"] is True


def test_profile_lifecycle(creator, recordings, capsys) -> None:
    sample, consent = recordings
    run(
        creator,
        capsys,
        "profile",
        "create",
        "me",
        "--display-name",
        "Me",
        "--sample",
        sample,
        "--consent",
        consent,
        "--language",
        "es-MX",
    )
    replicated = run(creator, capsys, "profile", "replicate", "me")
    listed = run(creator, capsys, "profile", "list")

    assert replicated["voice_id"] == "voice_me"
    assert [p["name"] for p in listed["profiles"]] == ["me"]


def test_render_reads_a_script_file(creator, capsys, tmp_path: Path) -> None:
    script = tmp_path / "scene.txt"
    script.write_text("Ana: Hola.\nLuis: Adiós.", encoding="utf-8")

    result = run(
        creator,
        capsys,
        "render",
        str(script),
        "--cast",
        "Ana=Kore",
        "--cast",
        "Luis=Puck",
        "--output-name",
        "scene",
    )

    assert Path(result["audio_path"]).name == "scene.wav"


def test_errors_are_reported_without_tracebacks(creator, capsys) -> None:
    assert main(["profile", "replicate", "ghost"], creator=creator) == 1
    assert "not found" in capsys.readouterr().err


@pytest.mark.parametrize(
    ("raw", "expected"),
    [(["Ana=Kore"], {"Ana": "Kore"}), (["Me = profile:me"], {"Me": "profile:me"})],
)
def test_parse_cast(raw, expected) -> None:
    assert parse_cast(raw) == expected


@pytest.mark.parametrize("raw", [["Ana"], ["=Kore"], ["Ana=Kore", "ana=Puck"]])
def test_parse_cast_rejects_bad_entries(raw) -> None:
    with pytest.raises(ValueError):
        parse_cast(raw)
