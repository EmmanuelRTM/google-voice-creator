"""Gemini 3.8 Live Extended Thinking + the Voice Creator MCP tools, in your cloned voice.

Uses the google-genai SDK directly. `LiveMcpBridge` declares the MCP tools the way
Gemini 3.8 expects (asynchronous, NON_BLOCKING functions with their JSON Schema),
runs each tool call while the model keeps talking, and answers with
`send_tool_response` when the model is idle.

    uv pip install -e ".[mcp]"
    python examples/live_mcp_bridge.py --profile me \\
        "Check how many voice profiles I have and tell me about them"

The MCP server is launched over stdio, or reached over Streamable HTTP when
VOICE_CREATOR_MCP_URL (and VOICE_CREATOR_MCP_BEARER) are set.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import os
from pathlib import Path

import httpx2
from google import genai
from google.genai import types
from mcp import Client, StdioServerParameters
from mcp.client.streamable_http import streamable_http_client

from voice_creator import VoiceCreator
from voice_creator.audio.pcm import PcmAudio, write_wav
from voice_creator.live.config import (
    GEMINI_38_LIVE_EXTENDED_THINKING,
    LiveSessionOptions,
    build_live_config,
)
from voice_creator.live.instructions import build_system_instruction
from voice_creator_mcp.integrations.gemini_live import LiveMcpBridge

TOOLS = ["voice_list_profiles", "voice_list_gemini_voices", "voice_preview_script"]
SERVER_ENV = ("GEMINI_API_KEY", "GEMINI_API_KEY_FILE", "VOICE_CREATOR_DATA_DIR")


@contextlib.asynccontextmanager
async def mcp_client():
    """Connect to the MCP server: remote over Streamable HTTP, or a local stdio child."""
    async with contextlib.AsyncExitStack() as stack:
        url = os.environ.get("VOICE_CREATOR_MCP_URL")
        if url:
            bearer = os.environ["VOICE_CREATOR_MCP_BEARER"]
            # You own the HTTP client, so you enter and close it; the SDK never closes one it
            # did not create. Audio resources can exceed the SDK's 1 MiB default per SSE event
            # on legacy (2025-11-25) connections, so lift that cap.
            http = await stack.enter_async_context(
                httpx2.AsyncClient(
                    headers={"Authorization": f"Bearer {bearer}"},
                    timeout=httpx2.Timeout(30.0, read=300.0),
                )
            )
            server = streamable_http_client(url, http_client=http, max_sse_event_size=None)
        else:
            server = StdioServerParameters(
                command="voice-creator-mcp",
                args=["--transport", "stdio"],
                env={name: os.environ[name] for name in SERVER_ENV if name in os.environ},
            )
        yield await stack.enter_async_context(Client(server))


async def converse(profile: str, prompt: str, output: Path) -> None:
    creator = VoiceCreator()
    options = LiveSessionOptions(
        model=GEMINI_38_LIVE_EXTENDED_THINKING,
        voice=creator.voices.live_voice(profile),
        system_instruction=build_system_instruction(
            "improvise", persona=creator.voices.profile(profile).display_name
        ),
        thinking_level="low",
    )
    audio = bytearray()
    async with mcp_client() as mcp:
        bridge = LiveMcpBridge(mcp, allowed_tools=TOOLS)
        config = {**build_live_config(options), "tools": await bridge.tools()}
        client = genai.Client(api_key=creator.settings.require_api_key())
        async with client.aio.live.connect(model=options.model, config=config) as live:
            await live.send_realtime_input(text=prompt)
            done = False
            while not done:
                async for message in live.receive():
                    if await bridge.dispatch(message, live):
                        continue  # a tool call: it runs in the background
                    content = message.server_content
                    if content is None:
                        continue
                    for part in content.model_turn.parts if content.model_turn else []:
                        if part.inline_data and part.inline_data.data:
                            audio += part.inline_data.data
                    if content.output_transcription and content.output_transcription.text:
                        print(content.output_transcription.text, end="", flush=True)
                    if content.turn_complete:
                        # Extended Thinking may answer in several turns: wait for IDLE,
                        # and for the model to react to every tool result.
                        status = content.interaction_status
                        idle = status in (None, types.InteractionStatus.IDLE)
                        done = idle and not bridge.busy
                        if done:
                            break
            await bridge.drain()

    write_wav(output, PcmAudio(data=bytes(audio), sample_rate=24_000))
    print(f"\nSaved {output}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("prompt")
    parser.add_argument("--profile", required=True)
    parser.add_argument("--output", type=Path, default=Path("live-mcp-reply.wav"))
    args = parser.parse_args()
    asyncio.run(converse(args.profile, args.prompt, args.output))


if __name__ == "__main__":
    main()
