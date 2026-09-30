"""Let Gemini call your deployed Voice Creator MCP server through the Interactions API.

The Interactions API's `mcp_server` tool makes Gemini connect to the server from
Google's infrastructure, so the server must be deployed over public HTTPS (see
README: remote deployment). The `headers` travel to Google with the request: send a
short-lived access token for *your* MCP server with only the scopes the task needs,
never the Gemini API key.

    GEMINI_API_KEY=... VOICE_CREATOR_MCP_BEARER=... \\
        python examples/interactions_mcp_server.py --url https://voice.example.com/mcp \\
        "Preview this script and tell me who speaks: Mario (warm): ¡Hola! Ana: ¿Qué tal?"
"""

from __future__ import annotations

import argparse
import os

from google import genai

from voice_creator.config import Settings

READ_ONLY_TOOLS = ["voice_list_profiles", "voice_list_gemini_voices", "voice_preview_script"]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("prompt")
    parser.add_argument("--url", required=True, help="public HTTPS MCP endpoint")
    parser.add_argument("--model", default="gemini-3.8-flash")
    parser.add_argument("--allow", action="append", help="tool to allow (repeatable)")
    args = parser.parse_args()
    if not args.url.startswith("https://"):
        parser.error("--url must be https:// (Gemini calls it from Google's servers)")

    # GEMINI_API_KEY_FILE or GEMINI_API_KEY, resolved the same way as everywhere else
    client = genai.Client(api_key=Settings.from_env().require_api_key())
    interaction = client.interactions.create(
        model=args.model,
        input=args.prompt,
        tools=[
            {
                "type": "mcp_server",
                "name": "voice_creator",
                "url": args.url,
                "headers": {"Authorization": f"Bearer {os.environ['VOICE_CREATOR_MCP_BEARER']}"},
                "allowed_tools": [{"tools": args.allow or READ_ONLY_TOOLS}],
            }
        ],
    )

    for step in interaction.steps or []:
        if step.type == "mcp_server_tool_call":
            print(f"→ {step.server_name}.{step.name}({step.arguments})")
        elif step.type == "mcp_server_tool_result":
            print(f"← {step.name}")
    print(interaction.output_text)


if __name__ == "__main__":
    main()
