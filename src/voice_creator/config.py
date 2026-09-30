"""Runtime settings, read from the environment only. Secrets are never hardcoded."""

from __future__ import annotations

import os
import stat
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

from voice_creator.errors import ConfigError

DEFAULT_TTS_MODEL = "gemini-3.8-flash-tts"
DEFAULT_LIVE_MODEL = "gemini-3.8-live"

_API_KEY_VARS = ("GEMINI_API_KEY", "GOOGLE_API_KEY")
_API_KEY_FILE_VAR = "GEMINI_API_KEY_FILE"
_LEGACY_KEY_PREFIX = "AIza"


@dataclass(frozen=True)
class Settings:
    api_key: str | None = field(repr=False)
    data_dir: Path
    output_dir: Path
    tts_model: str = DEFAULT_TTS_MODEL
    live_model: str = DEFAULT_LIVE_MODEL
    allowed_dirs: tuple[Path, ...] | None = None
    rate_limit_per_minute: int | None = None
    api_key_file: Path | None = None

    @classmethod
    def from_env(
        cls, env: Mapping[str, str] | None = None, *, home: Path | None = None
    ) -> Settings:
        env = os.environ if env is None else env
        home = home or Path.home()

        def read(name: str) -> str | None:
            value = env.get(name, "").strip()
            return value or None

        key_file = _path(read(_API_KEY_FILE_VAR))
        api_key = next((key for key in map(read, _API_KEY_VARS) if key), None)
        if api_key is None and key_file is not None:
            api_key = _read_secret(key_file)

        data_dir = _path(read("VOICE_CREATOR_DATA_DIR")) or home / ".voice-creator"
        return cls(
            api_key=api_key,
            data_dir=data_dir,
            output_dir=_path(read("VOICE_CREATOR_OUTPUT_DIR")) or data_dir / "output",
            tts_model=read("VOICE_CREATOR_TTS_MODEL") or DEFAULT_TTS_MODEL,
            live_model=read("VOICE_CREATOR_LIVE_MODEL") or DEFAULT_LIVE_MODEL,
            allowed_dirs=_paths(read("VOICE_CREATOR_ALLOWED_DIRS")),
            rate_limit_per_minute=_positive_int(
                "VOICE_CREATOR_RATE_LIMIT_PER_MINUTE", read("VOICE_CREATOR_RATE_LIMIT_PER_MINUTE")
            ),
            api_key_file=key_file,
        )

    def require_api_key(self) -> str:
        if not self.api_key:
            raise ConfigError(
                "No Gemini API key configured. Set GEMINI_API_KEY (or GEMINI_API_KEY_FILE) in "
                "the MCP server's environment (see .env.example)."
            )
        return self.api_key

    def credential_warnings(self) -> list[str]:
        """Problems worth logging at startup; never includes the secret itself."""
        warnings = []
        if self.api_key and self.api_key.startswith(_LEGACY_KEY_PREFIX):
            warnings.append(
                "GEMINI_API_KEY is a legacy standard key (AIza...). The Gemini API stopped "
                "accepting standard keys in September 2026; create an auth key (AQ....) in "
                "Google AI Studio."
            )
        if self.api_key_file and os.name == "posix" and self.api_key_file.exists():
            mode = stat.S_IMODE(self.api_key_file.stat().st_mode)
            if mode & (stat.S_IRWXG | stat.S_IRWXO):
                warnings.append(
                    f"{self.api_key_file} is readable by other users; run `chmod 600` on it"
                )
        return warnings


def _path(value: str | None) -> Path | None:
    return Path(value).expanduser() if value else None


def _paths(value: str | None) -> tuple[Path, ...] | None:
    if not value:
        return None
    return tuple(Path(item).expanduser() for item in value.split(os.pathsep) if item.strip())


def _positive_int(name: str, value: str | None) -> int | None:
    if value is None:
        return None
    try:
        number = int(value)
    except ValueError:
        number = 0
    if number <= 0:
        raise ConfigError(f"{name} must be a positive integer")
    return number


def _read_secret(path: Path) -> str:
    try:
        secret = path.read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise ConfigError(f"Cannot read {_API_KEY_FILE_VAR} ({path}): {exc.strerror}") from exc
    if not secret:
        raise ConfigError(f"{_API_KEY_FILE_VAR} ({path}) is empty")
    return secret
