"""`SpeechSynthesizer` backed by Gemini 3.8 Flash TTS through the Interactions API.

Gemini 3.8 TTS treats the input text as the transcript. Sustained delivery
directions ("whispered, urgent") travel as a `speech_metadata` annotation on the
text instead of being written into the prompt, where they would be read aloud.
The voice can be a prebuilt name, a stored voice ID or a stateless voice key.
"""

from __future__ import annotations

import base64
from typing import Any

from voice_creator.audio.pcm import DEFAULT_SAMPLE_RATE, PcmAudio, decode_audio_payload
from voice_creator.errors import SynthesisError


class GeminiTtsSynthesizer:
    def __init__(self, client: Any, *, model: str) -> None:
        self._interactions = client.interactions
        self._model = model

    def synthesize(
        self, text: str, *, voice: str, style: str | None, language: str | None
    ) -> PcmAudio:
        interaction = self._interactions.create(
            model=self._model,
            input=[_text_content(text, style)],
            response_modalities=["audio"],
            generation_config={"speech_config": [_speech_config(voice, language)]},
            response_format={"type": "audio", "mime_type": "audio/wav"},
        )
        output = getattr(interaction, "output_audio", None)
        data = getattr(output, "data", None)
        if not data:
            raise SynthesisError(f"Gemini TTS returned no audio for voice {voice!r}")
        raw = base64.b64decode(data) if isinstance(data, str) else bytes(data)
        return decode_audio_payload(
            raw,
            getattr(output, "mime_type", None),
            default_rate=getattr(output, "sample_rate", None) or DEFAULT_SAMPLE_RATE,
        )


def _text_content(text: str, style: str | None) -> dict[str, Any]:
    content: dict[str, Any] = {"type": "text", "text": text}
    if style:
        # No start/end index: the style applies to the whole text, as in the official example.
        content["annotations"] = [{"type": "speech_metadata", "style": style}]
    return content


def _speech_config(voice: str, language: str | None) -> dict[str, str]:
    config = {"voice": voice}
    if language:
        config["language"] = language
    return config
