"""Genkit flows around the voice core: traced, testable in the Genkit Dev UI, deployable.

The voice work runs in `voice_creator` (Gemini TTS through the Interactions API, and
Gemini 3.8 Live with the cloned voice); each Gemini call is a traced step (`ai.run`).
No Genkit model plugin is needed.

    uv pip install "genkit>=0.12" -e .
    genkit start -- python examples/genkit_flow/flows.py      # opens the Dev UI

Genkit traces record flow inputs and outputs, so these flows never return
credentials: browser apps get ephemeral Live tokens from `POST /v1/live-tokens`.
"""

from __future__ import annotations

import asyncio
from typing import Any, Literal

from genkit import Genkit
from pydantic import BaseModel, Field

from voice_creator import VoiceCreator
from voice_creator.dialogue.cast import build_cast


class CastEntry(BaseModel):
    speaker: str = Field(max_length=40)
    voice: str = Field(
        max_length=200, description="profile:<name>, a voice ID, or a prebuilt voice"
    )


class RenderRequest(BaseModel):
    script: str = Field(description="Lines like `Speaker (style): text`")
    cast: list[CastEntry] = Field(min_length=1, max_length=20)
    output_name: str | None = None


class SpeakRequest(BaseModel):
    profile: str
    prompts: list[str] = Field(min_length=1, max_length=20)
    mode: Literal["improvise", "verbatim"] = "improvise"
    model: Literal["gemini-3.8-live", "gemini-3.8-live-extended-thinking"] | None = None
    thinking_level: Literal["low", "medium", "high"] | None = None


def register_flows(ai: Genkit, creator: VoiceCreator) -> dict[str, Any]:
    @ai.flow(name="renderDialogue")
    async def render_dialogue(request: RenderRequest) -> dict[str, Any]:
        """Render a multi-voice script with Gemini TTS (cloned or prebuilt voices)."""
        cast = build_cast((entry.speaker, entry.voice) for entry in request.cast)

        async def preview() -> dict[str, Any]:
            return creator.dialogue.preview_script(request.script)

        async def render() -> dict[str, Any]:
            return await asyncio.to_thread(
                creator.dialogue.render_dialogue,
                request.script,
                cast,
                output_name=request.output_name,
            )

        await ai.run(name="previewScript", fn=preview)
        return await ai.run(name="geminiTts", fn=render)

    @ai.flow(name="speakAsProfile")
    async def speak_as_profile(request: SpeakRequest) -> dict[str, Any]:
        """Speak the prompts in the profile's cloned voice with Gemini 3.8 Live."""

        async def live() -> dict[str, Any]:
            return await creator.dialogue.live_dialogue(
                request.profile,
                request.prompts,
                mode=request.mode,
                model=request.model,
                thinking_level=request.thinking_level,
            )

        return await ai.run(name="geminiLive", fn=live)

    return {"renderDialogue": render_dialogue, "speakAsProfile": speak_as_profile}


async def main() -> None:
    """Nothing to run at startup: in dev mode Genkit keeps serving the flows to the Dev UI."""


if __name__ == "__main__":
    ai = Genkit()
    register_flows(ai, VoiceCreator())
    ai.run_main(main())
