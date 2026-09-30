import pytest

from voice_creator.errors import LiveConnectionError
from voice_creator.live.config import LiveSessionOptions, ReplicatedVoice
from voice_creator.live.realtime import (
    INPUT_SAMPLE_RATE,
    ClientMessage,
    RealtimeBridge,
)
from voice_creator.live.runner import LiveEvent

from fakes import (
    FakeClientChannel,
    FakeRealtimeConnector,
    FakeRealtimeSession,
    audio_event,
)

OPTIONS = LiveSessionOptions(voice=ReplicatedVoice(sample_wav=b"s", consent_wav=b"c"))


def is_turn_complete(event) -> bool:
    return event.kind == "turn_complete"


async def test_forwards_client_audio_text_and_activity_signals() -> None:
    session = FakeRealtimeSession()
    client = FakeClientChannel(
        [
            ClientMessage("audio", audio=b"\x01\x00" * 160),
            ClientMessage("activity_start"),
            ClientMessage("activity_end"),
            ClientMessage("text", text="hola"),
            ClientMessage("audio_stream_end"),
            ClientMessage("stop"),
        ]
    )

    summary = await RealtimeBridge(FakeRealtimeConnector([session])).run(OPTIONS, client)

    assert session.sent == [
        ("audio", b"\x01\x00" * 160, INPUT_SAMPLE_RATE),
        ("activity_start",),
        ("activity_end",),
        ("text", "hola"),
        ("audio_stream_end",),
    ]
    assert summary.ended == "client"
    assert client.events[0].kind == "session"
    assert client.events[0].data["output_sample_rate"] == 24_000


async def test_relays_model_audio_transcripts_and_interruptions() -> None:
    session = FakeRealtimeSession(
        batches=[
            [
                LiveEvent(input_transcript="¿Qué tal?"),
                audio_event(0.1),
                LiveEvent(output_transcript="Bien"),
                LiveEvent(interrupted=True),
                LiveEvent(turn_complete=True, interaction_status="IDLE"),
            ]
        ]
    )
    client = FakeClientChannel(close_when=is_turn_complete)

    await RealtimeBridge(FakeRealtimeConnector([session])).run(OPTIONS, client)

    assert client.kinds() == [
        "session",
        "input_transcript",
        "audio",
        "output_transcript",
        "interrupted",
        "turn_complete",
    ]
    assert client.events[1].data == {"text": "¿Qué tal?"}
    assert len(client.events[2].audio) == 2 * 2_400
    assert client.events[5].data == {"status": "IDLE"}


async def test_go_away_resumes_transparently_with_the_latest_handle() -> None:
    first = FakeRealtimeSession(
        batches=[[LiveEvent(resumption_handle="h-1"), LiveEvent(go_away=True)]]
    )
    second = FakeRealtimeSession(batches=[[audio_event(0.1), LiveEvent(turn_complete=True)]])
    connector = FakeRealtimeConnector([first, second])
    client = FakeClientChannel(close_when=is_turn_complete)

    summary = await RealtimeBridge(connector).run(OPTIONS, client)

    assert summary.reconnects == 1
    assert "session_resumption" in connector.connections[0][1]
    assert connector.connections[1][1]["session_resumption"] == {"handle": "h-1"}
    sessions = [event for event in client.events if event.kind == "session"]
    assert [event.data["resumed"] for event in sessions] == [False, True]


async def test_server_close_without_handle_ends_with_an_error_event() -> None:
    client = FakeClientChannel()
    summary = await RealtimeBridge(FakeRealtimeConnector([FakeRealtimeSession(batches=[[]])])).run(
        OPTIONS, client
    )

    assert summary.ended == "server_closed"
    assert client.events[-1].kind == "error"


async def test_invalid_audio_is_reported_and_skipped() -> None:
    session = FakeRealtimeSession()
    client = FakeClientChannel([ClientMessage("audio", audio=b"\x01"), ClientMessage("stop")])

    await RealtimeBridge(FakeRealtimeConnector([session])).run(OPTIONS, client)

    assert session.sent == []
    assert any(e.kind == "error" and "16-bit" in e.data["description"] for e in client.events)


async def test_session_time_limit() -> None:
    client = FakeClientChannel()
    bridge = RealtimeBridge(FakeRealtimeConnector([FakeRealtimeSession()]), max_duration_s=0.05)

    summary = await bridge.run(OPTIONS, client)

    assert summary.ended == "time_limit"
    assert client.events[-1].kind == "error"


async def test_initial_connection_failure_raises() -> None:
    connector = FakeRealtimeConnector([FakeRealtimeSession()], reject_signatures=True)
    options = LiveSessionOptions(voice=ReplicatedVoice(sample_wav=b"s", consent_signature="old"))

    with pytest.raises(LiveConnectionError):
        await RealtimeBridge(connector).run(options, FakeClientChannel())


async def test_consent_signature_is_reported() -> None:
    session = FakeRealtimeSession(consent_signature="sig-new")
    summary = await RealtimeBridge(FakeRealtimeConnector([session])).run(
        OPTIONS, FakeClientChannel([ClientMessage("stop")])
    )
    assert summary.consent_signature == "sig-new"
    assert summary.to_dict()["reconnects"] == 0
