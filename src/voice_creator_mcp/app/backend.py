"""The ports the application layer needs, and their lazy Gemini implementation.

The Gemini client is only created when a tool actually calls Gemini, so offline
tools (sample validation, script preview, profile management) work without a key.
"""

from __future__ import annotations

from functools import cached_property
from typing import Any, Protocol

from voice_creator_mcp.config import Settings
from voice_creator_mcp.dialogue.renderer import SpeechSynthesizer
from voice_creator_mcp.live.runner import LiveConnector
from voice_creator_mcp.voices.catalog import VoiceCatalog


class Backend(Protocol):
    def voice_catalog(self) -> VoiceCatalog: ...

    def synthesizer(self) -> SpeechSynthesizer: ...

    def live_connector(self) -> LiveConnector: ...


class GeminiBackend:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    @cached_property
    def _client(self) -> Any:
        from google import genai

        return genai.Client(api_key=self._settings.require_api_key())

    def voice_catalog(self) -> VoiceCatalog:
        from voice_creator_mcp.gemini.voices_gateway import GeminiVoicesGateway

        return VoiceCatalog(GeminiVoicesGateway(self._client))

    def synthesizer(self) -> SpeechSynthesizer:
        from voice_creator_mcp.gemini.tts_gateway import GeminiTtsSynthesizer

        return GeminiTtsSynthesizer(self._client, model=self._settings.tts_model)

    def live_connector(self) -> LiveConnector:
        from voice_creator_mcp.gemini.live_gateway import GeminiLiveConnector

        return GeminiLiveConnector(self._client)
