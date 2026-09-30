"""Untrusted audio inputs: confine file paths and bound base64 payloads.

Tool arguments are written by a model and may be influenced by prompt injection,
so a path argument must never let a caller read arbitrary server files. Remote
deployments disable paths entirely (audio arrives as base64); local deployments
can confine them with VOICE_CREATOR_ALLOWED_DIRS.
"""

from __future__ import annotations

import base64
import binascii
from collections.abc import Sequence
from pathlib import Path

from voice_creator_mcp.errors import AudioFormatError, PathNotAllowedError

MAX_AUDIO_BYTES = 4 * 1024 * 1024


class PathPolicy:
    """`None` roots: any path. Empty roots: paths disabled. Otherwise: inside a root only."""

    def __init__(self, roots: Sequence[Path] | None) -> None:
        self._roots = (
            None if roots is None else tuple(Path(r).expanduser().resolve() for r in roots)
        )

    @property
    def paths_enabled(self) -> bool:
        return self._roots != ()

    def resolve(self, raw: str) -> Path:
        if not self.paths_enabled:
            raise PathNotAllowedError(
                "This server does not read file paths; send the audio as base64 instead"
            )
        path = Path(raw).expanduser().resolve()  # follows symlinks before the check
        if self._roots is None or any(path.is_relative_to(root) for root in self._roots):
            return path
        raise PathNotAllowedError(f"{raw} is outside the allowed directories")


def load_audio_input(
    *,
    path: str | None,
    base64_data: str | None,
    policy: PathPolicy,
    label: str,
    max_bytes: int = MAX_AUDIO_BYTES,
) -> bytes:
    if (path is None) == (base64_data is None):
        raise ValueError(f"Provide exactly one of {label}_path or {label}_base64")
    if base64_data is not None:
        return _decode(base64_data, label, max_bytes)

    resolved = policy.resolve(path or "")
    if not resolved.is_file():
        raise AudioFormatError(f"{label} audio file not found: {path}")
    if resolved.stat().st_size > max_bytes:
        raise AudioFormatError(f"{label} audio is too large (limit {max_bytes // 1024} KiB)")
    return resolved.read_bytes()


def _decode(data: str, label: str, max_bytes: int) -> bytes:
    compact = "".join(data.split())
    if len(compact) > (max_bytes + 2) // 3 * 4:
        raise AudioFormatError(f"{label} audio is too large (limit {max_bytes // 1024} KiB)")
    try:
        return base64.b64decode(compact, validate=True)
    except binascii.Error as exc:
        raise ValueError(f"{label}_base64 is not valid base64") from exc
