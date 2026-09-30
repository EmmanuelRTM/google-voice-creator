import base64
import json
import logging
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

import pytest
from mcp.server.auth.middleware.auth_context import auth_context_var
from mcp.server.auth.middleware.bearer_auth import AuthenticatedUser
from mcp.server.auth.provider import AccessToken
from mcp.server.mcpserver.exceptions import ResourceError, ToolError

from voice_creator.config import Settings
from voice_creator.live.runner import LiveEvent
from voice_creator_mcp.app.inputs import PathPolicy
from voice_creator_mcp.app.rate_limit import RateLimiter
from voice_creator_mcp.app.workspace import WorkspaceProvider
from voice_creator_mcp.auth.scopes import TOOL_SCOPES
from voice_creator_mcp.server import ServerOptions, build_server, create_server

from conftest import make_tone, wav_bytes
from fakes import FakeBackend, FakeConnector, FakeSession, FakeVoicesGateway, audio_event

NOW = datetime(2026, 9, 30, 12, 0, tzinfo=UTC)
SAMPLE_B64 = base64.b64encode(wav_bytes(make_tone(15.0))).decode()
CONSENT_B64 = base64.b64encode(wav_bytes(make_tone(6.0))).decode()
ALL_SCOPES = "voice:read voice:write voice:clone voice:delete dialogue:generate"


def fake_backend() -> FakeBackend:
    session = FakeSession(
        passes_per_turn=[
            [[audio_event(0.5), LiveEvent(output_transcript="Hi", turn_complete=True)]]
        ]
    )
    return FakeBackend(
        voices=FakeVoicesGateway(create_response={"id": "voice_mario", "type": "replicated"}),
        connector=FakeConnector(session),
    )


def make_server(tmp_path: Path, *, isolate: bool = False, **options):
    workspaces = WorkspaceProvider(
        data_dir=tmp_path / "data",
        output_dir=tmp_path / "out",
        backend=fake_backend(),
        default_live_model="gemini-3.8-live",
        isolate=isolate,
        clock=lambda: NOW,
    )
    return build_server(workspaces, ServerOptions(**options))


@contextmanager
def signed_in(subject: str, scopes: str = ALL_SCOPES):
    token = AccessToken(
        token="t",
        client_id="claude",
        scopes=scopes.split(),
        subject=subject,
        claims={"iss": "https://auth.example.com"},
    )
    reset = auth_context_var.set(AuthenticatedUser(token))
    try:
        yield
    finally:
        auth_context_var.reset(reset)


async def create_profile(server, name: str = "mario"):
    return await server.call_tool(
        "voice_create_profile",
        {
            "name": name,
            "display_name": "Mario",
            "sample_base64": SAMPLE_B64,
            "consent_base64": CONSENT_B64,
        },
    )


# --- Discovery -----------------------------------------------------------------


async def test_tools_are_prefixed_described_annotated_and_typed(tmp_path: Path) -> None:
    tools = {tool.name: tool for tool in await make_server(tmp_path).list_tools()}

    assert set(tools) == set(TOOL_SCOPES)
    for tool in tools.values():
        assert tool.name.startswith("voice_")
        assert tool.description
        assert tool.annotations is not None and tool.annotations.title
        assert tool.output_schema is not None
    assert tools["voice_delete_gemini_voice"].annotations.destructive_hint is True
    assert tools["voice_list_profiles"].annotations.read_only_hint is True
    assert tools["voice_list_profiles"].annotations.open_world_hint is False


async def test_render_resources_are_advertised(tmp_path: Path) -> None:
    templates = {t.uri_template for t in await make_server(tmp_path).list_resource_templates()}
    assert templates == {
        "voice://renders/{render_id}/audio",
        "voice://renders/{render_id}/transcript",
    }


# --- Local (stdio) behaviour ---------------------------------------------------


async def test_local_end_to_end_with_paths_and_resources(tmp_path: Path) -> None:
    server = make_server(tmp_path)
    sample = tmp_path / "sample.wav"
    sample.write_bytes(wav_bytes(make_tone(15.0)))

    validated = await server.call_tool("voice_validate_recording", {"wav_path": str(sample)})
    await create_profile(server)
    await server.call_tool("voice_replicate_profile", {"name": "mario"})
    rendered = await server.call_tool(
        "voice_render_dialogue",
        {"script": "Mario: Hola.\nAna: Hi.", "cast": {"Mario": "profile:mario", "Ana": "Kore"}},
    )
    live = await server.call_tool("voice_live_dialogue", {"profile": "mario", "prompts": ["Hi"]})

    assert validated.structured_content["ok"] is True
    render = rendered.structured_content
    assert render["audio_uri"] == f"voice://renders/{render['render_id']}/audio"
    assert Path(render["audio_path"]).exists()
    assert live.structured_content["transcript"] == "Hi"

    audio = list(await server.read_resource(render["audio_uri"]))
    transcript = list(await server.read_resource(render["transcript_uri"]))
    assert audio[0].content.startswith(b"RIFF")
    assert audio[0].mime_type == "audio/wav"
    assert transcript[0].content == "Mario: Hola.\nAna: Hi.\n"


async def test_missing_render_resource(tmp_path: Path) -> None:
    with pytest.raises(ResourceError, match="not found"):
        await make_server(tmp_path).read_resource("voice://renders/nope/audio")


async def test_domain_and_value_errors_become_tool_errors(tmp_path: Path) -> None:
    server = make_server(tmp_path)
    with pytest.raises(ToolError, match="not found"):
        await server.call_tool("voice_validate_recording", {"wav_path": str(tmp_path / "x.wav")})
    with pytest.raises(ToolError, match="exactly one"):
        await server.call_tool("voice_validate_recording", {})


# --- Remote (HTTP) behaviour ---------------------------------------------------


async def test_remote_mode_refuses_server_paths_and_redacts_them(tmp_path: Path) -> None:
    server = make_server(tmp_path, redact_paths=True, path_policy=PathPolicy(()))

    with pytest.raises(ToolError, match="base64"):
        await server.call_tool("voice_validate_recording", {"wav_path": "/etc/passwd"})
    created = await create_profile(server)
    rendered = await server.call_tool(
        "voice_render_dialogue", {"script": "Ana: Hi.", "cast": {"Ana": "Kore"}}
    )

    assert created.structured_content["profile"]["sample_path"] is None
    assert rendered.structured_content["audio_path"] is None
    assert rendered.structured_content["audio_uri"].startswith("voice://renders/")


async def test_scopes_are_enforced_per_tool(tmp_path: Path) -> None:
    server = make_server(tmp_path, isolate=True, auth_enabled=True)

    with pytest.raises(ToolError, match="Authentication required"):
        await server.call_tool("voice_list_profiles", {})
    with signed_in("alice", "voice:read voice:write"):
        await create_profile(server)
        with pytest.raises(ToolError, match=r"insufficient_scope.*voice:clone"):
            await server.call_tool("voice_replicate_profile", {"name": "mario"})
    with signed_in("alice", "voice:read voice:clone"):
        await server.call_tool("voice_replicate_profile", {"name": "mario"})


async def test_principals_cannot_see_each_others_voices_or_renders(tmp_path: Path) -> None:
    server = make_server(tmp_path, isolate=True, auth_enabled=True)
    with signed_in("alice"):
        await create_profile(server)
        rendered = await server.call_tool(
            "voice_render_dialogue", {"script": "Ana: Hi.", "cast": {"Ana": "Kore"}}
        )

    with signed_in("bob"):
        profiles = await server.call_tool("voice_list_profiles", {})
        assert profiles.structured_content == {"profiles": []}
        with pytest.raises(ToolError, match="not found"):
            await server.call_tool("voice_live_dialogue", {"profile": "mario", "prompts": ["Hi"]})
        with pytest.raises(ResourceError, match="not found"):
            await server.read_resource(rendered.structured_content["audio_uri"])


async def test_resources_require_authentication_when_auth_is_on(tmp_path: Path) -> None:
    server = make_server(tmp_path, isolate=True, auth_enabled=True)
    with pytest.raises(ResourceError, match="Authentication required"):
        await server.read_resource("voice://renders/x/audio")


async def test_costly_tools_are_rate_limited(tmp_path: Path) -> None:
    server = make_server(tmp_path, rate_limiter=RateLimiter(max_calls=1))
    args = {"script": "Ana: Hi.", "cast": {"Ana": "Kore"}}

    await server.call_tool("voice_render_dialogue", args)
    with pytest.raises(ToolError, match="Rate limit"):
        await server.call_tool("voice_render_dialogue", args)
    await server.call_tool("voice_list_profiles", {})  # free tools are not limited


async def test_every_call_is_audited(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    server = make_server(tmp_path, isolate=True, auth_enabled=True)

    with caplog.at_level(logging.INFO, logger="voice_creator_mcp.audit"):
        with signed_in("alice", "voice:read"), pytest.raises(ToolError):
            await server.call_tool("voice_replicate_profile", {"name": "mario"})
        with signed_in("alice", "voice:read"):
            await server.call_tool("voice_list_profiles", {})

    events = [json.loads(record.getMessage()) for record in caplog.records]
    assert [(e["tool"], e["outcome"]) for e in events] == [
        ("voice_replicate_profile", "denied"),
        ("voice_list_profiles", "ok"),
    ]


async def test_oversized_inputs_are_rejected_by_the_schema(tmp_path: Path) -> None:
    with pytest.raises(ToolError):
        await make_server(tmp_path).call_tool(
            "voice_live_dialogue", {"profile": "mario", "prompts": ["x"] * 21}
        )


# --- Composition root ----------------------------------------------------------


async def test_create_server_starts_without_an_api_key(tmp_path: Path) -> None:
    settings = Settings.from_env({"VOICE_CREATOR_DATA_DIR": str(tmp_path)}, home=tmp_path)

    server = create_server(settings)
    result = await server.call_tool("voice_list_profiles", {})

    assert result.structured_content == {"profiles": []}
    with pytest.raises(ToolError, match="GEMINI_API_KEY"):
        await server.call_tool("voice_list_gemini_voices", {})
