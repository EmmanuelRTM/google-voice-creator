"""Assemble the MCP server from its tools and resources."""

from __future__ import annotations

from mcp.server.mcpserver import MCPServer

from voice_creator_mcp import __version__
from voice_creator_mcp.app.workspace import WorkspaceProvider
from voice_creator_mcp.server.dialogue_tools import register_dialogue_tools
from voice_creator_mcp.server.guard import ToolGuard
from voice_creator_mcp.server.options import ServerOptions
from voice_creator_mcp.server.resources import register_render_resources
from voice_creator_mcp.server.voice_tools import register_voice_tools

SERVER_NAME = "voice_creator_mcp"

INSTRUCTIONS = """\
Create dialogue in the user's own voice with Gemini.

Setup (once): voice_validate_recording -> voice_create_profile (a 10-20 s, 24 kHz mono
16-bit WAV of the user plus a WAV of them reading Google's consent statement; pass files
as *_base64, or *_path on local servers) -> voice_replicate_profile.

Scripted dialogue: voice_preview_script, then voice_render_dialogue with a cast such as
{"Mario": "profile:mario", "Ana": "Kore"}. Lines look like `Speaker (style): text` and
may contain vocal tags like <laugh> or <short pause>.

Live dialogue: voice_live_dialogue(profile, prompts) speaks as the user with Gemini 3.8
Live. Use mode="verbatim" to read given lines, or model="gemini-3.8-live-extended-thinking"
with thinking_level low|medium|high for turns that need reasoning.

Results link to resources voice://renders/{render_id}/audio and /transcript.
Only use a voice with its owner's documented consent.
"""


def build_server(workspaces: WorkspaceProvider, options: ServerOptions | None = None) -> MCPServer:
    guard = ToolGuard(workspaces, options or ServerOptions())
    server = MCPServer(name=SERVER_NAME, instructions=INSTRUCTIONS, version=__version__)
    register_voice_tools(server, guard)
    register_dialogue_tools(server, guard)
    register_render_resources(server, guard)
    return server
