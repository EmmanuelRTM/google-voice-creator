"""Build `LiveConnectConfig` payloads that respect each Gemini 3.8 Live model's rules.

* `gemini-3.8-live` rejects `thinking_level`; it must be omitted.
* `gemini-3.8-live-extended-thinking` accepts `low`, `medium` or `high` (not `minimal`).
* A replicated voice is sent inline on every session (`replicated_voice_config`):
  a 24 kHz, 16-bit mono WAV sample plus either the consent recording or the
  consent signature the server returned for an earlier session.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from voice_creator.errors import LiveConfigError

GEMINI_38_LIVE = "gemini-3.8-live"
GEMINI_38_LIVE_EXTENDED_THINKING = "gemini-3.8-live-extended-thinking"
EXTENDED_THINKING_LEVELS = ("low", "medium", "high")
VOICE_SAMPLE_MIME_TYPE = "audio/wav"

TurnDetection = Literal["vad", "manual"]
"""`vad`: the server detects speech (default). `manual`: push-to-talk; the client
sends activity_start/activity_end, which avoids 3.8's end-of-turn detection issues
with clients that stream digital silence."""


@dataclass(frozen=True)
class ReplicatedVoice:
    sample_wav: bytes
    consent_wav: bytes | None = None
    consent_signature: str | None = None

    def __post_init__(self) -> None:
        if not self.sample_wav:
            raise LiveConfigError("A replicated voice needs a voice sample")
        if not (self.consent_wav or self.consent_signature):
            raise LiveConfigError(
                "A replicated voice needs the consent recording or a consent signature"
            )

    def to_config(self) -> dict[str, Any]:
        config: dict[str, Any] = {
            "mime_type": VOICE_SAMPLE_MIME_TYPE,
            "voice_sample_audio": self.sample_wav,
        }
        if self.consent_signature:
            config["voice_consent_signature"] = {"signature": self.consent_signature}
        else:
            config["consent_audio"] = self.consent_wav
        return config


@dataclass(frozen=True)
class LiveSessionOptions:
    model: str = GEMINI_38_LIVE
    voice: ReplicatedVoice | str | None = None
    system_instruction: str | None = None
    thinking_level: str | None = None
    language_code: str | None = None
    output_transcription: bool = True
    input_transcription: bool = False
    turn_detection: TurnDetection | None = None
    resumable: bool = False
    resumption_handle: str | None = None
    compress_context: bool = False


def build_live_config(options: LiveSessionOptions) -> dict[str, Any]:
    config: dict[str, Any] = {"response_modalities": ["AUDIO"]}

    speech = _speech_config(options)
    if speech:
        config["speech_config"] = speech
    if options.system_instruction:
        config["system_instruction"] = options.system_instruction
    level = validate_thinking_level(options.model, options.thinking_level)
    if level:
        config["thinking_config"] = {"thinking_level": level}
    if options.output_transcription:
        config["output_audio_transcription"] = {}
    config.update(_realtime_config(options))
    return config


def _realtime_config(options: LiveSessionOptions) -> dict[str, Any]:
    """Session-management settings Google recommends for long, streamed conversations."""
    config: dict[str, Any] = {}
    if options.input_transcription:
        config["input_audio_transcription"] = {}
    if options.turn_detection == "manual":
        config["realtime_input_config"] = {"automatic_activity_detection": {"disabled": True}}
    if options.resumption_handle:
        config["session_resumption"] = {"handle": options.resumption_handle}
    elif options.resumable:
        config["session_resumption"] = {}
    if options.compress_context:
        # Sliding-window compression lifts the ~15 min audio session limit.
        config["context_window_compression"] = {"sliding_window": {}}
    return config


def validate_thinking_level(model: str, level: str | None) -> str | None:
    """Return the API enum value for `level`, or raise if `model` does not accept it."""
    if level is None:
        return None
    normalized = level.strip().lower()
    if model == GEMINI_38_LIVE:
        raise LiveConfigError(
            f"{GEMINI_38_LIVE} does not accept thinking_level; omit it or use "
            f"{GEMINI_38_LIVE_EXTENDED_THINKING}"
        )
    if model == GEMINI_38_LIVE_EXTENDED_THINKING and normalized not in EXTENDED_THINKING_LEVELS:
        raise LiveConfigError(
            f"{GEMINI_38_LIVE_EXTENDED_THINKING} supports thinking_level "
            f"{', '.join(EXTENDED_THINKING_LEVELS)} (got {level!r}; minimal is not supported)"
        )
    return normalized.upper()


def _speech_config(options: LiveSessionOptions) -> dict[str, Any]:
    speech: dict[str, Any] = {}
    if isinstance(options.voice, ReplicatedVoice):
        speech["voice_config"] = {"replicated_voice_config": options.voice.to_config()}
    elif isinstance(options.voice, str) and options.voice.strip():
        speech["voice_config"] = {"prebuilt_voice_config": {"voice_name": options.voice.strip()}}
    if options.language_code:
        speech["language_code"] = options.language_code
    return speech
