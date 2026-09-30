import os
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


def test_api_key_can_come_from_a_secret_file(tmp_path: Path) -> None:
    secret = tmp_path / "gemini.key"
    secret.write_text("AQ.file-key\n")
    secret.chmod(0o600)

    settings = Settings.from_env({"GEMINI_API_KEY_FILE": str(secret)}, home=tmp_path)

    assert settings.require_api_key() == "AQ.file-key"
    assert settings.credential_warnings() == []


def test_environment_key_wins_over_the_secret_file(tmp_path: Path) -> None:
    secret = tmp_path / "gemini.key"
    secret.write_text("AQ.file-key")
    settings = Settings.from_env(
        {"GEMINI_API_KEY": "AQ.env-key", "GEMINI_API_KEY_FILE": str(secret)}, home=tmp_path
    )
    assert settings.require_api_key() == "AQ.env-key"


def test_unreadable_secret_file_is_a_config_error(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="GEMINI_API_KEY_FILE"):
        Settings.from_env({"GEMINI_API_KEY_FILE": str(tmp_path / "missing")}, home=tmp_path)


@pytest.mark.skipif(os.name != "posix", reason="POSIX permissions only")
def test_world_readable_secret_file_is_flagged(tmp_path: Path) -> None:
    secret = tmp_path / "gemini.key"
    secret.write_text("AQ.file-key")
    secret.chmod(0o644)

    settings = Settings.from_env({"GEMINI_API_KEY_FILE": str(secret)}, home=tmp_path)

    assert any("chmod 600" in warning for warning in settings.credential_warnings())


def test_legacy_standard_keys_are_flagged(tmp_path: Path) -> None:
    settings = Settings.from_env({"GEMINI_API_KEY": "AIzaSyLegacyKey"}, home=tmp_path)
    assert any("auth key" in warning for warning in settings.credential_warnings())


def test_allowed_dirs_and_rate_limit(tmp_path: Path) -> None:
    settings = Settings.from_env(
        {
            "VOICE_CREATOR_ALLOWED_DIRS": os.pathsep.join(
                [str(tmp_path / "a"), str(tmp_path / "b")]
            ),
            "VOICE_CREATOR_RATE_LIMIT_PER_MINUTE": "20",
        },
        home=tmp_path,
    )

    assert settings.allowed_dirs == (tmp_path / "a", tmp_path / "b")
    assert settings.rate_limit_per_minute == 20


def test_defaults_leave_paths_unrestricted_and_no_rate_limit(tmp_path: Path) -> None:
    settings = Settings.from_env({}, home=tmp_path)
    assert settings.allowed_dirs is None
    assert settings.rate_limit_per_minute is None


@pytest.mark.parametrize("value", ["zero", "0", "-5"])
def test_invalid_rate_limit_is_rejected(tmp_path: Path, value: str) -> None:
    with pytest.raises(ConfigError, match="RATE_LIMIT"):
        Settings.from_env({"VOICE_CREATOR_RATE_LIMIT_PER_MINUTE": value}, home=tmp_path)
