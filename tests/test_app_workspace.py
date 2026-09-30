import os
import stat
from pathlib import Path

import pytest

from voice_creator.errors import AuthorizationError
from voice_creator_mcp.app.workspace import WorkspaceProvider
from voice_creator_mcp.auth.principal import Principal

from fakes import FakeBackend


def principal(subject: str) -> Principal:
    return Principal(
        issuer="https://auth.example.com", subject=subject, client_id="c", scopes=frozenset()
    )


def provider(tmp_path: Path, *, isolate: bool) -> WorkspaceProvider:
    return WorkspaceProvider(
        data_dir=tmp_path / "data",
        output_dir=tmp_path / "out",
        backend=FakeBackend(),
        default_live_model="gemini-3.8-live",
        isolate=isolate,
    )


def test_local_mode_uses_one_shared_workspace(tmp_path: Path) -> None:
    workspaces = provider(tmp_path, isolate=False)

    local = workspaces.get(None)

    assert local.id == "local"
    assert local.output_dir == tmp_path / "out"
    assert workspaces.get(principal("anyone")) is local


def test_isolated_mode_gives_each_principal_a_private_workspace(tmp_path: Path) -> None:
    workspaces = provider(tmp_path, isolate=True)
    alice, bob = principal("alice"), principal("bob")

    alice_ws = workspaces.get(alice)
    bob_ws = workspaces.get(bob)

    assert alice_ws.id == alice.workspace_id
    assert alice_ws.output_dir == tmp_path / "data" / "principals" / alice.workspace_id / "output"
    assert alice_ws.output_dir != bob_ws.output_dir
    assert workspaces.get(principal("alice")) is alice_ws  # cached


@pytest.mark.skipif(os.name != "posix", reason="POSIX permissions only")
def test_isolated_workspaces_are_owner_only(tmp_path: Path) -> None:
    workspace = provider(tmp_path, isolate=True).get(principal("alice"))
    root = workspace.output_dir.parent
    assert stat.S_IMODE(root.stat().st_mode) == 0o700
    assert stat.S_IMODE(root.parent.stat().st_mode) == 0o700  # principals/ is not listable


def test_isolated_mode_requires_a_principal(tmp_path: Path) -> None:
    with pytest.raises(AuthorizationError, match="Authentication required"):
        provider(tmp_path, isolate=True).get(None)


def test_profiles_do_not_leak_between_principals(tmp_path: Path) -> None:
    from conftest import make_tone, wav_bytes

    workspaces = provider(tmp_path, isolate=True)
    alice = workspaces.get(principal("alice"))
    alice.library.register_profile(
        "me",
        display_name="Alice",
        sample_wav=wav_bytes(make_tone(15.0)),
        consent_wav=wav_bytes(make_tone(6.0)),
    )

    assert [p["name"] for p in alice.library.list_profiles()] == ["me"]
    assert workspaces.get(principal("bob")).library.list_profiles() == []
