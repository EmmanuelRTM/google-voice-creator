import asyncio
from types import SimpleNamespace as NS
from typing import Any

import pytest
from google.genai import types

from voice_creator_mcp.integrations.gemini_live import LiveMcpBridge


class FakeMcpSession:
    def __init__(self, *, fail: bool = False, delay: float = 0.0) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.fail = fail
        self.delay = delay

    async def list_tools(self) -> Any:
        return NS(
            tools=[
                NS(
                    name="voice_list_profiles",
                    description="List profiles",
                    input_schema={"type": "object", "properties": {}},
                ),
                NS(
                    name="voice_render_dialogue",
                    description="Render",
                    input_schema={
                        "type": "object",
                        "properties": {"script": {"type": "string"}},
                        "required": ["script"],
                    },
                ),
            ]
        )

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> Any:
        self.calls.append((name, arguments))
        await asyncio.sleep(self.delay)
        if self.fail:
            return NS(is_error=True, structured_content=None, content=[NS(text="boom")])
        return NS(is_error=False, structured_content={"render_id": "r1"}, content=[])


class FakeLiveSession:
    def __init__(self) -> None:
        self.responses: list[types.FunctionResponse] = []

    async def send_tool_response(self, *, function_responses: list[types.FunctionResponse]) -> None:
        self.responses.extend(function_responses)


def tool_call(name: str, call_id: str = "c1", **args: Any) -> types.LiveServerMessage:
    return types.LiveServerMessage(
        tool_call=types.LiveServerToolCall(
            function_calls=[types.FunctionCall(id=call_id, name=name, args=args)]
        )
    )


async def test_declarations_are_non_blocking_json_schema_functions() -> None:
    bridge = LiveMcpBridge(FakeMcpSession())

    (tool,) = await bridge.tools()

    names = [decl.name for decl in tool.function_declarations]
    assert names == ["voice_list_profiles", "voice_render_dialogue"]
    for decl in tool.function_declarations:
        assert decl.behavior == types.Behavior.NON_BLOCKING
        assert decl.parameters_json_schema["type"] == "object"


async def test_allow_list_limits_the_exposed_tools() -> None:
    bridge = LiveMcpBridge(FakeMcpSession(), allowed_tools={"voice_render_dialogue"})
    (tool,) = await bridge.tools()
    assert [decl.name for decl in tool.function_declarations] == ["voice_render_dialogue"]


async def test_tool_calls_run_and_answer_with_scheduling() -> None:
    mcp, live = FakeMcpSession(), FakeLiveSession()
    bridge = LiveMcpBridge(mcp)

    await bridge.dispatch(tool_call("voice_render_dialogue", script="Ana: Hi"), live)
    await bridge.drain()

    assert mcp.calls == [("voice_render_dialogue", {"script": "Ana: Hi"})]
    (response,) = live.responses
    assert response.id == "c1"
    assert response.response == {"result": {"render_id": "r1"}}
    assert response.scheduling == types.FunctionResponseScheduling.WHEN_IDLE


async def test_tool_errors_are_reported_to_the_model() -> None:
    live = FakeLiveSession()
    bridge = LiveMcpBridge(FakeMcpSession(fail=True))

    await bridge.dispatch(tool_call("voice_render_dialogue", script="x"), live)
    await bridge.drain()

    assert live.responses[0].response == {"error": "boom"}


async def test_calls_outside_the_allow_list_are_refused() -> None:
    mcp, live = FakeMcpSession(), FakeLiveSession()
    bridge = LiveMcpBridge(mcp, allowed_tools={"voice_list_profiles"})

    await bridge.dispatch(tool_call("voice_render_dialogue", script="x"), live)
    await bridge.drain()

    assert mcp.calls == []
    assert "not allowed" in live.responses[0].response["error"]


async def test_cancellation_stops_pending_calls() -> None:
    mcp, live = FakeMcpSession(delay=10), FakeLiveSession()
    bridge = LiveMcpBridge(mcp)

    await bridge.dispatch(tool_call("voice_render_dialogue", call_id="slow", script="x"), live)
    await asyncio.sleep(0)
    await bridge.dispatch(
        types.LiveServerMessage(
            tool_call_cancellation=types.LiveServerToolCallCancellation(ids=["slow"])
        ),
        live,
    )
    await bridge.drain()

    assert live.responses == []


async def test_other_messages_are_ignored() -> None:
    bridge = LiveMcpBridge(FakeMcpSession())
    assert await bridge.dispatch(types.LiveServerMessage(), FakeLiveSession()) is False


@pytest.mark.parametrize("scheduling", ["SILENT", "INTERRUPT"])
async def test_scheduling_is_configurable(scheduling: str) -> None:
    live = FakeLiveSession()
    bridge = LiveMcpBridge(
        FakeMcpSession(), scheduling=types.FunctionResponseScheduling(scheduling)
    )
    await bridge.dispatch(tool_call("voice_list_profiles"), live)
    await bridge.drain()
    assert live.responses[0].scheduling == types.FunctionResponseScheduling(scheduling)
