"""An ADK agent that talks in your cloned voice and uses the Voice Creator MCP tools.

Google's Agent Development Kit runs Gemini Live sessions with `Runner.run_live`. The
cloned voice goes in `RunConfig.speech_config` (built from your profile by
`VoiceCreator.live_speech_config`), and the MCP server is attached as an `McpToolset`:
launched over stdio by default, or remote over Streamable HTTP with a bearer token.

    uv pip install "google-adk[mcp]>=2.10" -e ".[mcp]"
    python examples/adk_voice_agent/agent.py --profile me \\
        --prompt "Introduce yourself and tell me how many voice profiles I have"

Environment: GEMINI_API_KEY (never a flag). For a remote server, also
VOICE_CREATOR_MCP_URL=https://voice.example.com/mcp and VOICE_CREATOR_MCP_BEARER.
Gemini 3.8 Live Extended Thinking only accepts asynchronous tools, so with
`--model gemini-3.8-live-extended-thinking` the MCP tools are declared NON_BLOCKING.
"""

from __future__ import annotations

import argparse
import asyncio
import os
from pathlib import Path

from google.adk.agents import Agent, LiveRequestQueue, RunConfig
from google.adk.agents.run_config import StreamingMode
from google.adk.runners import InMemoryRunner
from google.adk.tools.mcp_tool.mcp_session_manager import (
    StdioConnectionParams,
    StreamableHTTPConnectionParams,
)
from google.adk.tools.mcp_tool.mcp_toolset import McpToolset
from google.genai import types
from mcp import StdioServerParameters

from voice_creator import VoiceCreator
from voice_creator.audio.pcm import PcmAudio, write_wav
from voice_creator.live.config import GEMINI_38_LIVE, GEMINI_38_LIVE_EXTENDED_THINKING
from voice_creator.live.instructions import build_system_instruction

APP_NAME = "voice_creator"
# Read-only tools: a voice agent should not create or delete voices without a human.
AGENT_TOOLS = ["voice_list_profiles", "voice_list_gemini_voices", "voice_preview_script"]
# Variables the stdio server needs; everything else stays out of the child process.
SERVER_ENV = (
    "GEMINI_API_KEY",
    "GEMINI_API_KEY_FILE",
    "VOICE_CREATOR_DATA_DIR",
    "VOICE_CREATOR_OUTPUT_DIR",
)


class AsyncMcpToolset(McpToolset):
    """MCP tools declared NON_BLOCKING, as Gemini 3.8 Live Extended Thinking requires."""

    async def get_tools(self, readonly_context=None):
        tools = await super().get_tools(readonly_context)
        for tool in tools:
            tool.behavior = types.Behavior.NON_BLOCKING
            tool.response_scheduling = types.FunctionResponseScheduling.WHEN_IDLE
        return tools


def build_toolset(*, asynchronous: bool) -> McpToolset:
    url = os.environ.get("VOICE_CREATOR_MCP_URL")
    if url:
        params = StreamableHTTPConnectionParams(
            url=url,
            headers={"Authorization": f"Bearer {os.environ['VOICE_CREATOR_MCP_BEARER']}"},
            timeout=30,
        )
    else:
        params = StdioConnectionParams(
            server_params=StdioServerParameters(
                command="voice-creator-mcp",
                args=["--transport", "stdio"],
                env={name: os.environ[name] for name in SERVER_ENV if name in os.environ},
            ),
            timeout=30,
        )
    toolset = AsyncMcpToolset if asynchronous else McpToolset
    return toolset(connection_params=params, tool_filter=AGENT_TOOLS)


def build_agent(creator: VoiceCreator, profile: str, model: str) -> Agent:
    extended = model == GEMINI_38_LIVE_EXTENDED_THINKING
    persona = creator.voices.profile(profile).display_name
    return Agent(
        name="my_voice",
        model=model,
        instruction=build_system_instruction("improvise", persona=persona),
        tools=[build_toolset(asynchronous=extended)],
        generate_content_config=(
            types.GenerateContentConfig(
                thinking_config=types.ThinkingConfig(thinking_level=types.ThinkingLevel.LOW)
            )
            if extended
            else None
        ),
    )


def build_run_config(creator: VoiceCreator, profile: str) -> RunConfig:
    return RunConfig(
        streaming_mode=StreamingMode.BIDI,
        response_modalities=[types.Modality.AUDIO],
        speech_config=creator.live_speech_config(profile),
        output_audio_transcription=types.AudioTranscriptionConfig(),
        session_resumption=types.SessionResumptionConfig(),
        context_window_compression=types.ContextWindowCompressionConfig(
            sliding_window=types.SlidingWindow()
        ),
    )


async def converse(profile: str, prompt: str, model: str, output: Path) -> None:
    creator = VoiceCreator()
    runner = InMemoryRunner(agent=build_agent(creator, profile, model), app_name=APP_NAME)
    session = await runner.session_service.create_session(app_name=APP_NAME, user_id="local")
    queue = LiveRequestQueue()
    queue.send_content(types.Content(role="user", parts=[types.Part(text=prompt)]))

    audio = bytearray()
    running_tools, spoke_since_tools = 0, False
    try:
        async for event in runner.run_live(
            user_id="local",
            session_id=session.id,
            live_request_queue=queue,
            run_config=build_run_config(creator, profile),
        ):
            running_tools += len(event.get_function_calls())
            if event.get_function_responses():
                running_tools -= len(event.get_function_responses())
                spoke_since_tools = False
            for part in event.content.parts if event.content and event.content.parts else []:
                blob = part.inline_data
                if blob and blob.data and (blob.mime_type or "").startswith("audio/pcm"):
                    audio += blob.data
                    spoke_since_tools = True
            spoken = event.output_transcription
            if spoken and spoken.finished and spoken.text:
                print(f"{profile}: {spoken.text}")
            # Extended Thinking can go idle while an async tool runs: wait for its result
            # to be spoken before ending this one-shot demo.
            idle = event.turn_complete and (
                event.interaction_status != types.InteractionStatus.IN_PROGRESS
            )
            if idle and not running_tools and spoke_since_tools:
                break
    finally:
        queue.close()
        await runner.close()

    write_wav(output, PcmAudio(data=bytes(audio), sample_rate=24_000))
    print(f"Saved {output}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--profile", required=True)
    parser.add_argument("--prompt", required=True)
    parser.add_argument(
        "--model",
        choices=(GEMINI_38_LIVE, GEMINI_38_LIVE_EXTENDED_THINKING),
        default=GEMINI_38_LIVE,
    )
    parser.add_argument("--output", type=Path, default=Path("adk-reply.wav"))
    args = parser.parse_args()
    asyncio.run(converse(args.profile, args.prompt, args.model, args.output))


if __name__ == "__main__":
    main()
