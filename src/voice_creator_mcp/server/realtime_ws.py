"""`/v1/live`: server-to-server realtime voice over WebSocket.

Protocol (after an OAuth-authenticated handshake with `Authorization: Bearer`):

1. The client sends `{"type": "start", "profile": "me", ...}` (options mirror
   `/v1/live-tokens`, plus `turn_detection`, `input_transcription`, `max_minutes`).
2. The client streams **binary** frames of 16-bit PCM mono at 16 kHz, and may send
   JSON control frames: `activity_start`, `activity_end` (push-to-talk),
   `audio_stream_end`, `{"type": "text", "text": ...}`, and `stop`.
3. The server sends **binary** frames of 24 kHz PCM (the reply in the cloned voice)
   and JSON events: `session`, `input_transcript`, `output_transcript`,
   `interrupted` (flush playback now), `turn_complete`, `error`, `session_end`.

Browsers cannot set the Authorization header on WebSockets. Browser apps should use
`POST /v1/live-tokens` and connect to Gemini directly (client-to-server).
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable, Sequence
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError
from starlette.websockets import WebSocket, WebSocketDisconnect

from voice_creator.errors import VoiceCreatorError
from voice_creator.live.realtime import ClientMessage, ServerEvent
from voice_creator_mcp.auth.scopes import API_ROUTE_SCOPES, BASELINE_SCOPES, LIVE_RELAY_PATH
from voice_creator_mcp.server.api import classify
from voice_creator_mcp.server.guard import ToolGuard

POLICY_VIOLATION = 1008
INTERNAL_ERROR = 1011
_CONTROL_KINDS = {"text", "activity_start", "activity_end", "audio_stream_end", "stop"}
MAX_TEXT_CHARS = 4_000


class RealtimeStart(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["start"]
    profile: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]{0,63}$")
    mode: Literal["improvise", "verbatim"] = "improvise"
    persona: str | None = Field(None, max_length=2000)
    model: Literal["gemini-3.8-live", "gemini-3.8-live-extended-thinking"] | None = None
    thinking_level: Literal["low", "medium", "high"] | None = None
    language_code: str | None = Field(None, max_length=35)
    extra_instructions: str | None = Field(None, max_length=2000)
    turn_detection: Literal["vad", "manual"] = "vad"
    input_transcription: bool = True
    max_minutes: int = Field(15, ge=1, le=60)


class WebSocketChannel:
    """`ClientChannel` over a Starlette WebSocket."""

    def __init__(self, websocket: WebSocket) -> None:
        self._ws = websocket

    async def receive(self) -> ClientMessage | None:
        while True:
            try:
                message = await self._ws.receive()
            except (WebSocketDisconnect, RuntimeError):
                return None
            if message["type"] == "websocket.disconnect":
                return None
            if message.get("bytes") is not None:
                return ClientMessage("audio", audio=message["bytes"])
            parsed = _control(message.get("text"))
            if parsed:
                return parsed
            await self.send(
                ServerEvent(
                    "error",
                    data={"error": "invalid_message", "description": "Unknown control message"},
                )
            )

    async def send(self, event: ServerEvent) -> None:
        try:
            if event.kind == "audio":
                await self._ws.send_bytes(event.audio)
            else:
                await self._ws.send_json({"type": event.kind, **event.data})
        except (WebSocketDisconnect, RuntimeError):
            pass  # the client left; the upstream side notices and ends the session


def realtime_endpoint(
    guard: ToolGuard, *, allowed_origins: Sequence[str] = ()
) -> Callable[[WebSocket], Awaitable[None]]:
    needed = BASELINE_SCOPES | API_ROUTE_SCOPES[LIVE_RELAY_PATH]

    async def endpoint(websocket: WebSocket) -> None:
        origin = websocket.headers.get("origin")
        if origin and not origin_allowed(origin, allowed_origins):
            await websocket.close(code=POLICY_VIOLATION, reason="origin not allowed")
            return
        await websocket.accept()
        try:
            start = RealtimeStart.model_validate(json.loads(await websocket.receive_text()))
        except (ValidationError, ValueError, KeyError, WebSocketDisconnect):
            await _fail(websocket, "invalid_request", "First message must be a valid start message")
            return
        try:
            async with guard.guarded("api:live_relay", needed, costly=True) as workspace:
                summary = await workspace.studio.realtime_conversation(
                    start.profile,
                    WebSocketChannel(websocket),
                    **start.model_dump(exclude={"type", "profile"}),
                )
        except VoiceCreatorError as exc:
            _status, code, description = classify(exc)
            await _fail(websocket, code, description)
            return
        except ValueError as exc:
            await _fail(websocket, "invalid_request", str(exc))
            return
        try:
            await websocket.send_json({"type": "session_end", **summary})
            await websocket.close(code=1000)
        except (WebSocketDisconnect, RuntimeError):
            pass

    return endpoint


def origin_allowed(origin: str, allowed: Sequence[str]) -> bool:
    """Exact match, or `scheme://host:*` wildcard ports (same rules as the MCP transport)."""
    for pattern in allowed:
        if origin == pattern:
            return True
        if pattern.endswith(":*") and origin.startswith(pattern[:-2] + ":"):
            return True
    return False


def _control(text: str | None) -> ClientMessage | None:
    try:
        payload = json.loads(text or "")
    except ValueError:
        return None
    if not isinstance(payload, dict) or payload.get("type") not in _CONTROL_KINDS:
        return None
    kind = payload["type"]
    if kind != "text":
        return ClientMessage(kind)
    value = payload.get("text")
    if not isinstance(value, str) or not value.strip() or len(value) > MAX_TEXT_CHARS:
        return None
    return ClientMessage("text", text=value)


async def _fail(websocket: WebSocket, code: str, description: str) -> None:
    try:
        await websocket.send_json({"type": "error", "error": code, "description": description})
        await websocket.close(code=POLICY_VIOLATION)
    except (WebSocketDisconnect, RuntimeError):
        pass
