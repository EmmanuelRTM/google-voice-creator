"""Voice management use cases: validate recordings, keep profiles, manage Gemini voices."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from typing import Any, Literal

from voice_creator.audio.pcm import read_wav_bytes, to_wav_bytes
from voice_creator.audio.quality import (
    CONSENT_RECORDING,
    LIVE_VOICE_SAMPLE,
    VOICES_API_SOURCE,
    QualityReport,
    assess,
)
from voice_creator.errors import AudioFormatError, CastError
from voice_creator.live.config import ReplicatedVoice
from voice_creator.studio.backend import Backend
from voice_creator.voices.catalog import normalize_voice_id
from voice_creator.voices.profiles import ProfileStore, VoiceProfile

SamplePurpose = Literal["live", "voices_api", "consent"]
PROFILE_PREFIX = "profile:"
_REQUIREMENTS = {
    "live": LIVE_VOICE_SAMPLE,
    "voices_api": VOICES_API_SOURCE,
    "consent": CONSENT_RECORDING,
}


class VoiceLibrary:
    def __init__(
        self,
        profiles: ProfileStore,
        backend: Backend,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._profiles = profiles
        self._backend = backend
        self._clock = clock or (lambda: datetime.now(UTC))

    # Recordings and local profiles ---------------------------------------

    def validate_recording(self, wav: bytes, purpose: SamplePurpose = "live") -> QualityReport:
        if purpose not in _REQUIREMENTS:
            raise ValueError(f"Unknown purpose {purpose!r}; use one of {', '.join(_REQUIREMENTS)}")
        return assess(read_wav_bytes(wav, source="recording"), _REQUIREMENTS[purpose])

    def register_profile(
        self,
        name: str,
        *,
        display_name: str,
        sample_wav: bytes,
        consent_wav: bytes,
        language_code: str | None = None,
        overwrite: bool = False,
    ) -> dict[str, Any]:
        sample_report = self.validate_recording(sample_wav, "live")
        consent_report = self.validate_recording(consent_wav, "consent")
        errors = [*sample_report.errors, *consent_report.errors]
        if errors:
            raise AudioFormatError("; ".join(errors))

        profile = self._profiles.create(
            name,
            display_name=display_name,
            sample_wav=_canonical(sample_wav),
            consent_wav=_canonical(consent_wav),
            language_code=language_code,
            overwrite=overwrite,
        )
        return {
            "profile": profile.to_public_dict(),
            "sample_report": sample_report.to_dict(),
            "consent_report": consent_report.to_dict(),
        }

    def profile(self, name: str) -> VoiceProfile:
        return self._profiles.get(name)

    def list_profiles(self) -> list[dict[str, Any]]:
        return [profile.to_public_dict() for profile in self._profiles.list()]

    def delete_profile(self, name: str, *, delete_remote: bool = False) -> dict[str, Any]:
        profile = self._profiles.get(name)
        remote = None
        if delete_remote and profile.voice_id:
            self._backend.voice_catalog().delete(profile.voice_id)
            remote = profile.voice_id
        self._profiles.delete(name)
        return {"deleted": name, "remote_voice_deleted": remote}

    def live_voice(self, name: str) -> ReplicatedVoice:
        profile = self._profiles.get(name)
        return ReplicatedVoice(
            sample_wav=profile.sample_path.read_bytes(),
            consent_wav=profile.consent_path.read_bytes(),
            consent_signature=profile.consent_signature,
        )

    def remember_consent_signature(self, name: str, signature: str) -> None:
        self._profiles.update(name, consent_signature=signature)

    def forget_consent_signature(self, name: str) -> None:
        self._profiles.update(name, consent_signature=None)

    # Gemini Voices API ----------------------------------------------------

    def replicate_profile(self, name: str, *, store: bool = True) -> dict[str, Any]:
        profile = self._profiles.get(name)
        created = self._backend.voice_catalog().replicate(
            source_wav=profile.sample_path.read_bytes(),
            consent_wav=profile.consent_path.read_bytes(),
            display_name=profile.display_name,
            store=store,
            language_code=profile.language_code,
        )
        updated = self._profiles.update(
            name,
            voice_id=created.voice_id,
            voice_key=created.voice_key,
            voice_expire_time=created.expire_time,
        )
        return updated.to_public_dict()

    def design_voice(
        self,
        description: str,
        *,
        display_name: str | None = None,
        language_code: str | None = None,
    ) -> dict[str, Any]:
        created = self._backend.voice_catalog().design(
            description=description, display_name=display_name, language_code=language_code
        )
        return created.to_dict()

    def list_voices(
        self,
        *,
        types: Sequence[str] | None = None,
        search: str | None = None,
        page_size: int | None = None,
        page_token: str | None = None,
    ) -> dict[str, Any]:
        return self._backend.voice_catalog().list(
            types=types, search=search, page_size=page_size, page_token=page_token
        )

    def delete_voice(self, voice_id: str) -> dict[str, Any]:
        normalized = normalize_voice_id(voice_id)
        self._backend.voice_catalog().delete(normalized)
        return {"deleted": normalized}

    # Casting ----------------------------------------------------------------

    def resolve_voice(self, reference: str) -> str:
        """Turn `profile:<name>` into its Gemini voice; pass any other reference through."""
        reference = reference.strip()
        if not reference:
            raise CastError("Voice reference is empty")
        if not reference.startswith(PROFILE_PREFIX):
            return reference

        name = reference.removeprefix(PROFILE_PREFIX).strip()
        profile = self._profiles.get(name)
        if not profile.synthesis_voice:
            raise CastError(
                f"Profile {name!r} has no Gemini voice yet; call voice_replicate_profile first"
            )
        if self._expired(profile.voice_expire_time):
            raise CastError(
                f"The Gemini voice for profile {name!r} expired at {profile.voice_expire_time}; "
                "call voice_replicate_profile again"
            )
        return profile.synthesis_voice

    def _expired(self, expire_time: str | None) -> bool:
        if not expire_time:
            return False
        try:
            expires = datetime.fromisoformat(expire_time)
        except ValueError:
            return False
        if expires.tzinfo is None:
            expires = expires.replace(tzinfo=UTC)
        return expires <= self._clock()


def _canonical(wav: bytes) -> bytes:
    """Re-encode as a minimal PCM WAV (drops metadata chunks some servers reject)."""
    return to_wav_bytes(read_wav_bytes(wav))
