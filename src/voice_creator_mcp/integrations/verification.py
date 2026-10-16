"""Verify the MCP server against Gemini's official MCP integrations.

* `mcp_tool_schemas`: every tool converts to a Gemini function declaration with the
  installed google-genai SDK, without the SDK silently dropping parameter shapes.
* `mcp_live_tool_call`: Gemini 3.8 Live Extended Thinking calls an MCP tool through
  `LiveMcpBridge` (asynchronous function calling) and speaks the result in the
  cloned voice.
* `interactions_mcp_server`: the Interactions API calls this server remotely via
  its `mcp_server` tool. Google's servers make that call, so it needs a public
  HTTPS URL, and the bearer token travels to Google: use a short-lived token with
  only the `voice:read` scope for this check.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from google.genai import types
from mcp import Client
from mcp.server.mcpserver import MCPServer

from voice_creator.diagnostics.report import Check, Outcome, failed, passed, skipped, warned
from voice_creator.live.config import LiveSessionOptions, build_live_config
from voice_creator_mcp.integrations.gemini_live import LiveMcpBridge, scheduling_for

TOOL_UNDER_TEST = "voice_list_profiles"
TOOL_PROMPT = (
    f"Call the {TOOL_UNDER_TEST} tool now, then tell me in one short sentence "
    "how many voice profiles I have."
)
DEFAULT_INTERACTIONS_MODEL = "gemini-3.8-flash"


@dataclass(frozen=True)
class InteractionsTarget:
    url: str
    bearer: str | None = None
    model: str = DEFAULT_INTERACTIONS_MODEL

    def tool(self) -> dict[str, Any]:
        tool: dict[str, Any] = {
            "type": "mcp_server",
            "name": "voice_creator",
            "url": self.url,
            "allowed_tools": [{"tools": [TOOL_UNDER_TEST]}],
        }
        if self.bearer:
            tool["headers"] = {"Authorization": f"Bearer {self.bearer}"}
        return tool


class McpChecks:
    def __init__(
        self,
        server: MCPServer,
        *,
        genai_client: Callable[[], Any],
        live_options: Callable[[], LiveSessionOptions],
        interactions: InteractionsTarget | None = None,
        live_timeout_s: float = 90.0,
    ) -> None:
        self._server = server
        self._genai_client = genai_client
        self._live_options = live_options
        self._interactions = interactions
        self._live_timeout_s = live_timeout_s

    def checks(self, *, requires: tuple[str, ...] = ()) -> list[Check]:
        return [
            Check("mcp_tool_schemas", self.tool_schemas),
            Check("mcp_live_tool_call", self.live_tool_call, requires=requires),
            Check("interactions_mcp_server", self.interactions_mcp_server, requires=requires),
        ]

    async def tool_schemas(self) -> Outcome:
        async with Client(self._server) as mcp:
            listed = await mcp.list_tools()
            await LiveMcpBridge(mcp).tools()  # the declarations must validate
        lossy = [
            f"{tool.name}: {path}" for tool in listed.tools for path in _lossy_paths(_schema(tool))
        ]
        count = len(listed.tools)
        if lossy:
            return warned(
                "The SDK's automatic MCP conversion drops the shape of these parameters; "
                "declare tools with LiveMcpBridge (parameters_json_schema) instead",
                tools=count,
                lossy=lossy,
            )
        return passed(f"All {count} tools convert to Gemini function declarations", tools=count)

    async def live_tool_call(self) -> Outcome:
        options = self._live_options()
        progress = _ToolRoundTrip()
        try:
            async with asyncio.timeout(self._live_timeout_s):
                await self._converse(options, progress)
        except TimeoutError:
            pass
        details = progress.details(options.model)
        if not progress.called:
            return failed(f"The model did not call {TOOL_UNDER_TEST}", **details)
        if not progress.spoke_after_result:
            return warned("The tool ran, but no spoken follow-up arrived", **details)
        return passed(
            f"{options.model} called an MCP tool asynchronously and spoke the result", **details
        )

    async def interactions_mcp_server(self) -> Outcome:
        target = self._interactions
        if target is None:
            return skipped("Pass --mcp-url with the server's public HTTPS MCP endpoint")
        interaction = await self._genai_client().aio.interactions.create(
            model=target.model, input=TOOL_PROMPT, tools=[target.tool()]
        )
        kinds = [getattr(step, "type", None) for step in getattr(interaction, "steps", None) or []]
        details = {
            "model": target.model,
            "steps": [kind for kind in kinds if kind],
            "answer": (getattr(interaction, "output_text", None) or "")[:300],
        }
        if "mcp_server_tool_call" not in kinds:
            return failed("Gemini did not call the MCP server", **details)
        if "mcp_server_tool_result" not in kinds:
            return failed("Gemini called the MCP server but got no result", **details)
        return passed("The Interactions API called this MCP server and used the result", **details)

    async def _converse(self, options: LiveSessionOptions, progress: _ToolRoundTrip) -> None:
        async with Client(self._server) as mcp:
            bridge = LiveMcpBridge(
                mcp, allowed_tools={TOOL_UNDER_TEST}, scheduling=scheduling_for(options.model)
            )
            config = {**build_live_config(options), "tools": await bridge.tools()}
            connect = self._genai_client().aio.live.connect
            async with connect(model=options.model, config=config) as live:
                recorder = _RecordingLive(live, progress)
                await live.send_realtime_input(text=TOOL_PROMPT)
                while not progress.done:
                    received = False
                    async for message in live.receive():
                        received = True
                        progress.observe(message)
                        await bridge.dispatch(message, recorder)
                        if progress.done:
                            break
                    if not received:
                        break
                await bridge.drain()


class _ToolRoundTrip:
    """Tracks call → result → spoken answer → end of turn."""

    def __init__(self) -> None:
        self.calls: list[str] = []
        self.responded = False
        self.spoke_after_result = False
        self.done = False

    @property
    def called(self) -> bool:
        return bool(self.calls)

    def observe(self, message: types.LiveServerMessage) -> None:
        if message.tool_call and message.tool_call.function_calls:
            self.calls += [call.name or "" for call in message.tool_call.function_calls]
        content = message.server_content
        if content is None or not self.responded:
            return
        parts = content.model_turn.parts if content.model_turn else None
        if any(part.inline_data for part in parts or []):
            self.spoke_after_result = True
        finished = content.turn_complete or _status(content.interaction_status) == "IDLE"
        if finished and self.spoke_after_result:
            self.done = True

    def details(self, model: str) -> dict[str, Any]:
        return {
            "model": model,
            "tool_calls": self.calls,
            "result_sent": self.responded,
            "spoke_after_result": self.spoke_after_result,
        }


class _RecordingLive:
    def __init__(self, live: Any, progress: _ToolRoundTrip) -> None:
        self._live = live
        self._progress = progress

    async def send_tool_response(self, *, function_responses: list[types.FunctionResponse]) -> None:
        await self._live.send_tool_response(function_responses=function_responses)
        self._progress.responded = True


def _schema(tool: Any) -> Mapping[str, Any]:
    return getattr(tool, "input_schema", None) or getattr(tool, "inputSchema", None) or {}


def _lossy_paths(schema: Mapping[str, Any], path: str = "") -> list[str]:
    """Parameters whose shape lives in `additionalProperties`, which the SDK drops."""
    found = []
    if isinstance(schema.get("additionalProperties"), Mapping):
        found.append(path or "(root)")
    for name, child in (schema.get("properties") or {}).items():
        if isinstance(child, Mapping):
            found += _lossy_paths(child, f"{path}.{name}" if path else name)
    items = schema.get("items")
    if isinstance(items, Mapping):
        found += _lossy_paths(items, f"{path}[]")
    for name, definition in (schema.get("$defs") or {}).items():
        if isinstance(definition, Mapping):
            found += _lossy_paths(definition, f"$defs.{name}")
    return found


def _status(value: Any) -> str | None:
    return None if value is None else str(getattr(value, "value", value))
