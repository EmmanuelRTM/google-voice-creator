"""`VoiceCreator`: the Python entry point to the voice core, independent of MCP.

from voice_creator import VoiceCreator

creator = VoiceCreator()                       # reads GEMINI_API_KEY etc.
creator.create_profile("me", display_name="Me", sample="me.wav", consent="consent.wav")
creator.voices.replicate_profile("me")
creator.dialogue.render_dialogue("Me (warm): ¡Hola!", {"Me": "profile:me"})
creator.live_speech_config("me")               # SpeechConfig for the SDK or ADK
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any

from voice_creator.audio.quality import QualityReport
from voice_creator.config import Settings
from voice_creator.live.config import LiveSessionOptions, build_live_config
from voice_creator.live.tokens import LiveToken
from voice_creator.studio.backend import Backend, GeminiBackend
from voice_creator.studio.dialogue_studio import DialogueStudio
from voice_creator.studio.outputs import OutputWriter
from voice_creator.studio.voice_library import SamplePurpose, VoiceLibrary
from voice_creator.voices.profiles import ProfileStore

Recording = bytes | str | Path


class VoiceCreator:
    def __init__(
        self,
        settings: Settings | None = None,
        *,
        backend: Backend | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.settings = settings or Settings.from_env()
        self.backend = backend or GeminiBackend(self.settings)
        self.voices = VoiceLibrary(ProfileStore(self.settings.data_dir), self.backend, clock=clock)
        self.dialogue = DialogueStudio(
            self.voices,
            self.backend,
            OutputWriter(self.settings.output_dir, clock=clock),
            default_live_model=self.settings.live_model,
        )

    def validate_recording(
        self, recording: Recording, purpose: SamplePurpose = "live"
    ) -> QualityReport:
        return self.voices.validate_recording(_read(recording), purpose)

    def create_profile(
        self,
        name: str,
        *,
        display_name: str,
        sample: Recording,
        consent: Recording,
        language_code: str | None = None,
        overwrite: bool = False,
    ) -> dict[str, Any]:
        return self.voices.register_profile(
            name,
            display_name=display_name,
            sample_wav=_read(sample),
            consent_wav=_read(consent),
            language_code=language_code,
            overwrite=overwrite,
        )

    def live_speech_config(self, profile: str, *, language_code: str | None = None) -> Any:
        """The official `types.SpeechConfig` that makes Gemini Live speak in `profile`'s voice.

        Use it with the google-genai SDK (`config={"speech_config": ...}`) or ADK
        (`RunConfig(speech_config=...)`). It carries the cached consent signature when
        there is one, otherwise the consent recording.
        """
        from google.genai import types

        options = LiveSessionOptions(
            voice=self.voices.live_voice(profile),
            language_code=language_code or self.voices.profile(profile).language_code,
        )
        return types.SpeechConfig.model_validate(build_live_config(options)["speech_config"])

    def issue_live_token(self, profile: str, **options: Any) -> LiveToken:
        """Ephemeral Live token locked to `profile`'s voice, for a browser or mobile client.
        Options match `DialogueStudio.issue_live_token`."""
        return self.dialogue.issue_live_token(profile, **options)


def _read(recording: Recording) -> bytes:
    return recording if isinstance(recording, bytes) else Path(recording).expanduser().read_bytes()
