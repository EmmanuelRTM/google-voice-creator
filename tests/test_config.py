from pathlib import Path

import pytest

from voice_creator_mcp.config import DEFAULT_LIVE_MODEL, DEFAULT_TTS_MODEL, Settings
from voice_creator_mcp.errors import ConfigError


def test_defaults_when_environment_is_empty(tmp_path: Path) -> None:
    settings = Settings.from_env({}, home=tmp_path)

    assert settings.api_key is None
    assert settings.data_dir == tmp_path / ".voice-creator"
    assert settings.output_dir == tmp_path / ".voice-creator" / "output"
    assert settings.tts_model == DEFAULT_TTS_MODEL == "gemini-3.8-flash-tts"
    assert settings.live_model == DEFAULT_LIVE_MODEL == "gemini-3.8-live"


def test_reads_gemini_api_key_and_overrides(tmp_path: Path) -> None:
    env = {
        "GEMINI_API_KEY": "  key-123  ",
        "VOICE_CREATOR_DATA_DIR": str(tmp_path / "data"),
        "VOICE_CREATOR_OUTPUT_DIR": str(tmp_path / "renders"),
        "VOICE_CREATOR_TTS_MODEL": "gemini-3.8-flash-lite-tts",
        "VOICE_CREATOR_LIVE_MODEL": "gemini-3.8-live-extended-thinking",
    }

    settings = Settings.from_env(env, home=tmp_path)

    assert settings.api_key == "key-123"
    assert settings.data_dir == tmp_path / "data"
    assert settings.output_dir == tmp_path / "renders"
    assert settings.tts_model == "gemini-3.8-flash-lite-tts"
    assert settings.live_model == "gemini-3.8-live-extended-thinking"


def test_falls_back_to_google_api_key(tmp_path: Path) -> None:
    settings = Settings.from_env({"GOOGLE_API_KEY": "fallback"}, home=tmp_path)
    assert settings.api_key == "fallback"


def test_blank_values_are_treated_as_unset(tmp_path: Path) -> None:
    settings = Settings.from_env(
        {"GEMINI_API_KEY": "   ", "VOICE_CREATOR_TTS_MODEL": ""}, home=tmp_path
    )
    assert settings.api_key is None
    assert settings.tts_model == DEFAULT_TTS_MODEL


def test_expands_user_home_in_paths(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    settings = Settings.from_env({"VOICE_CREATOR_DATA_DIR": "~/voices"}, home=tmp_path)
    assert settings.data_dir == tmp_path / "voices"


def test_require_api_key_raises_helpful_error(tmp_path: Path) -> None:
    settings = Settings.from_env({}, home=tmp_path)
    with pytest.raises(ConfigError, match="GEMINI_API_KEY"):
        settings.require_api_key()


def test_repr_never_leaks_the_api_key(tmp_path: Path) -> None:
    settings = Settings.from_env({"GEMINI_API_KEY": "super-secret"}, home=tmp_path)
    assert "super-secret" not in repr(settings)
    assert settings.require_api_key() == "super-secret"
