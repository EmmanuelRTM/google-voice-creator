"""Smoke test for scripts/verify_gemini.py (no network: no API key configured)."""

import importlib.util
import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from voice_creator import VoiceCreator
from voice_creator.config import Settings

from conftest import make_tone, wav_bytes
from fakes import FakeBackend

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "verify_gemini.py"


@pytest.fixture
def script():
    spec = importlib.util.spec_from_file_location("verify_gemini", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_runs_core_and_mcp_checks_and_writes_the_report(script, tmp_path, capsys) -> None:
    settings = Settings.from_env({"VOICE_CREATOR_DATA_DIR": str(tmp_path / "data")}, home=tmp_path)
    creator = VoiceCreator(
        settings, backend=FakeBackend(), clock=lambda: datetime(2026, 9, 30, tzinfo=UTC)
    )
    sample, consent = tmp_path / "s.wav", tmp_path / "c.wav"
    sample.write_bytes(wav_bytes(make_tone(12.0)))
    consent.write_bytes(wav_bytes(make_tone(6.0)))
    report_path = tmp_path / "out" / "report.json"

    code = script.main(
        ["--sample", str(sample), "--consent", str(consent), "--report", str(report_path)],
        creator=creator,
    )

    assert code == 1  # no API key in this environment
    report = json.loads(report_path.read_text())
    statuses = {check["name"]: check["status"] for check in report["checks"]}
    assert statuses["credentials"] == "fail"
    assert statuses["profile"] == "pass"
    assert statuses["mcp_tool_schemas"] == "pass"
    assert statuses["mcp_live_tool_call"] == "skip"
    assert statuses["interactions_mcp_server"] == "skip"
    assert "| mcp_tool_schemas | pass |" in capsys.readouterr().out


def test_rejects_a_non_https_mcp_url(script, capsys) -> None:
    with pytest.raises(SystemExit):
        script.main(["--mcp-url", "http://voice.example.com/mcp"])
    assert "https" in capsys.readouterr().err
