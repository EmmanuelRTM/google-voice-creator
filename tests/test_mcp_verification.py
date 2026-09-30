import asyncio
import json
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace as NS
from typing import Any

import pytest
from google.genai import types
from mcp.server.mcpserver import MCPServer

from voice_creator.config import Settings
from voice_creator.diagnostics import run_checks
from voice_creator.live.config import LiveSessionOptions, ReplicatedVoice
from voice_creator_mcp.integrations.verification import (
    TOOL_UNDER_TEST,
    InteractionsTarget,
    McpChecks,
)
from voice_creator_mcp.server.composition import create_server

from fakes import FakeBackend

BEARER = "mcp-bearer-secret-123"


class FakeLive:
    """Calls the tool on the first prompt, then speaks once the result arrives."""

    def __init__(self, *, call_tool: bool = True) -> None:
        self.call_tool = call_tool
        self.responses: list[types.FunctionResponse] = []
        self._queue: asyncio.Queue[types.LiveServerMessage] = asyncio.Queue()

    async def send_realtime_input(self, *, text: str) -> None:
        if self.call_tool:
            self._queue.put_nowait(
                types.LiveServerMessage(
                    tool_call=types.LiveServerToolCall(
                        function_calls=[types.FunctionCall(id="c1", name=TOOL_UNDER_TEST, args={})]
                    )
                )
            )
        else:
            self._speak()

    async def send_tool_response(self, *, function_responses: list[types.FunctionResponse]) -> None:
        self.responses.extend(function_responses)
        self._speak()

    def _speak(self) -> None:
        audio = types.Blob(data=b"\x01\x00" * 480, mime_type="audio/pcm;rate=24000")
        self._queue.put_nowait(
            types.LiveServerMessage(
                server_content=types.LiveServerContent(
                    model_turn=types.Content(parts=[types.Part(inline_data=audio)])
                )
            )
        )
        self._queue.put_nowait(
            types.LiveServerMessage(server_content=types.LiveServerContent(turn_complete=True))
        )

    async def receive(self):
        while True:
            message = await self._queue.get()
            yield message
            if message.server_content and message.server_content.turn_complete:
                return


class FakeGenai:
    def __init__(self, live: FakeLive | None = None, interaction: Any = None) -> None:
        self.live_session = live or FakeLive()
        self.connections: list[tuple[str, dict[str, Any]]] = []
        self.interaction_requests: list[dict[str, Any]] = []
        self._interaction = interaction
        self.aio = NS(live=NS(connect=self._connect), interactions=NS(create=self._create))

    @asynccontextmanager
    async def _connect(self, *, model: str, config: dict[str, Any]):
        self.connections.append((model, config))
        yield self.live_session

    async def _create(self, **request: Any) -> Any:
        self.interaction_requests.append(request)
        return self._interaction


@pytest.fixture
def server(tmp_path: Path) -> MCPServer:
    settings = Settings.from_env({"VOICE_CREATOR_DATA_DIR": str(tmp_path)}, home=tmp_path)
    return create_server(settings, backend=FakeBackend())


def options() -> LiveSessionOptions:
    return LiveSessionOptions(
        model="gemini-3.8-live-extended-thinking",
        voice=ReplicatedVoice(sample_wav=b"s", consent_wav=b"c"),
        thinking_level="low",
    )


def checks(server, genai, target=None, **kwargs) -> McpChecks:
    return McpChecks(
        server,
        genai_client=lambda: genai,
        live_options=options,
        interactions=target,
        **kwargs,
    )


async def run(check) -> Any:
    return await check()


async def test_our_tools_convert_losslessly_with_the_installed_sdk(server) -> None:
    outcome = await checks(server, FakeGenai()).tool_schemas()

    assert outcome.status == "pass"
    assert outcome.details["tools"] >= 10


async def test_dict_parameters_are_flagged_as_lossy() -> None:
    lossy = MCPServer("lossy")

    @lossy.tool()
    def tag(labels: dict[str, str]) -> str:
        return "ok"

    outcome = await checks(lossy, FakeGenai()).tool_schemas()

    assert outcome.status == "warn"
    assert outcome.details["lossy"] == ["tag: labels"]


async def test_live_session_calls_the_mcp_tool_and_speaks_the_result(server) -> None:
    genai = FakeGenai()

    outcome = await checks(server, genai).live_tool_call()

    assert outcome.status == "pass", outcome.summary
    model, config = genai.connections[0]
    assert model == "gemini-3.8-live-extended-thinking"
    declaration = config["tools"][0].function_declarations[0]
    assert declaration.name == TOOL_UNDER_TEST
    assert declaration.behavior == types.Behavior.NON_BLOCKING
    assert "replicated_voice_config" in config["speech_config"]["voice_config"]
    response = genai.live_session.responses[0]
    assert "profiles" in response.response["result"]


async def test_live_session_without_a_tool_call_fails(server) -> None:
    genai = FakeGenai(FakeLive(call_tool=False))

    outcome = await checks(server, genai, live_timeout_s=0.2).live_tool_call()

    assert outcome.status == "fail"
    assert "did not call" in outcome.summary


async def test_interactions_check_needs_a_public_url(server) -> None:
    outcome = await checks(server, FakeGenai()).interactions_mcp_server()
    assert outcome.status == "skip"
    assert "--mcp-url" in outcome.summary


async def test_interactions_mcp_server_round_trip(server) -> None:
    interaction = NS(
        steps=[
            NS(type="mcp_server_tool_call", name=TOOL_UNDER_TEST, server_name="voice_creator"),
            NS(type="mcp_server_tool_result", name=TOOL_UNDER_TEST),
        ],
        output_text="You have one voice profile.",
    )
    genai = FakeGenai(interaction=interaction)
    target = InteractionsTarget(url="https://voice.example.com/mcp", bearer=BEARER)

    mcp_checks = checks(server, genai, target)
    report = await run_checks(mcp_checks.checks(), secrets=[BEARER])

    result = next(r for r in report.results if r.name == "interactions_mcp_server")
    assert result.status == "pass"
    request = genai.interaction_requests[0]
    tool = request["tools"][0]
    assert tool["type"] == "mcp_server"
    assert tool["url"] == "https://voice.example.com/mcp"
    assert tool["headers"] == {"Authorization": f"Bearer {BEARER}"}
    assert tool["allowed_tools"] == [{"tools": [TOOL_UNDER_TEST]}]
    assert BEARER not in json.dumps(report.to_dict())


def test_interactions_tool_matches_the_sdk_schema() -> None:
    from google.genai._gaos.types.interactions.mcpserver import MCPServer as McpServerTool

    tool = InteractionsTarget(url="https://voice.example.com/mcp", bearer="t").tool()
    validated = McpServerTool.model_validate(tool)
    assert validated.allowed_tools[0].tools == [TOOL_UNDER_TEST]
