"""Dialogue tools: script preview, scripted renders (TTS) and live dialogue (Live API)."""

from typing import Annotated, Literal

import anyio
from mcp.server.mcpserver import Context, MCPServer
from mcp.types import ToolAnnotations
from pydantic import Field

from voice_creator_mcp.server import schemas as out
from voice_creator_mcp.server.guard import ToolGuard, in_thread, report_progress

MAX_SCRIPT_CHARS = 20_000
MAX_CAST = 20
MAX_PROMPTS = 20
MAX_PROMPT_CHARS = 4_000

Script = Annotated[
    str,
    Field(
        description="Lines like `Mario (warm): Hola <laugh>`; indented lines continue the "
        "previous line; `#` starts a comment",
        max_length=MAX_SCRIPT_CHARS,
    ),
]
OutputName = Annotated[
    str | None, Field(description="File name for the render (no extension)", max_length=80)
]

_LOCAL_READ = ToolAnnotations(read_only_hint=True, idempotent_hint=True, open_world_hint=False)
_GENERATE = ToolAnnotations(read_only_hint=False, destructive_hint=False, open_world_hint=True)


def register_dialogue_tools(server: MCPServer, guard: ToolGuard) -> None:
    redact = guard.options.redact_paths

    @guard.register(
        server,
        name="voice_preview_script",
        title="Preview a dialogue script",
        annotations=_LOCAL_READ,
    )
    async def voice_preview_script(script: Script) -> out.ScriptPreview:
        """Parse a script and list its speakers without calling Gemini (free)."""
        return out.ScriptPreview.model_validate(guard.workspace().studio.preview_script(script))

    @guard.register(
        server,
        name="voice_render_dialogue",
        title="Render a scripted dialogue",
        annotations=_GENERATE,
    )
    async def voice_render_dialogue(
        script: Script,
        cast: Annotated[
            dict[str, str],
            Field(
                description="Speaker -> voice: `profile:<name>`, a stored voice id, "
                "a voicekey_..., or a prebuilt voice such as Kore",
                max_length=MAX_CAST,
            ),
        ],
        ctx: Context,
        output_name: OutputName = None,
        gap_ms: Annotated[int, Field(ge=0, le=10_000, description="Silence between lines")] = 300,
        language: Annotated[
            str | None, Field(description="BCP-47, e.g. en-US", max_length=35)
        ] = None,
    ) -> out.RenderOutput:
        """Render a multi-speaker script with Gemini 3.8 Flash TTS into one WAV + transcript.

        Returns MCP resource URIs for the audio and transcript. Reports progress per line.
        """

        def on_progress(done: int, total: int) -> None:
            anyio.from_thread.run(
                report_progress, ctx, done, total, f"Rendered line {done}/{total}"
            )

        result = await in_thread(
            guard.workspace().studio.render_dialogue,
            script,
            cast,
            output_name=output_name,
            gap_ms=gap_ms,
            language=language,
            on_progress=on_progress,
        )
        return out.RenderOutput(
            **out.output_links(result, redact=redact),
            duration_seconds=result["duration_seconds"],
            segments=[out.Segment.model_validate(s) for s in result["segments"]],
        )

    @guard.register(
        server,
        name="voice_live_dialogue",
        title="Speak live in your voice",
        annotations=_GENERATE,
    )
    async def voice_live_dialogue(
        profile: Annotated[
            str,
            Field(description="Voice profile to speak with", pattern=r"^[a-z0-9][a-z0-9_-]{0,63}$"),
        ],
        prompts: Annotated[
            list[Annotated[str, Field(max_length=MAX_PROMPT_CHARS)]],
            Field(min_length=1, max_length=MAX_PROMPTS, description="One user turn per item"),
        ],
        ctx: Context,
        mode: Annotated[
            Literal["improvise", "verbatim"],
            Field(description="improvise: answer in character; verbatim: read the text as is"),
        ] = "improvise",
        persona: Annotated[str | None, Field(max_length=2000)] = None,
        model: Annotated[
            Literal["gemini-3.8-live", "gemini-3.8-live-extended-thinking"] | None,
            Field(description="Defaults to the server's configured Live model"),
        ] = None,
        thinking_level: Annotated[
            Literal["low", "medium", "high"] | None,
            Field(description="Extended Thinking only"),
        ] = None,
        language_code: Annotated[str | None, Field(max_length=35)] = None,
        extra_instructions: Annotated[str | None, Field(max_length=2000)] = None,
        output_name: OutputName = None,
    ) -> out.LiveOutput:
        """Hold a Gemini 3.8 Live conversation that answers in your cloned voice.

        Use mode="verbatim" to read given lines (may paraphrase; prefer
        voice_render_dialogue for exact words). Reports progress per turn.
        """

        async def on_turn(done: int, total: int) -> None:
            await report_progress(ctx, done, total, f"Finished turn {done}/{total}")

        result = await guard.workspace().studio.live_dialogue(
            profile,
            prompts,
            mode=mode,
            persona=persona,
            model=model,
            thinking_level=thinking_level,
            language_code=language_code,
            extra_instructions=extra_instructions,
            output_name=output_name,
            on_turn=on_turn,
        )
        return out.LiveOutput(
            **out.output_links(result, redact=redact),
            transcript=result["transcript"],
            duration_seconds=result["duration_seconds"],
            model=result["model"],
            turns=[out.LiveTurnInfo.model_validate(turn) for turn in result["turns"]],
            warnings=result["warnings"],
        )
