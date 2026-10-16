"""Keep the integration examples consistent with the code they demonstrate.

The examples need optional packages (google-adk, genkit, websockets), so they are
parsed rather than imported here; each was also run against the real packages.
"""

import ast
import json
import py_compile
import re
import tomllib
from pathlib import Path

import pytest

from voice_creator import __version__
from voice_creator_mcp.auth.scopes import LIVE_TOKENS_PATH, TOOL_SCOPES

ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = ROOT / "examples"
# Skip macOS AppleDouble sidecars ("._foo.py") that appear on exFAT and network volumes.
PYTHON_EXAMPLES = sorted(p for p in EXAMPLES.rglob("*.py") if not p.name.startswith("._"))
TOOL_LISTS = {"AGENT_TOOLS", "TOOLS", "READ_ONLY_TOOLS"}


def test_there_are_examples_for_each_integration() -> None:
    names = {
        path.relative_to(EXAMPLES).as_posix() for path in EXAMPLES.rglob("*") if path.is_file()
    }
    for expected in (
        "adk_voice_agent/agent.py",
        "genkit_flow/flows.py",
        "gemini_cli_extension/gemini-extension.json",
        "interactions_mcp_server.py",
        "live_mcp_bridge.py",
        "realtime_ws_client.py",
        "browser_ephemeral_token/main.js",
    ):
        assert expected in names


@pytest.mark.parametrize("path", PYTHON_EXAMPLES, ids=lambda p: p.name)
def test_python_examples_compile(path: Path, tmp_path: Path) -> None:
    py_compile.compile(str(path), cfile=str(tmp_path / "out.pyc"), doraise=True)


@pytest.mark.parametrize("path", PYTHON_EXAMPLES, ids=lambda p: p.name)
def test_examples_only_reference_real_tools(path: Path) -> None:
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id in TOOL_LISTS for target in node.targets
        ):
            assert set(ast.literal_eval(node.value)) <= set(TOOL_SCOPES)


@pytest.mark.parametrize("path", PYTHON_EXAMPLES, ids=lambda p: p.name)
def test_examples_never_take_secrets_as_flags(path: Path) -> None:
    source = path.read_text()
    assert not re.search(r"add_argument\(\s*\"--(api-key|key|token|bearer)", source)


def test_gemini_cli_extension_matches_the_package() -> None:
    folder = EXAMPLES / "gemini_cli_extension"
    manifest = json.loads((folder / "gemini-extension.json").read_text())
    scripts = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["scripts"]

    assert manifest["version"] == __version__
    assert (folder / manifest["contextFileName"]).exists()
    (server,) = manifest["mcpServers"].values()
    assert server["command"] in scripts
    assert server["env"]["GEMINI_API_KEY"] == "$GEMINI_API_KEY"  # expanded, never inlined
    assert set(server["excludeTools"]) <= set(TOOL_SCOPES)


def test_browser_example_uses_the_token_endpoint() -> None:
    folder = EXAMPLES / "browser_ephemeral_token"
    assert f'"{LIVE_TOKENS_PATH}"' in (folder / "main.js").read_text()
    assert f'"{LIVE_TOKENS_PATH}"' in (folder / "vite.config.js").read_text()
    package = json.loads((folder / "package.json").read_text())
    assert "@google/genai" in package["dependencies"]
