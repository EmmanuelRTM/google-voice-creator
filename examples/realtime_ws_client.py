"""Server-to-server realtime client for the `/v1/live` WebSocket relay.

Backends, telephony bridges and mobile backends stream the user's microphone to the
Voice Creator server, which holds the Gemini Live connection and answers in the
cloned voice. This client streams a WAV file at real-time pace and saves the reply.

    pip install websockets
    VOICE_CREATOR_MCP_BEARER=... python examples/realtime_ws_client.py \\
        --url wss://voice.example.com/v1/live --profile me question.wav

The bearer is an access token for the Voice Creator server with the
`dialogue:generate` scope (never the Gemini API key). A server on loopback without
OAuth needs no token.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path

import websockets

from voice_creator.audio.convert import chunk_pcm, to_live_input
from voice_creator.audio.pcm import PcmAudio, read_wav, silence, write_wav

CHUNK_MS = 100


async def send_audio(ws, recording: PcmAudio) -> None:
    audio = to_live_input(recording)  # 16-bit PCM mono at 16 kHz
    tail = silence(1000, sample_rate=audio.sample_rate)  # lets VAD detect the end of speech
    for chunk in (*chunk_pcm(audio, milliseconds=CHUNK_MS), *chunk_pcm(tail)):
        await ws.send(chunk)
        await asyncio.sleep(CHUNK_MS / 1000)  # real-time pace, like a microphone
    await ws.send(json.dumps({"type": "audio_stream_end"}))


async def receive_reply(ws) -> PcmAudio:
    reply = bytearray()
    rate = 24_000
    async for message in ws:
        if isinstance(message, bytes):
            reply += message
            continue
        event = json.loads(message)
        kind = event["type"]
        if kind == "session":
            rate = event["output_sample_rate"]
        elif kind in ("input_transcript", "output_transcript"):
            print(f"[{kind}] {event['text']}")
        elif kind == "interrupted":
            reply.clear()  # barge-in: a real player flushes its buffer here
        elif kind == "error":
            raise RuntimeError(f"{event['error']}: {event['description']}")
        elif kind == "turn_complete" and event.get("status") in (None, "IDLE") and reply:
            await ws.send(json.dumps({"type": "stop"}))
        elif kind == "session_end":
            print(f"[session_end] {event}")
    return PcmAudio(data=bytes(reply), sample_rate=rate)


async def converse(url: str, profile: str, recording: Path, output: Path) -> None:
    bearer = os.environ.get("VOICE_CREATOR_MCP_BEARER")
    headers = {"Authorization": f"Bearer {bearer}"} if bearer else {}
    async with websockets.connect(url, additional_headers=headers, max_size=2**22) as ws:
        await ws.send(json.dumps({"type": "start", "profile": profile, "turn_detection": "vad"}))
        sender = asyncio.create_task(send_audio(ws, read_wav(recording)))
        try:
            reply = await receive_reply(ws)
        finally:
            sender.cancel()
    write_wav(output, reply)
    print(f"Saved {output} ({reply.duration_seconds:.1f} s)")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("recording", type=Path, help="WAV with your question")
    parser.add_argument("--url", default="ws://127.0.0.1:8765/v1/live")
    parser.add_argument("--profile", required=True)
    parser.add_argument("--output", type=Path, default=Path("realtime-reply.wav"))
    args = parser.parse_args()
    asyncio.run(converse(args.url, args.profile, args.recording, args.output))


if __name__ == "__main__":
    main()
