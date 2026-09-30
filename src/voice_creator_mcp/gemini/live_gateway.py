"""`LiveConnector` backed by `genai.Client().aio.live` (google-genai >= 2.25).

Text is sent with `send_realtime_input(text=...)`: Gemini 3.x Live models only
accept `client_content` for seeding initial history.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from voice_creator_mcp.live.runner import LiveEvent


class GeminiLiveConnector:
    def __init__(self, client: Any) -> None:
        self._live = client.aio.live

    @asynccontextmanager
    async def connect(self, model: str, config: dict[str, Any]) -> AsyncIterator[_Session]:
        async with self._live.connect(model=model, config=config) as session:
            yield _Session(session)


class _Session:
    def __init__(self, session: Any) -> None:
        self._session = session

    @property
    def consent_signature(self) -> str | None:
        setup = getattr(self._session, "setup_complete", None)
        signature = getattr(setup, "voice_consent_signature", None)
        return getattr(signature, "signature", None)

    async def send_text(self, text: str) -> None:
        await self._session.send_realtime_input(text=text)

    async def receive(self) -> AsyncIterator[LiveEvent]:
        async for message in self._session.receive():
            for event in message_to_events(message):
                yield event


def message_to_events(message: Any) -> list[LiveEvent]:
    """Split one `LiveServerMessage` into ordered, SDK-independent events."""
    events: list[LiveEvent] = []
    content = getattr(message, "server_content", None)
    if content is not None:
        model_turn = getattr(content, "model_turn", None)
        for part in getattr(model_turn, "parts", None) or []:
            blob = getattr(part, "inline_data", None)
            if blob is not None and blob.data:
                events.append(LiveEvent(audio=blob.data, audio_mime_type=blob.mime_type))
        transcription = getattr(content, "output_transcription", None)
        if transcription is not None and transcription.text:
            events.append(LiveEvent(output_transcript=transcription.text))
        if content.interrupted:
            events.append(LiveEvent(interrupted=True))
        status = _enum_value(content.interaction_status)
        if content.turn_complete or status:
            events.append(
                LiveEvent(turn_complete=bool(content.turn_complete), interaction_status=status)
            )
    if getattr(message, "go_away", None) is not None:
        events.append(LiveEvent(go_away=True))
    return events


def _enum_value(value: Any) -> str | None:
    if value is None:
        return None
    return str(getattr(value, "value", value))
