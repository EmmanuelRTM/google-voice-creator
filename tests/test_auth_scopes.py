import pytest

from voice_creator_mcp.auth.scopes import (
    ALL_SCOPES,
    BASELINE_SCOPES,
    DIALOGUE_GENERATE,
    TOOL_SCOPES,
    VOICE_CLONE,
    VOICE_READ,
    missing_scopes,
    required_scopes,
)


def test_every_tool_scope_is_advertised() -> None:
    used = {scope for scopes in TOOL_SCOPES.values() for scope in scopes}
    assert used <= set(ALL_SCOPES)
    assert set(BASELINE_SCOPES) <= set(ALL_SCOPES)


def test_required_scopes_always_include_the_baseline() -> None:
    assert required_scopes("voice_replicate_profile") == {VOICE_READ, VOICE_CLONE}
    assert required_scopes("voice_list_profiles") == {VOICE_READ}


def test_voice_cloning_needs_its_own_scope() -> None:
    assert VOICE_CLONE in required_scopes("voice_replicate_profile")
    assert VOICE_CLONE not in required_scopes("voice_render_dialogue")
    assert DIALOGUE_GENERATE in required_scopes("voice_live_dialogue")


def test_missing_scopes() -> None:
    assert missing_scopes({VOICE_READ}, "voice_replicate_profile") == {VOICE_CLONE}
    assert missing_scopes({VOICE_READ, VOICE_CLONE}, "voice_replicate_profile") == set()


def test_unknown_tools_are_denied_by_default() -> None:
    with pytest.raises(KeyError, match="no scope mapping"):
        required_scopes("voice_something_new")
