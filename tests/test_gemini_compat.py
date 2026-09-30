"""Our MCP tools must survive Google's own MCP-to-Gemini conversion (google-genai SDK),
which is what `tools=[mcp_session]` uses in generate_content and the Live API."""

from pathlib import Path

import pytest
from google.genai import types
from google.genai._mcp_utils import mcp_to_gemini_tool

from voice_creator.config import Settings
from voice_creator_mcp.server import create_server


def free_form_objects(schema: types.Schema, path: str) -> list[str]:
    found = []
    if schema.type == types.Type.OBJECT and not schema.properties and path != "":
        found.append(path)
    for name, child in (schema.properties or {}).items():
        found += free_form_objects(child, f"{path}.{name}")
    if schema.items is not None:
        found += free_form_objects(schema.items, f"{path}[]")
    for index, option in enumerate(schema.any_of or []):
        found += free_form_objects(option, f"{path}|{index}")
    return found


@pytest.fixture
async def tools(tmp_path: Path):
    settings = Settings.from_env({"VOICE_CREATOR_DATA_DIR": str(tmp_path)}, home=tmp_path)
    return await create_server(settings).list_tools()


async def test_every_tool_converts_without_losing_parameters(tools) -> None:
    for tool in tools:
        declaration = mcp_to_gemini_tool(tool).function_declarations[0]
        gemini_params = set(
            (declaration.parameters.properties or {}) if declaration.parameters else ()
        )
        assert gemini_params == set(tool.input_schema.get("properties", {})), tool.name
        assert declaration.description


async def test_no_tool_exposes_a_free_form_object_to_gemini(tools) -> None:
    problems = {
        tool.name: found
        for tool in tools
        if (params := mcp_to_gemini_tool(tool).function_declarations[0].parameters)
        and (found := free_form_objects(params, ""))
    }
    assert problems == {}


async def test_cast_is_a_list_of_speaker_voice_pairs(tools) -> None:
    render = next(tool for tool in tools if tool.name == "voice_render_dialogue")
    cast = mcp_to_gemini_tool(render).function_declarations[0].parameters.properties["cast"]

    assert cast.type == types.Type.ARRAY
    assert set(cast.items.properties) == {"speaker", "voice"}
