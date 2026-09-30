"""MCP server: thin tool adapters over the application services.

Tools translate expected failures into `ToolError` so MCP clients see the reason
(the SDK hides the message of any other exception). Blocking work (file I/O and
HTTP calls to Gemini) runs in a worker thread to keep the event loop responsive.
"""

from __future__ import annotations

import functools
from collections.abc import Awaitable, Callable
from typing import Annotated, Any, Literal, ParamSpec, TypeVar

import anyio
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations
from pydantic import Field

from voice_creator_mcp import __version__
from voice_creator_mcp.app.backend import GeminiBackend
from voice_creator_mcp.app.dialogue_studio import DialogueStudio
from voice_creator_mcp.app.outputs import OutputWriter
from voice_creator_mcp.app.voice_library import VoiceLibrary
from voice_creator_mcp.config import Settings
from voice_creator_mcp.errors import VoiceCreatorError
from voice_creator_mcp.voices.profiles import ProfileStore

P = ParamSpec("P")
R = TypeVar("R")

INSTRUCTIONS = """\
Create dialogue in the user's own voice with Gemini.

Setup (once): validate_voice_sample -> register_voice_profile (a 10-20 s, 24 kHz mono
16-bit WAV of the user speaking plus a WAV of them reading Google's consent statement)
-> replicate_voice_profile (creates a reusable Gemini TTS voice).

Scripted dialogue: preview_dialogue_script, then render_dialogue with a cast such as
{"Mario": "profile:mario", "Ana": "Kore"}. Script lines look like
`Speaker (style): text` and may contain vocal tags like <laugh> or <short pause>.

Live dialogue: live_dialogue(profile, prompts) speaks as the user with Gemini 3.8 Live.
Use mode="verbatim" to read given lines, or model="gemini-3.8-live-extended-thinking"
with thinking_level low|medium|high for harder reasoning.

Only use a voice with its owner's documented consent.
"""

_READ_ONLY = ToolAnnotations(read_only_hint=True, open_world_hint=False)
_LOCAL_WRITE = ToolAnnotations(read_only_hint=False, destructive_hint=False, open_world_hint=False)
_LOCAL_DELETE = ToolAnnotations(read_only_hint=False, destructive_hint=True, open_world_hint=True)
_REMOTE_READ = ToolAnnotations(read_only_hint=True, open_world_hint=True)
_REMOTE_WRITE = ToolAnnotations(read_only_hint=False, destructive_hint=False, open_world_hint=True)
_REMOTE_DELETE = ToolAnnotations(read_only_hint=False, destructive_hint=True, open_world_hint=True)


def create_server(settings: Settings | None = None) -> MCPServer:
    """Composition root wiring the real Gemini backend."""
    settings = settings or Settings.from_env()
    backend = GeminiBackend(settings)
    library = VoiceLibrary(ProfileStore(settings.data_dir), backend)
    studio = DialogueStudio(
        library, backend, OutputWriter(settings.output_dir), default_live_model=settings.live_model
    )
    return build_server(library, studio)


def build_server(library: VoiceLibrary, studio: DialogueStudio) -> MCPServer:
    server = MCPServer(name="google-voice-creator", instructions=INSTRUCTIONS, version=__version__)
    _register_voice_tools(server, library)
    _register_dialogue_tools(server, studio)
    return server


def _tool_errors(fn: Callable[P, Awaitable[R]]) -> Callable[P, Awaitable[R]]:
    @functools.wraps(fn)
    async def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
        try:
            return await fn(*args, **kwargs)
        except (VoiceCreatorError, ValueError) as exc:
            raise ToolError(str(exc)) from exc

    return wrapper


async def _in_thread(fn: Callable[..., R], *args: Any, **kwargs: Any) -> R:
    return await anyio.to_thread.run_sync(functools.partial(fn, *args, **kwargs))


def _register_voice_tools(server: MCPServer, library: VoiceLibrary) -> None:
    @server.tool(annotations=_READ_ONLY)
    @_tool_errors
    async def validate_voice_sample(
        path: Annotated[str, Field(description="Absolute path to a WAV file")],
        purpose: Annotated[
            Literal["live", "voices_api", "consent"],
            Field(description="Which Gemini requirement to check the recording against"),
        ] = "live",
    ) -> dict[str, Any]:
        """Check a recording's length, format, loudness, clipping and silence before use."""
        report = await _in_thread(library.validate_sample, path, purpose)
        return report.to_dict()

    @server.tool(annotations=_LOCAL_WRITE)
    @_tool_errors
    async def register_voice_profile(
        name: Annotated[str, Field(description="Profile id: lowercase letters, digits, - or _")],
        display_name: Annotated[str, Field(description="Human-readable speaker name")],
        sample_path: Annotated[str, Field(description="10-20 s, 24 kHz mono 16-bit WAV")],
        consent_path: Annotated[
            str, Field(description="WAV of the same speaker reading Google's consent statement")
        ],
        language_code: Annotated[str | None, Field(description="BCP-47, e.g. es-MX")] = None,
        overwrite: bool = False,
    ) -> dict[str, Any]:
        """Save a voice sample and consent recording as a private local voice profile."""
        return await _in_thread(
            library.register_profile,
            name,
            display_name=display_name,
            sample_path=sample_path,
            consent_path=consent_path,
            language_code=language_code,
            overwrite=overwrite,
        )

    @server.tool(annotations=_REMOTE_WRITE)
    @_tool_errors
    async def replicate_voice_profile(
        name: str,
        store: Annotated[
            bool,
            Field(
                description="true: stored Gemini voice id (up to 1 year); "
                "false: client-held key that expires after about 7 days"
            ),
        ] = True,
    ) -> dict[str, Any]:
        """Create a Gemini TTS voice from a profile so render_dialogue can use `profile:<name>`."""
        return await _in_thread(library.replicate_profile, name, store=store)

    @server.tool(annotations=_READ_ONLY)
    @_tool_errors
    async def list_voice_profiles() -> dict[str, Any]:
        """List local voice profiles (secrets masked)."""
        return {"profiles": await _in_thread(library.list_profiles)}

    @server.tool(annotations=_LOCAL_DELETE)
    @_tool_errors
    async def delete_voice_profile(name: str, delete_remote_voice: bool = False) -> dict[str, Any]:
        """Delete a local profile and its recordings; optionally delete its stored Gemini voice."""
        return await _in_thread(library.delete_profile, name, delete_remote=delete_remote_voice)

    @server.tool(annotations=_REMOTE_WRITE)
    @_tool_errors
    async def design_voice(
        description: Annotated[
            str, Field(description="Natural-language voice description (timbre, age, accent, pace)")
        ],
        display_name: str | None = None,
        language_code: str | None = None,
    ) -> dict[str, Any]:
        """Design a new synthetic voice from a text description (stored in the Gemini project)."""
        return await _in_thread(
            library.design_voice,
            description,
            display_name=display_name,
            language_code=language_code,
        )

    @server.tool(annotations=_REMOTE_READ)
    @_tool_errors
    async def list_voices(
        types: Annotated[
            list[Literal["prebuilt", "prompted", "replicated"]] | None,
            Field(description="Filter by voice type"),
        ] = None,
        search: str | None = None,
        page_size: Annotated[int | None, Field(ge=1, le=100)] = None,
        page_token: str | None = None,
    ) -> dict[str, Any]:
        """List your stored Gemini voices followed by the prebuilt catalog."""
        return await _in_thread(
            library.list_voices,
            types=types,
            search=search,
            page_size=page_size,
            page_token=page_token,
        )

    @server.tool(annotations=_REMOTE_DELETE)
    @_tool_errors
    async def delete_voice(voice_id: str) -> dict[str, Any]:
        """Permanently delete a stored Gemini voice (designed or replicated)."""
        return await _in_thread(library.delete_voice, voice_id)


def _register_dialogue_tools(server: MCPServer, studio: DialogueStudio) -> None:
    @server.tool(annotations=_READ_ONLY)
    @_tool_errors
    async def preview_dialogue_script(script: str) -> dict[str, Any]:
        """Parse a `Speaker (style): text` script and list its speakers without calling Gemini."""
        return studio.preview_script(script)

    @server.tool(annotations=_REMOTE_WRITE)
    @_tool_errors
    async def render_dialogue(
        script: Annotated[str, Field(description="Lines like `Mario (warm): Hola <laugh>`")],
        cast: Annotated[
            dict[str, str],
            Field(
                description="Speaker -> voice: `profile:<name>`, a stored voice id, "
                "a voicekey_..., or a prebuilt voice such as Kore"
            ),
        ],
        output_name: str | None = None,
        gap_ms: Annotated[int, Field(ge=0, le=10_000)] = 300,
        language: Annotated[str | None, Field(description="BCP-47 language, e.g. en-US")] = None,
    ) -> dict[str, Any]:
        """Render a multi-speaker script with Gemini 3.8 Flash TTS into one WAV + transcript."""
        return await _in_thread(
            studio.render_dialogue,
            script,
            cast,
            output_name=output_name,
            gap_ms=gap_ms,
            language=language,
        )

    @server.tool(annotations=_REMOTE_WRITE)
    @_tool_errors
    async def live_dialogue(
        profile: Annotated[str, Field(description="Voice profile to speak with")],
        prompts: Annotated[list[str], Field(min_length=1, description="One user turn per item")],
        mode: Annotated[
            Literal["improvise", "verbatim"],
            Field(description="improvise: answer in character; verbatim: read the text as is"),
        ] = "improvise",
        persona: str | None = None,
        model: Annotated[
            str | None,
            Field(description="gemini-3.8-live (default) or gemini-3.8-live-extended-thinking"),
        ] = None,
        thinking_level: Annotated[
            Literal["low", "medium", "high"] | None,
            Field(description="Extended Thinking only"),
        ] = None,
        language_code: str | None = None,
        extra_instructions: str | None = None,
        output_name: str | None = None,
    ) -> dict[str, Any]:
        """Hold a Gemini 3.8 Live conversation that answers in your replicated voice."""
        return await studio.live_dialogue(
            profile,
            prompts,
            mode=mode,
            persona=persona,
            model=model,
            thinking_level=thinking_level,
            language_code=language_code,
            extra_instructions=extra_instructions,
            output_name=output_name,
        )
