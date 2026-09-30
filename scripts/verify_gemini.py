#!/usr/bin/env python3
"""Verify every official Gemini integration path with your own key and recordings.

    uv run python scripts/verify_gemini.py --sample me.wav --consent consent.wav \\
        --replicate --report verification.md

Checks the voice core (Voices API, Live standard and Extended Thinking with the cloned
voice, consent-signature reuse, server-to-server realtime audio, ephemeral tokens, TTS)
and the MCP server (tool schemas with the installed SDK, a Live Extended Thinking
session calling an MCP tool, and optionally the Interactions API `mcp_server` tool).

For the Interactions check, the server must be reachable from Google over HTTPS, and
the bearer token (read from VOICE_CREATOR_MCP_BEARER, never a flag) is sent to Google:
use a short-lived token with only the `voice:read` scope.

    VOICE_CREATOR_MCP_BEARER=... uv run python scripts/verify_gemini.py \\
        --profile me --mcp-url https://voice.example.com/mcp

The Gemini key comes from GEMINI_API_KEY or GEMINI_API_KEY_FILE. Reports never
contain keys or tokens. Generated audio is written to VOICE_CREATOR_OUTPUT_DIR.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from voice_creator import VoiceCreator
from voice_creator.cli import write_report
from voice_creator.diagnostics import VerifyPlan, verify_gemini
from voice_creator.live.config import GEMINI_38_LIVE_EXTENDED_THINKING, LiveSessionOptions
from voice_creator.live.instructions import build_system_instruction
from voice_creator_mcp.integrations.verification import (
    DEFAULT_INTERACTIONS_MODEL,
    InteractionsTarget,
    McpChecks,
)
from voice_creator_mcp.server.composition import create_server

BEARER_ENV = "VOICE_CREATOR_MCP_BEARER"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--profile", default="verify")
    parser.add_argument("--display-name", default="Voice verification")
    parser.add_argument("--sample", help="(re)create the profile from this 10-20 s WAV")
    parser.add_argument("--consent", help="the consent recording for --sample")
    parser.add_argument("--language")
    parser.add_argument("--replicate", action="store_true", help="test TTS with a 7-day key")
    parser.add_argument("--skip", action="append", default=[], metavar="CHECK[,CHECK]")
    parser.add_argument("--mcp-url", help="public HTTPS MCP endpoint for the Interactions check")
    parser.add_argument("--interactions-model", default=DEFAULT_INTERACTIONS_MODEL)
    parser.add_argument("--report", help="write the report (.md or .json)")
    return parser


def main(argv: Sequence[str] | None = None, *, creator: VoiceCreator | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.mcp_url and not args.mcp_url.startswith("https://"):
        parser.error("--mcp-url must be an https:// URL that Google can reach")

    creator = creator or VoiceCreator()
    plan = VerifyPlan(
        profile=args.profile,
        display_name=args.display_name,
        sample=_read(args.sample),
        consent=_read(args.consent),
        language_code=args.language,
        replicate=args.replicate,
        skip=frozenset(name.strip() for entry in args.skip for name in entry.split(",") if name),
    )
    bearer = os.environ.get(BEARER_ENV) or None
    target = (
        InteractionsTarget(args.mcp_url, bearer=bearer, model=args.interactions_model)
        if args.mcp_url
        else None
    )
    mcp_checks = McpChecks(
        create_server(creator.settings, backend=creator.backend),
        genai_client=lambda: _genai_client(creator),
        live_options=lambda: _live_options(creator, plan),
        interactions=target,
    ).checks(requires=("credentials", "profile"))

    report = asyncio.run(
        verify_gemini(creator, plan, extra_checks=mcp_checks, secrets=[bearer or ""])
    )
    print(report.to_markdown())
    if args.report:
        print(f"Report written to {write_report(report, Path(args.report))}", file=sys.stderr)
    return 0 if report.ok else 1


def _live_options(creator: VoiceCreator, plan: VerifyPlan) -> LiveSessionOptions:
    return LiveSessionOptions(
        model=GEMINI_38_LIVE_EXTENDED_THINKING,
        voice=creator.voices.live_voice(plan.profile),
        system_instruction=build_system_instruction("improvise", persona=plan.display_name),
        thinking_level="low",
        language_code=plan.language_code,
    )


def _genai_client(creator: VoiceCreator) -> Any:
    from google import genai

    return genai.Client(api_key=creator.settings.require_api_key())


def _read(path: str | None) -> bytes | None:
    return Path(path).expanduser().read_bytes() if path else None


if __name__ == "__main__":
    raise SystemExit(main())
