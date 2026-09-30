from datetime import UTC, datetime
from pathlib import Path

import pytest
from mcp.server.mcpserver.exceptions import ToolError

from voice_creator_mcp.app.dialogue_studio import DialogueStudio
from voice_creator_mcp.app.outputs import OutputWriter
from voice_creator_mcp.app.voice_library import VoiceLibrary
from voice_creator_mcp.config import Settings
from voice_creator_mcp.live.runner import LiveEvent
from voice_creator_mcp.server import build_server, create_server
from voice_creator_mcp.voices.profiles import ProfileStore

from conftest import make_tone, wav_bytes
from fakes import FakeBackend, FakeConnector, FakeSession, FakeVoicesGateway, audio_event

NOW = datetime(2026, 9, 30, 12, 0, tzinfo=UTC)

EXPECTED_TOOLS = {
    "validate_voice_sample",
    "register_voice_profile",
    "replicate_voice_profile",
    "list_voice_profiles",
    "delete_voice_profile",
    "design_voice",
    "list_voices",
    "delete_voice",
    "preview_dialogue_script",
    "render_dialogue",
    "live_dialogue",
}


@pytest.fixture
def backend() -> FakeBackend:
    session = FakeSession(
        passes_per_turn=[
            [[audio_event(0.5), LiveEvent(output_transcript="Hi", turn_complete=True)]]
        ]
    )
    return FakeBackend(
        voices=FakeVoicesGateway(create_response={"id": "voice_mario", "type": "replicated"}),
        connector=FakeConnector(session),
    )


@pytest.fixture
def server(tmp_path: Path, backend: FakeBackend):
    library = VoiceLibrary(ProfileStore(tmp_path / "data"), backend, clock=lambda: NOW)
    studio = DialogueStudio(
        library,
        backend,
        OutputWriter(tmp_path / "out", clock=lambda: NOW),
        default_live_model="gemini-3.8-live",
    )
    return build_server(library, studio)


@pytest.fixture
def recordings(tmp_path: Path) -> dict[str, str]:
    sample, consent = tmp_path / "sample.wav", tmp_path / "consent.wav"
    sample.write_bytes(wav_bytes(make_tone(15.0)))
    consent.write_bytes(wav_bytes(make_tone(6.0)))
    return {"sample_path": str(sample), "consent_path": str(consent)}


async def test_exposes_the_expected_tools(server) -> None:
    tools = {tool.name: tool for tool in await server.list_tools()}

    assert set(tools) == EXPECTED_TOOLS
    assert all(tool.description for tool in tools.values())
    assert tools["delete_voice"].annotations.destructive_hint is True
    assert tools["list_voice_profiles"].annotations.read_only_hint is True


async def test_end_to_end_profile_render_and_live(server, recordings: dict[str, str]) -> None:
    await server.call_tool(
        "register_voice_profile", {"name": "mario", "display_name": "Mario", **recordings}
    )
    await server.call_tool("replicate_voice_profile", {"name": "mario"})

    rendered = await server.call_tool(
        "render_dialogue",
        {"script": "Mario: Hola.\nAna: Hi.", "cast": {"Mario": "profile:mario", "Ana": "Kore"}},
    )
    live = await server.call_tool("live_dialogue", {"profile": "mario", "prompts": ["Say hi"]})

    assert Path(rendered.structured_content["audio_path"]).exists()
    assert live.structured_content["transcript"] == "Hi"


async def test_domain_errors_reach_the_client_as_tool_errors(server, tmp_path: Path) -> None:
    with pytest.raises(ToolError, match="not found"):
        await server.call_tool(
            "validate_voice_sample", {"path": str(tmp_path / "missing.wav"), "purpose": "live"}
        )


async def test_value_errors_are_reported_too(server) -> None:
    with pytest.raises(ToolError, match="description"):
        await server.call_tool("design_voice", {"description": " "})


async def test_create_server_starts_without_an_api_key(tmp_path: Path) -> None:
    settings = Settings.from_env({"VOICE_CREATOR_DATA_DIR": str(tmp_path)}, home=tmp_path)

    server = create_server(settings)
    result = await server.call_tool("list_voice_profiles", {})

    assert result.structured_content == {"profiles": []}
    with pytest.raises(ToolError, match="GEMINI_API_KEY"):
        await server.call_tool("list_voices", {})
