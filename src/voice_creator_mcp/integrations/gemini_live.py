"""Use an MCP server's tools inside a Gemini Live session, the way Gemini 3.8 expects.

The google-genai SDK can turn an MCP session into tools (`tools=[mcp_session]`), but
it declares them as blocking functions and leaves execution to you. Gemini 3.8 Live
Extended Thinking only accepts asynchronous (`NON_BLOCKING`) functions and keeps
talking while they run, so this bridge:

* declares every tool with `behavior=NON_BLOCKING` and its JSON Schema as
  `parameters_json_schema` (the SDK's recommended native JSON Schema field),
* runs each `tool_call` concurrently and answers with `send_tool_response`, using
  `scheduling` (WHEN_IDLE by default) so results do not cut the model off,
* cancels running calls on `tool_call_cancellation`.

    async with mcp_session_for_server() as mcp, client.aio.live.connect(
        model="gemini-3.8-live-extended-thinking",
        config={"tools": await bridge.tools(), ...},
    ) as live:
        async for message in live.receive():
            await bridge.dispatch(message, live)
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Collection
from typing import Any, Protocol

from google.genai import types

logger = logging.getLogger(__name__)


class McpSession(Protocol):
    async def list_tools(self) -> Any: ...

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> Any: ...


class LiveSession(Protocol):
    async def send_tool_response(
        self, *, function_responses: list[types.FunctionResponse]
    ) -> None: ...


class LiveMcpBridge:
    def __init__(
        self,
        mcp: McpSession,
        *,
        allowed_tools: Collection[str] | None = None,
        scheduling: types.FunctionResponseScheduling = types.FunctionResponseScheduling.WHEN_IDLE,
    ) -> None:
        self._mcp = mcp
        self._allowed = frozenset(allowed_tools) if allowed_tools is not None else None
        self._scheduling = scheduling
        self._pending: dict[str, asyncio.Task[None]] = {}
        self._unanswered = 0

    async def tools(self) -> list[types.Tool]:
        listed = await self._mcp.list_tools()
        declarations = [
            types.FunctionDeclaration(
                name=tool.name,
                description=tool.description or "",
                parameters_json_schema=_input_schema(tool),
                behavior=types.Behavior.NON_BLOCKING,
            )
            for tool in listed.tools
            if self._is_allowed(tool.name)
        ]
        return [types.Tool(function_declarations=declarations)]

    @property
    def busy(self) -> bool:
        """True while a call runs, or a result was sent and the model has not spoken since.

        With Extended Thinking the model can report IDLE before it reacts to a result
        (WHEN_IDLE scheduling), so end a conversation on IDLE only when not busy.
        """
        return bool(self._pending) or self._unanswered > 0

    async def dispatch(self, message: types.LiveServerMessage, live: LiveSession) -> bool:
        """Handle tool calls and cancellations; returns False for any other message."""
        if _has_model_output(message):
            self._unanswered = 0
        if message.tool_call and message.tool_call.function_calls:
            for call in message.tool_call.function_calls:
                call_id = call.id or call.name or ""
                self._pending[call_id] = asyncio.create_task(self._run(call, live))
            return True
        if message.tool_call_cancellation and message.tool_call_cancellation.ids:
            for call_id in message.tool_call_cancellation.ids:
                task = self._pending.pop(call_id, None)
                if task:
                    task.cancel()
            return True
        return False

    async def drain(self) -> None:
        """Wait for (or finish cancelling) every in-flight call."""
        tasks = list(self._pending.values())
        await asyncio.gather(*tasks, return_exceptions=True)
        self._pending.clear()

    async def _run(self, call: types.FunctionCall, live: LiveSession) -> None:
        name = call.name or ""
        if not self._is_allowed(name):
            response: dict[str, Any] = {"error": f"Tool {name!r} is not allowed in this session"}
        else:
            try:
                result = await self._mcp.call_tool(name, dict(call.args or {}))
                response = _to_response(result)
            except Exception as exc:  # report to the model instead of killing the session
                logger.warning("MCP tool %s failed: %s", name, type(exc).__name__)
                response = {"error": f"{type(exc).__name__}: {exc}"}
        await live.send_tool_response(
            function_responses=[
                types.FunctionResponse(
                    id=call.id, name=name, response=response, scheduling=self._scheduling
                )
            ]
        )
        if self._scheduling != types.FunctionResponseScheduling.SILENT:
            self._unanswered += 1
        self._pending.pop(call.id or name, None)

    def _is_allowed(self, name: str) -> bool:
        return self._allowed is None or name in self._allowed


def _has_model_output(message: types.LiveServerMessage) -> bool:
    content = message.server_content
    parts = content.model_turn.parts if content and content.model_turn else None
    return bool(parts) or bool(content and content.output_transcription)


def _input_schema(tool: Any) -> dict[str, Any]:
    schema = getattr(tool, "input_schema", None) or getattr(tool, "inputSchema", None)
    return dict(schema or {"type": "object", "properties": {}})


def _to_response(result: Any) -> dict[str, Any]:
    text = "\n".join(
        part.text for part in getattr(result, "content", None) or [] if getattr(part, "text", None)
    )
    if getattr(result, "is_error", False):
        return {"error": text or "Tool failed"}
    structured = getattr(result, "structured_content", None)
    return {"result": structured if structured is not None else text}
