"""End-to-end checks of the voice core against the real Gemini API.

Each check exercises one official integration path with the user's own key and
recordings, and reports what Gemini actually accepted: the Voices API, Live with
the cloned voice (standard and Extended Thinking), consent-signature reuse,
server-to-server realtime audio, ephemeral tokens for clients, and TTS.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from voice_creator.audio.pcm import parse_pcm_rate, read_wav_bytes
from voice_creator.client import VoiceCreator
from voice_creator.diagnostics.channel import ScriptedChannel
from voice_creator.diagnostics.report import (
    Check,
    Outcome,
    failed,
    passed,
    skipped,
    warned,
)
from voice_creator.errors import CastError, ProfileError
from voice_creator.live.config import GEMINI_38_LIVE_EXTENDED_THINKING
from voice_creator.live.realtime import OUTPUT_SAMPLE_RATE
from voice_creator.live.runner import LiveConnector
from voice_creator.live.tokens import LiveToken
from voice_creator.studio.dialogue_studio import SIGNATURE_REJECTED
from voice_creator.studio.voice_library import PROFILE_PREFIX, VoiceLibrary

TokenConnector = Callable[[LiveToken], LiveConnector]

GREETING = "Say hello and introduce yourself in one short sentence."
TTS_SPEAKER = "Me"
SCRATCH_PROFILE = "verify"
TTS_LINE = "Hello! This is a short test of my cloned voice with Gemini text to speech."


@dataclass(frozen=True)
class VerifyPlan:
    """What to verify. With `sample` and `consent`, the profile is created from them; only
    the scratch `verify` profile is ever re-created, so real profiles are never replaced."""

    profile: str = SCRATCH_PROFILE
    display_name: str = "Voice verification"
    sample: bytes | None = None
    consent: bytes | None = None
    language_code: str | None = None
    replicate: bool = False
    skip: frozenset[str] = frozenset()


class GeminiChecks:
    def __init__(
        self,
        creator: VoiceCreator,
        plan: VerifyPlan,
        *,
        token_connector: TokenConnector | None = None,
        reply_timeout_s: float = 60.0,
    ) -> None:
        self._creator = creator
        self._voices = creator.voices
        self._dialogue = creator.dialogue
        self._plan = plan
        self._token_connector = token_connector
        self._reply_timeout_s = reply_timeout_s

    def checks(self) -> list[Check]:
        api = ("credentials",)
        voice = ("credentials", "profile")
        checks = [
            Check("credentials", self.credentials),
            Check("recordings", self.recordings),
            Check("profile", self.profile),
            Check("voices_api", self.voices_api, requires=api),
            Check("live_standard", self.live_standard, requires=voice),
            Check("live_signature_reuse", self.live_signature_reuse, requires=("live_standard",)),
            Check("live_extended_thinking", self.live_extended_thinking, requires=voice),
            Check("live_realtime", self.live_realtime, requires=voice),
            Check("ephemeral_token", self.ephemeral_token, requires=voice),
            Check("tts_cloned_voice", self.tts_cloned_voice, requires=voice),
        ]
        return [_skippable(check, self._plan.skip) for check in checks]

    # Setup -------------------------------------------------------------------

    async def credentials(self) -> Outcome:
        settings = self._creator.settings
        settings.require_api_key()
        warnings = settings.credential_warnings()
        if warnings:
            return warned(warnings[0], warnings=warnings)
        return passed("Gemini API key configured")

    async def recordings(self) -> Outcome:
        plan = self._plan
        if plan.sample is None and plan.consent is None:
            return skipped("No recordings given; using the saved profile")
        if plan.sample is None or plan.consent is None:
            return failed("Pass both --sample and --consent")
        sample = self._voices.validate_recording(plan.sample, "live")
        consent = self._voices.validate_recording(plan.consent, "consent")
        details = {"sample": sample.to_dict(), "consent": consent.to_dict()}
        problems = [*sample.errors, *consent.errors]
        if problems:
            return failed(problems[0], **details)
        notes = [*sample.warnings, *consent.warnings]
        if notes:
            return warned(notes[0], **details)
        return passed(
            f"Sample ({sample.duration_seconds:.1f} s) and consent "
            f"({consent.duration_seconds:.1f} s) meet the Live API rules",
            **details,
        )

    async def profile(self) -> Outcome:
        plan = self._plan
        if plan.sample is not None and plan.consent is not None:
            if plan.profile != SCRATCH_PROFILE and self._exists(plan.profile):
                return failed(
                    f"Profile {plan.profile!r} already exists; omit --sample/--consent to "
                    "verify it as it is, or verify the recordings under another --profile"
                )
            created = self._voices.register_profile(
                plan.profile,
                display_name=plan.display_name,
                sample_wav=plan.sample,
                consent_wav=plan.consent,
                language_code=plan.language_code,
                overwrite=True,
            )
            return passed(f"Profile {plan.profile!r} saved locally", **created["profile"])
        try:
            profile = self._voices.profile(plan.profile)
        except ProfileError:
            return failed(
                f"Profile {plan.profile!r} not found; pass --sample and --consent to create it"
            )
        return passed(f"Using the saved profile {plan.profile!r}", **profile.to_public_dict())

    def _exists(self, name: str) -> bool:
        try:
            self._voices.profile(name)
        except ProfileError:
            return False
        return True

    async def voices_api(self) -> Outcome:
        listed = await asyncio.to_thread(self._voices.list_voices, page_size=5)
        count = len(listed["voices"])
        return passed(f"Voices API reachable ({count} voices on the first page)", voices=count)

    # Live --------------------------------------------------------------------

    async def live_standard(self) -> Outcome:
        result = await self._dialogue.live_dialogue(
            self._plan.profile, [GREETING], output_name="verify-live-standard"
        )
        return self._spoken(result, "spoke in the cloned voice")

    async def live_signature_reuse(self) -> Outcome:
        if not self._voices.profile(self._plan.profile).consent_signature:
            return skipped("Gemini returned no consent signature to reuse")
        result = await self._dialogue.live_dialogue(
            self._plan.profile, [GREETING], output_name="verify-live-signature"
        )
        if SIGNATURE_REJECTED in result["warnings"]:
            return warned(
                "The cached consent signature was rejected; the consent recording was used",
                **_live_details(result),
            )
        return self._spoken(result, "accepted the cached consent signature (no consent audio)")

    async def live_extended_thinking(self) -> Outcome:
        result = await self._dialogue.live_dialogue(
            self._plan.profile,
            [GREETING],
            model=GEMINI_38_LIVE_EXTENDED_THINKING,
            thinking_level="low",
            output_name="verify-live-extended-thinking",
        )
        return self._spoken(result, "spoke in the cloned voice with thinking_level=low")

    async def live_realtime(self) -> Outcome:
        profile = self._voices.profile(self._plan.profile)
        recording = read_wav_bytes(profile.sample_path.read_bytes(), source="voice sample")
        channel = ScriptedChannel.speaking(
            recording, reply_timeout_s=self._reply_timeout_s, pace_s=0.005
        )
        summary = await self._dialogue.realtime_conversation(
            self._plan.profile, channel, max_minutes=3
        )
        details = {
            "heard": "".join(e.data["text"] for e in channel.of_kind("input_transcript")),
            "reply_audio_seconds": _seconds(channel.of_kind("audio")),
            "ended": summary["ended"],
            "reconnects": summary["reconnects"],
        }
        errors = channel.of_kind("error")
        if errors:
            return failed(errors[0].data.get("description", "Realtime error"), **details)
        if not details["reply_audio_seconds"]:
            return failed(f"No spoken reply within {self._reply_timeout_s:g} s", **details)
        if not details["heard"]:
            return warned("Replied, but no input transcription came back", **details)
        return passed("Streamed 16 kHz audio in and heard the reply in the cloned voice", **details)

    async def ephemeral_token(self) -> Outcome:
        token = await asyncio.to_thread(
            self._creator.issue_live_token, self._plan.profile, uses=1, ttl_minutes=2
        )
        connector = (self._token_connector or _gemini_token_connector)(token)
        seconds, transcript = await _speak_once(connector, token.model, GREETING)
        details = {
            "model": token.model,
            "uses": token.uses,
            "expire_time": token.expire_time,
            "connected_with_token": True,
            "reply_audio_seconds": seconds,
            "transcript": transcript,
        }
        if not seconds:
            return failed("Connected with the ephemeral token but no audio came back", **details)
        return passed(
            "A single-use token locked to the cloned voice opened a Live session", **details
        )

    # TTS ---------------------------------------------------------------------

    async def tts_cloned_voice(self) -> Outcome:
        """Render with the profile's Gemini voice; never replace a voice the user relies on.

        Only a profile without a usable voice gets a stateless 7-day key (`--replicate`).
        """
        name = self._plan.profile
        existing = _usable_voice(self._voices, name)
        if existing is None:
            if not self._plan.replicate:
                return skipped("Pass --replicate to create a 7-day voice key (nothing stored)")
            await asyncio.to_thread(self._voices.replicate_profile, name, store=False)
        result = await asyncio.to_thread(
            self._dialogue.render_dialogue,
            f"{TTS_SPEAKER} (warm, cheerful): {TTS_LINE}",
            {TTS_SPEAKER: f"profile:{name}"},
            output_name="verify-tts",
        )
        which = "the profile's existing voice" if existing else "a new 7-day voice key"
        return passed(
            f"Gemini TTS rendered {result['duration_seconds']} s with {which}",
            audio_path=result["audio_path"],
            duration_seconds=result["duration_seconds"],
            reused_existing_voice=existing is not None,
        )

    def _spoken(self, result: dict[str, Any], what: str) -> Outcome:
        details = _live_details(result)
        details["consent_signature_cached"] = bool(
            self._voices.profile(self._plan.profile).consent_signature
        )
        if not result["duration_seconds"]:
            return failed("No audio came back", **details)
        if result["warnings"]:
            return warned(result["warnings"][0], **details)
        return passed(f"{result['model']} {what} ({result['duration_seconds']} s)", **details)


def _usable_voice(voices: VoiceLibrary, profile: str) -> str | None:
    try:
        return voices.resolve_voice(f"{PROFILE_PREFIX}{profile}")
    except CastError:
        return None  # no Gemini voice yet, or it expired


def _live_details(result: dict[str, Any]) -> dict[str, Any]:
    return {
        "model": result["model"],
        "audio_path": result["audio_path"],
        "duration_seconds": result["duration_seconds"],
        "transcript": result["transcript"],
        "warnings": result["warnings"],
    }


def _skippable(check: Check, skip: frozenset[str]) -> Check:
    if check.name not in skip:
        return check

    async def skip_it() -> Outcome:
        return skipped("Skipped by request")

    return Check(check.name, skip_it, check.requires, check.timeout_s)


async def _speak_once(connector: LiveConnector, model: str, prompt: str) -> tuple[float, str]:
    """Connect the way a browser would with a locked token: model plus modality only."""
    audio_bytes = 0.0
    words: list[str] = []
    async with connector.connect(model, {"response_modalities": ["AUDIO"]}) as session:
        await session.send_text(prompt)
        complete = False
        while not complete:
            received = False
            async for event in session.receive():
                received = True
                if event.audio:
                    rate = parse_pcm_rate(event.audio_mime_type, default=OUTPUT_SAMPLE_RATE)
                    audio_bytes += len(event.audio) / (2 * rate)
                if event.output_transcript:
                    words.append(event.output_transcript)
                complete = complete or event.turn_complete
            if not received:
                break
    return round(audio_bytes, 3), "".join(words).strip()


def _seconds(events: list[Any]) -> float:
    total = sum(len(e.audio) / (2 * e.data.get("sample_rate", OUTPUT_SAMPLE_RATE)) for e in events)
    return round(total, 3)


def _gemini_token_connector(token: LiveToken) -> LiveConnector:
    from voice_creator.gemini.live_gateway import connector_for_token

    return connector_for_token(token)
