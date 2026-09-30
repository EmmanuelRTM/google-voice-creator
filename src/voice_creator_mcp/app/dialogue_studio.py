"""Dialogue use cases: scripted multi-voice renders (TTS) and live dialogue (Live API)."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import asdict, replace
from typing import Any

from voice_creator_mcp.app.backend import Backend
from voice_creator_mcp.app.outputs import OutputWriter, WrittenOutput
from voice_creator_mcp.app.voice_library import VoiceLibrary
from voice_creator_mcp.dialogue.renderer import DialogueRenderer
from voice_creator_mcp.dialogue.script import parse_script, speakers
from voice_creator_mcp.errors import LiveConnectionError
from voice_creator_mcp.live.config import LiveSessionOptions, ReplicatedVoice, build_live_config
from voice_creator_mcp.live.instructions import LiveMode, build_system_instruction
from voice_creator_mcp.live.runner import LiveDialogueResult, LiveDialogueRunner


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
    ) -> dict[str, Any]:
        lines = parse_script(script)
        in_script = {name.casefold() for name in speakers(lines)}
        voices = {
            speaker: self._library.resolve_voice(reference)
            for speaker, reference in cast.items()
            if speaker.casefold() in in_script
        }
        rendered = DialogueRenderer(self._backend.synthesizer()).render(
            lines, voices, gap_ms=gap_ms, language=language
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
    ) -> dict[str, Any]:
        voice_profile = self._library.profile(profile)
        options = LiveSessionOptions(
            model=model or self._default_live_model,
            voice=self._library.live_voice(profile),
            system_instruction=build_system_instruction(
                mode, persona=persona or voice_profile.display_name, extra=extra_instructions
            ),
            thinking_level=thinking_level,
            language_code=language_code or voice_profile.language_code,
        )
        build_live_config(options)  # fail fast on model/option mismatches

        result, warnings = await self._run_with_consent_fallback(profile, options, prompts, gap_ms)
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

    async def _run_with_consent_fallback(
        self,
        profile: str,
        options: LiveSessionOptions,
        prompts: Sequence[str],
        gap_ms: int,
    ) -> tuple[LiveDialogueResult, list[str]]:
        """Use the cached consent signature; if it is rejected, re-verify with the recording."""
        runner = LiveDialogueRunner(
            self._backend.live_connector(), turn_timeout_s=self._live_turn_timeout_s
        )
        try:
            return await runner.run(options, prompts, gap_ms=gap_ms), []
        except LiveConnectionError:
            voice = options.voice
            if not (isinstance(voice, ReplicatedVoice) and voice.consent_signature):
                raise
        self._library.forget_consent_signature(profile)
        retry = replace(options, voice=self._library.live_voice(profile))
        warning = "cached consent signature was rejected; re-verified with the consent recording"
        return await runner.run(retry, prompts, gap_ms=gap_ms), [warning]


def _paths(written: WrittenOutput) -> dict[str, str]:
    return {
        "audio_path": str(written.audio_path),
        "transcript_path": str(written.transcript_path),
    }
