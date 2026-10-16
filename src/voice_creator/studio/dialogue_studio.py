"""Dialogue use cases: scripted multi-voice renders (TTS) and live dialogue (Live API)."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import asdict, replace
from datetime import timedelta
from typing import Any

from voice_creator.dialogue.renderer import DialogueRenderer, ProgressCallback
from voice_creator.dialogue.script import parse_script, speakers
from voice_creator.errors import LiveConnectionError
from voice_creator.live.config import (
    LiveSessionOptions,
    ReplicatedVoice,
    TurnDetection,
    build_live_config,
)
from voice_creator.live.instructions import LiveMode, build_system_instruction
from voice_creator.live.realtime import ClientChannel, RealtimeBridge
from voice_creator.live.runner import LiveDialogueResult, LiveDialogueRunner, TurnCallback
from voice_creator.live.tokens import LiveToken, LiveTokenIssuer
from voice_creator.studio.backend import Backend
from voice_creator.studio.outputs import OutputWriter, WrittenOutput
from voice_creator.studio.voice_library import VoiceLibrary


class DialogueStudio:
    def __init__(
        self,
        library: VoiceLibrary,
        backend: Backend,
        outputs: OutputWriter,
        *,
        default_live_model: str,
        live_turn_timeout_s: float = 120.0,
    ) -> None:
        self._library = library
        self._backend = backend
        self._outputs = outputs
        self._default_live_model = default_live_model
        self._live_turn_timeout_s = live_turn_timeout_s

    def preview_script(self, script: str) -> dict[str, Any]:
        lines = parse_script(script)
        return {"speakers": speakers(lines), "lines": [asdict(line) for line in lines]}

    def render_dialogue(
        self,
        script: str,
        cast: Mapping[str, str],
        *,
        output_name: str | None = None,
        gap_ms: int = 300,
        language: str | None = None,
        on_progress: ProgressCallback | None = None,
    ) -> dict[str, Any]:
        lines = parse_script(script)
        in_script = {name.casefold() for name in speakers(lines)}
        voices = {
            speaker: self._library.resolve_voice(reference)
            for speaker, reference in cast.items()
            if speaker.casefold() in in_script
        }
        rendered = DialogueRenderer(self._backend.synthesizer()).render(
            lines, voices, gap_ms=gap_ms, language=language, on_progress=on_progress
        )
        written = self._outputs.write(
            output_name, rendered.audio, rendered.transcript(), prefix="dialogue"
        )
        return {
            **_paths(written),
            "duration_seconds": round(rendered.audio.duration_seconds, 3),
            "segments": [asdict(segment) for segment in rendered.segments],
        }

    async def live_dialogue(
        self,
        profile: str,
        prompts: Sequence[str],
        *,
        mode: LiveMode = "improvise",
        persona: str | None = None,
        model: str | None = None,
        thinking_level: str | None = None,
        language_code: str | None = None,
        extra_instructions: str | None = None,
        output_name: str | None = None,
        gap_ms: int = 400,
        on_turn: TurnCallback | None = None,
    ) -> dict[str, Any]:
        options = self._live_options(
            profile, mode, persona, model, thinking_level, language_code, extra_instructions
        )
        build_live_config(options)  # fail fast on model/option mismatches

        result, warnings = await self._run_with_consent_fallback(
            profile, options, prompts, gap_ms, on_turn
        )
        if result.consent_signature:
            self._library.remember_consent_signature(profile, result.consent_signature)
        written = self._outputs.write(
            output_name, result.audio, result.transcript(), prefix=f"live-{profile}"
        )
        return {
            **_paths(written),
            "transcript": result.transcript(),
            "duration_seconds": round(result.audio.duration_seconds, 3),
            "model": options.model,
            "turns": [asdict(turn) for turn in result.turns],
            "warnings": [*warnings, *result.warnings],
        }

    async def realtime_conversation(
        self,
        profile: str,
        client: ClientChannel,
        *,
        mode: LiveMode = "improvise",
        persona: str | None = None,
        model: str | None = None,
        thinking_level: str | None = None,
        language_code: str | None = None,
        extra_instructions: str | None = None,
        turn_detection: TurnDetection | None = None,
        input_transcription: bool = True,
        max_minutes: int = 15,
    ) -> dict[str, Any]:
        """Relay a live, streamed conversation between `client` and Gemini in the profile's
        voice (server-to-server), with resumption and context compression enabled."""
        options = replace(
            self._live_options(
                profile, mode, persona, model, thinking_level, language_code, extra_instructions
            ),
            input_transcription=input_transcription,
            turn_detection=turn_detection,
            compress_context=True,
        )
        build_live_config(options)  # fail fast on model/option mismatches
        bridge = RealtimeBridge(self._backend.live_connector(), max_duration_s=max_minutes * 60)
        warnings: list[str] = []
        try:
            summary = await bridge.run(options, client)
        except LiveConnectionError:
            if not _used_signature(options):
                raise
            self._library.forget_consent_signature(profile)
            warnings.append(SIGNATURE_REJECTED)
            summary = await bridge.run(
                replace(options, voice=self._library.live_voice(profile)), client
            )
        if summary.consent_signature:
            self._library.remember_consent_signature(profile, summary.consent_signature)
        return {**summary.to_dict(), "warnings": warnings}

    def issue_live_token(
        self,
        profile: str,
        *,
        mode: LiveMode = "improvise",
        persona: str | None = None,
        model: str | None = None,
        thinking_level: str | None = None,
        language_code: str | None = None,
        extra_instructions: str | None = None,
        uses: int = 1,
        ttl_minutes: int = 30,
    ) -> LiveToken:
        """Mint an ephemeral Live token locked to this profile's voice and instructions.

        Uses the cached consent signature when there is one (a much smaller token);
        otherwise the consent recording travels in the locked config.
        """
        options = self._live_options(
            profile, mode, persona, model, thinking_level, language_code, extra_instructions
        )
        # The token locks the setup config, so a browser cannot ask for transcripts later.
        options = replace(options, input_transcription=True)
        issuer = LiveTokenIssuer(self._backend.token_gateway())
        return issuer.issue(options, uses=uses, ttl=timedelta(minutes=ttl_minutes))

    def _live_options(
        self,
        profile: str,
        mode: LiveMode,
        persona: str | None,
        model: str | None,
        thinking_level: str | None,
        language_code: str | None,
        extra_instructions: str | None,
    ) -> LiveSessionOptions:
        voice_profile = self._library.profile(profile)
        return LiveSessionOptions(
            model=model or self._default_live_model,
            voice=self._library.live_voice(profile),
            system_instruction=build_system_instruction(
                mode, persona=persona or voice_profile.display_name, extra=extra_instructions
            ),
            thinking_level=thinking_level,
            language_code=language_code or voice_profile.language_code,
        )

    async def _run_with_consent_fallback(
        self,
        profile: str,
        options: LiveSessionOptions,
        prompts: Sequence[str],
        gap_ms: int,
        on_turn: TurnCallback | None,
    ) -> tuple[LiveDialogueResult, list[str]]:
        """Use the cached consent signature; if it is rejected, re-verify with the recording."""
        runner = LiveDialogueRunner(
            self._backend.live_connector(), turn_timeout_s=self._live_turn_timeout_s
        )
        try:
            return await runner.run(options, prompts, gap_ms=gap_ms, on_turn=on_turn), []
        except LiveConnectionError:
            if not _used_signature(options):
                raise
        self._library.forget_consent_signature(profile)
        retry = replace(options, voice=self._library.live_voice(profile))
        return await runner.run(retry, prompts, gap_ms=gap_ms, on_turn=on_turn), [
            SIGNATURE_REJECTED
        ]


SIGNATURE_REJECTED = "cached consent signature was rejected; re-verified with the consent recording"


def _used_signature(options: LiveSessionOptions) -> bool:
    return isinstance(options.voice, ReplicatedVoice) and bool(options.voice.consent_signature)


def _paths(written: WrittenOutput) -> dict[str, str]:
    return {
        "render_id": written.audio_path.stem,
        "audio_path": str(written.audio_path),
        "transcript_path": str(written.transcript_path),
    }
