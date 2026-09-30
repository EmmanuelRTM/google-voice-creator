"""`voice-creator`: the voice core from the command line (no MCP needed).

    voice-creator validate me.wav
    voice-creator profile create me --display-name Me --sample me.wav --consent consent.wav
    voice-creator profile replicate me
    voice-creator render scene.txt --cast Me=profile:me --cast Ana=Kore
    voice-creator live me --prompt "Preséntate" --model gemini-3.8-live
    voice-creator token me            # ephemeral Live token for a browser/mobile client

Results are printed as JSON on stdout; errors go to stderr with a non-zero exit code.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import Any

from voice_creator.errors import ConfigError, VoiceCreatorError


def parse_cast(entries: Iterable[str]) -> dict[str, str]:
    """`Speaker=voice` pairs into a cast map (speakers are case-insensitive, no duplicates)."""
    cast: dict[str, str] = {}
    seen: set[str] = set()
    for entry in entries:
        speaker, sep, voice = entry.partition("=")
        speaker, voice = speaker.strip(), voice.strip()
        if not sep or not speaker or not voice:
            raise ValueError(f"Cast entries look like Speaker=voice, got {entry!r}")
        if speaker.casefold() in seen:
            raise ValueError(f"Speaker {speaker!r} is cast twice")
        seen.add(speaker.casefold())
        cast[speaker] = voice
    return cast


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="voice-creator", description=__doc__.split("\n")[0])
    commands = parser.add_subparsers(dest="command", required=True)

    validate = commands.add_parser("validate", help="check a WAV recording")
    validate.add_argument("path")
    validate.add_argument("--purpose", choices=("live", "voices_api", "consent"), default="live")

    profile = commands.add_parser("profile", help="manage voice profiles")
    profile_cmds = profile.add_subparsers(dest="profile_command", required=True)
    create = profile_cmds.add_parser("create")
    create.add_argument("name")
    create.add_argument("--display-name", required=True)
    create.add_argument("--sample", required=True)
    create.add_argument("--consent", required=True)
    create.add_argument("--language")
    create.add_argument("--overwrite", action="store_true")
    profile_cmds.add_parser("list")
    replicate = profile_cmds.add_parser("replicate")
    replicate.add_argument("name")
    replicate.add_argument("--stateless", action="store_true", help="7-day key, nothing stored")
    delete = profile_cmds.add_parser("delete")
    delete.add_argument("name")
    delete.add_argument("--delete-remote", action="store_true")

    render = commands.add_parser("render", help="render a dialogue script with Gemini TTS")
    render.add_argument("script", help="path to a `Speaker (style): text` script")
    render.add_argument("--cast", action="append", required=True, metavar="SPEAKER=VOICE")
    render.add_argument("--output-name")
    render.add_argument("--gap-ms", type=int, default=300)
    render.add_argument("--language")

    live = commands.add_parser("live", help="speak as a profile with Gemini 3.8 Live")
    _live_options(live)
    live.add_argument("--prompt", action="append", required=True)
    live.add_argument("--output-name")

    token = commands.add_parser("token", help="mint an ephemeral Live token locked to a voice")
    _live_options(token)
    token.add_argument("--uses", type=int, default=1)
    return parser


def _live_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("profile")
    parser.add_argument("--mode", choices=("improvise", "verbatim"), default="improvise")
    parser.add_argument("--persona")
    parser.add_argument("--model")
    parser.add_argument("--thinking-level", choices=("low", "medium", "high"))
    parser.add_argument("--language")


def main(argv: Sequence[str] | None = None, *, creator: Any = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if creator is None:
            from voice_creator.client import VoiceCreator

            creator = VoiceCreator()
        result = _dispatch(args, creator)
    except ConfigError as exc:
        print(f"voice-creator: configuration error: {exc}", file=sys.stderr)
        return 2
    except (VoiceCreatorError, ValueError, OSError) as exc:
        print(f"voice-creator: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2, ensure_ascii=False, default=str))
    return 0


def _dispatch(args: argparse.Namespace, creator: Any) -> Any:
    if args.command == "validate":
        return creator.validate_recording(args.path, args.purpose).to_dict()
    if args.command == "profile":
        return _profile(args, creator)
    if args.command == "render":
        script = Path(args.script).read_text(encoding="utf-8")
        return creator.dialogue.render_dialogue(
            script,
            parse_cast(args.cast),
            output_name=args.output_name,
            gap_ms=args.gap_ms,
            language=args.language,
        )
    if args.command == "live":
        return asyncio.run(
            creator.dialogue.live_dialogue(
                args.profile,
                args.prompt,
                mode=args.mode,
                persona=args.persona,
                model=args.model,
                thinking_level=args.thinking_level,
                language_code=args.language,
                output_name=args.output_name,
            )
        )
    if args.command == "token":
        return creator.issue_live_token(
            args.profile,
            mode=args.mode,
            persona=args.persona,
            model=args.model,
            thinking_level=args.thinking_level,
            language_code=args.language,
            uses=args.uses,
        ).to_dict()
    raise ValueError(f"Unknown command {args.command!r}")


def _profile(args: argparse.Namespace, creator: Any) -> Any:
    voices = creator.voices
    if args.profile_command == "create":
        return creator.create_profile(
            args.name,
            display_name=args.display_name,
            sample=args.sample,
            consent=args.consent,
            language_code=args.language,
            overwrite=args.overwrite,
        )
    if args.profile_command == "list":
        return {"profiles": voices.list_profiles()}
    if args.profile_command == "replicate":
        return voices.replicate_profile(args.name, store=not args.stateless)
    return voices.delete_profile(args.name, delete_remote=args.delete_remote)


if __name__ == "__main__":
    raise SystemExit(main())
