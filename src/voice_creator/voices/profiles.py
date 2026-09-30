"""Local voice profiles: your voice sample, consent recording and cached references.

The Live API cannot use Voices API IDs yet, so each Live session re-sends the sample
and either the consent audio or a server-issued consent signature. Profiles keep
those recordings on disk with owner-only permissions because they are biometric data.
"""

from __future__ import annotations

import json
import os
import re
import shutil
from collections.abc import Callable
from dataclasses import asdict, dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from voice_creator.errors import ProfileError

_NAME_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
_MUTABLE_FIELDS = frozenset(
    {
        "display_name",
        "language_code",
        "consent_signature",
        "voice_id",
        "voice_key",
        "voice_expire_time",
    }
)
_PROFILE_FILE = "profile.json"
_SAMPLE_FILE = "sample.wav"
_CONSENT_FILE = "consent.wav"


@dataclass(frozen=True)
class VoiceProfile:
    name: str
    display_name: str
    sample_path: Path
    consent_path: Path
    created_at: str
    language_code: str | None = None
    consent_signature: str | None = None
    voice_id: str | None = None
    voice_key: str | None = None
    voice_expire_time: str | None = None

    @property
    def synthesis_voice(self) -> str | None:
        """The reference Gemini TTS accepts: a stored voice ID, else a stateless key."""
        return self.voice_id or self.voice_key

    def to_public_dict(self) -> dict[str, Any]:
        """A view that is safe to return to MCP clients (secrets masked)."""
        return {
            "name": self.name,
            "display_name": self.display_name,
            "language_code": self.language_code,
            "created_at": self.created_at,
            "sample_path": str(self.sample_path),
            "consent_path": str(self.consent_path),
            "has_consent_signature": self.consent_signature is not None,
            "voice_id": self.voice_id,
            "voice_key": _mask(self.voice_key),
            "voice_expire_time": self.voice_expire_time,
        }


class ProfileStore:
    def __init__(self, root: Path, *, clock: Callable[[], datetime] | None = None) -> None:
        self._dir = Path(root) / "profiles"
        self._clock = clock or (lambda: datetime.now(UTC))

    def create(
        self,
        name: str,
        *,
        display_name: str,
        sample_wav: bytes,
        consent_wav: bytes,
        language_code: str | None = None,
        overwrite: bool = False,
    ) -> VoiceProfile:
        directory = self._profile_dir(name)
        if directory.exists():
            if not overwrite:
                raise ProfileError(f"Voice profile {name!r} already exists; pass overwrite=true")
            shutil.rmtree(directory)
        _make_private_dir(directory)
        _write_private(directory / _SAMPLE_FILE, sample_wav)
        _write_private(directory / _CONSENT_FILE, consent_wav)

        profile = VoiceProfile(
            name=name,
            display_name=display_name.strip() or name,
            sample_path=directory / _SAMPLE_FILE,
            consent_path=directory / _CONSENT_FILE,
            created_at=self._clock().isoformat(),
            language_code=language_code,
        )
        self._save(profile)
        return profile

    def get(self, name: str) -> VoiceProfile:
        path = self._profile_dir(name) / _PROFILE_FILE
        if not path.is_file():
            raise ProfileError(f"Voice profile {name!r} not found")
        data = json.loads(path.read_text(encoding="utf-8"))
        directory = path.parent
        return VoiceProfile(
            sample_path=directory / _SAMPLE_FILE, consent_path=directory / _CONSENT_FILE, **data
        )

    def list(self) -> list[VoiceProfile]:
        if not self._dir.is_dir():
            return []
        names = sorted(
            child.name for child in self._dir.iterdir() if (child / _PROFILE_FILE).is_file()
        )
        return [self.get(name) for name in names]

    def update(self, name: str, **changes: Any) -> VoiceProfile:
        unknown = sorted(set(changes) - _MUTABLE_FIELDS)
        if unknown:
            raise ProfileError(f"Cannot update field(s): {', '.join(unknown)}")
        profile = replace(self.get(name), **changes)
        self._save(profile)
        return profile

    def delete(self, name: str) -> None:
        self.get(name)
        shutil.rmtree(self._profile_dir(name))

    def read_sample(self, name: str) -> bytes:
        return self.get(name).sample_path.read_bytes()

    def read_consent(self, name: str) -> bytes:
        return self.get(name).consent_path.read_bytes()

    def _profile_dir(self, name: str) -> Path:
        if not _NAME_PATTERN.fullmatch(name or ""):
            raise ProfileError(
                f"Invalid profile name {name!r}: use 1-64 lowercase letters, digits, '-' or '_', "
                "starting with a letter or digit"
            )
        return self._dir / name

    def _save(self, profile: VoiceProfile) -> None:
        data = asdict(profile)
        data.pop("sample_path")
        data.pop("consent_path")
        target = self._profile_dir(profile.name) / _PROFILE_FILE
        temporary = target.with_suffix(".tmp")
        _write_private(temporary, json.dumps(data, indent=2).encode("utf-8"))
        os.replace(temporary, target)


def _make_private_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    path.chmod(0o700)


def _write_private(path: Path, data: bytes) -> None:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "wb") as handle:
        handle.write(data)
    path.chmod(0o600)


def _mask(secret: str | None) -> str | None:
    if not secret:
        return None
    prefix, _, _ = secret.partition("_")
    return f"{prefix}_…" if prefix != secret else "…"
