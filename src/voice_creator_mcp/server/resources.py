"""Rendered audio and transcripts as MCP resources, scoped to the caller's workspace.

Remote clients cannot open server paths, so every render is also addressable as
`voice://renders/{render_id}/audio` (audio/wav) and `/transcript` (text/plain).
"""

from mcp.server.mcpserver import MCPServer

from voice_creator_mcp.server.guard import ToolGuard, in_thread


def register_render_resources(server: MCPServer, guard: ToolGuard) -> None:
    @server.resource(
        "voice://renders/{render_id}/audio",
        name="render_audio",
        title="Rendered dialogue audio",
        description="WAV audio from voice_render_dialogue or voice_live_dialogue",
        mime_type="audio/wav",
    )
    async def render_audio(render_id: str) -> bytes:
        path = await guard.read_render(render_id, ".wav")
        return await in_thread(path.read_bytes)

    @server.resource(
        "voice://renders/{render_id}/transcript",
        name="render_transcript",
        title="Rendered dialogue transcript",
        description="Plain-text transcript of a render (vocal tags removed)",
        mime_type="text/plain",
    )
    async def render_transcript(render_id: str) -> str:
        path = await guard.read_render(render_id, ".txt")
        return await in_thread(path.read_text, encoding="utf-8")
