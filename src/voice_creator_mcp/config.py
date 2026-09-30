"""Runtime settings, read from the environment only. Secrets are never hardcoded."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

from voice_creator_mcp.errors import ConfigError

DEFAULT_TTS_MODEL = "gemini-3.8-flash-tts"
DEFAULT_LIVE_MODEL = "gemini-3.8-live"

_API_KEY_VARS = ("GEMINI_API_KEY", "GOOGLE_API_KEY")


@dataclass(frozen=True)
class Settings:
    api_key: str | None = field(repr=False)
    data_dir: Path
    output_dir: Path
    tts_model: str = DEFAULT_TTS_MODEL
    live_model: str = DEFAULT_LIVE_MODEL

    @classmethod
    def from_env(
        cls, env: Mapping[str, str] | None = None, *, home: Path | None = None
    ) -> Settings:
        env = os.environ if env is None else env
        home = home or Path.home()

        def read(name: str) -> str | None:
            value = env.get(name, "").strip()
            return value or None

        data_dir = _path(read("VOICE_CREATOR_DATA_DIR")) or home / ".voice-creator"
        return cls(
            api_key=next((key for key in map(read, _API_KEY_VARS) if key), None),
            data_dir=data_dir,
            output_dir=_path(read("VOICE_CREATOR_OUTPUT_DIR")) or data_dir / "output",
            tts_model=read("VOICE_CREATOR_TTS_MODEL") or DEFAULT_TTS_MODEL,
            live_model=read("VOICE_CREATOR_LIVE_MODEL") or DEFAULT_LIVE_MODEL,
        )

    def require_api_key(self) -> str:
        if not self.api_key:
            raise ConfigError(
                "No Gemini API key configured. Set GEMINI_API_KEY in the MCP server's "
                "environment (see .env.example)."
            )
        return self.api_key


def _path(value: str | None) -> Path | None:
    return Path(value).expanduser() if value else None
