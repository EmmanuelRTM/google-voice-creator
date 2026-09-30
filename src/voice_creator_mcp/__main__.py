"""Entry point: `voice-creator-mcp [--transport stdio|http]` or `python -m voice_creator_mcp`."""

from __future__ import annotations

import argparse
import logging
import os
import sys
from collections.abc import Sequence

from voice_creator.config import Settings
from voice_creator.errors import ConfigError
from voice_creator_mcp.auth.config import HttpConfig

logger = logging.getLogger("voice_creator_mcp")


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="voice-creator-mcp",
        description="MCP server for creating dialogue in your own voice with Gemini.",
    )
    parser.add_argument(
        "--transport",
        choices=("stdio", "http"),
        default=os.environ.get("VOICE_CREATOR_TRANSPORT", "stdio"),
        help="stdio for local clients (default); http for Streamable HTTP with OAuth",
    )
    parser.add_argument("--host", help="HTTP bind address (default 127.0.0.1)")
    parser.add_argument("--port", type=int, help="HTTP port (default 8765)")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> None:
    args = parse_args(argv)
    # stdout carries the stdio protocol, so all logging goes to stderr.
    logging.basicConfig(
        level=logging.INFO,
        stream=sys.stderr,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    try:
        settings = Settings.from_env()
        for warning in settings.credential_warnings():
            logger.warning(warning)
        if args.transport == "stdio":
            from voice_creator_mcp.server import create_server

            create_server(settings).run()
            return
        env = dict(os.environ)
        if args.host:
            env["VOICE_CREATOR_HTTP_HOST"] = args.host
        if args.port:
            env["VOICE_CREATOR_HTTP_PORT"] = str(args.port)
        from voice_creator_mcp.server.http import run_http

        run_http(settings, HttpConfig.from_env(env))
    except ConfigError as exc:
        print(f"voice-creator-mcp: configuration error: {exc}", file=sys.stderr)
        raise SystemExit(2) from exc


if __name__ == "__main__":
    main()
