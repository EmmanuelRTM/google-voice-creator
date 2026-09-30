"""Entry point: `voice-creator-mcp` or `python -m voice_creator_mcp` (stdio transport)."""

from voice_creator_mcp.server import create_server


def main() -> None:
    create_server().run()


if __name__ == "__main__":
    main()
