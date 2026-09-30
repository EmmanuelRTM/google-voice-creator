"""Least-privilege OAuth scopes and the tool-to-scope policy (the single source of truth).

Scopes are grouped by risk so a client can hold read access without being able to
clone a voice (biometric), spend Gemini quota, or delete data. Every tool must have
an entry here; unknown tools are denied.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from types import MappingProxyType

VOICE_READ = "voice:read"
VOICE_WRITE = "voice:write"
VOICE_CLONE = "voice:clone"
VOICE_DELETE = "voice:delete"
DIALOGUE_GENERATE = "dialogue:generate"

ALL_SCOPES: tuple[str, ...] = (
    VOICE_READ,
    VOICE_WRITE,
    VOICE_CLONE,
    VOICE_DELETE,
    DIALOGUE_GENERATE,
)
BASELINE_SCOPES: frozenset[str] = frozenset({VOICE_READ})

TOOL_SCOPES: Mapping[str, frozenset[str]] = MappingProxyType(
    {
        "voice_validate_recording": frozenset({VOICE_READ}),
        "voice_list_profiles": frozenset({VOICE_READ}),
        "voice_list_gemini_voices": frozenset({VOICE_READ}),
        "voice_preview_script": frozenset({VOICE_READ}),
        "voice_create_profile": frozenset({VOICE_WRITE}),
        "voice_design_gemini_voice": frozenset({VOICE_WRITE}),
        "voice_replicate_profile": frozenset({VOICE_CLONE}),
        "voice_delete_profile": frozenset({VOICE_DELETE}),
        "voice_delete_gemini_voice": frozenset({VOICE_DELETE}),
        "voice_render_dialogue": frozenset({DIALOGUE_GENERATE}),
        "voice_live_dialogue": frozenset({DIALOGUE_GENERATE}),
    }
)


def required_scopes(tool: str) -> frozenset[str]:
    if tool not in TOOL_SCOPES:
        raise KeyError(f"Tool {tool!r} has no scope mapping; add it to TOOL_SCOPES")
    return BASELINE_SCOPES | TOOL_SCOPES[tool]


def missing_scopes(granted: Iterable[str], tool: str) -> frozenset[str]:
    return required_scopes(tool) - frozenset(granted)
