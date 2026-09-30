import base64

import pytest

from voice_creator_mcp.errors import VoiceApiError
from voice_creator_mcp.voices.catalog import VoiceCatalog, normalize_voice_id

from fakes import FakeVoicesGateway


def test_replicate_stored_voice_sends_both_recordings() -> None:
    gateway = FakeVoicesGateway(
        create_response={"id": "voice_abc", "type": "replicated", "display_name": "Mario"}
    )

    created = VoiceCatalog(gateway).replicate(
        source_wav=b"source", consent_wav=b"consent", display_name="Mario", language_code="es-MX"
    )

    _, payload = gateway.calls[0]
    assert payload["store"] is True
    voice = payload["voice"]
    assert voice["type"] == "replicated"
    assert voice["display_name"] == "Mario"
    assert voice["language_code"] == "es-MX"
    assert voice["replicated"]["source_audio"] == {
        "data": base64.b64encode(b"source").decode(),
        "mime_type": "audio/wav",
    }
    assert voice["replicated"]["consent_audio"]["data"] == base64.b64encode(b"consent").decode()
    assert created.voice_id == "voice_abc"
    assert created.synthesis_voice == "voice_abc"


def test_replicate_stateless_voice_returns_key() -> None:
    gateway = FakeVoicesGateway(
        create_response={
            "key": "voicekey_123",
            "type": "replicated",
            "expire_time": "2026-10-07T00:00:00Z",
        }
    )

    created = VoiceCatalog(gateway).replicate(
        source_wav=b"s", consent_wav=b"c", display_name=None, store=False
    )

    assert gateway.calls[0][1]["store"] is False
    assert "display_name" not in gateway.calls[0][1]["voice"]
    assert created.voice_key == "voicekey_123"
    assert created.expire_time == "2026-10-07T00:00:00Z"
    assert created.synthesis_voice == "voicekey_123"


def test_replicate_fails_when_response_has_no_usable_reference() -> None:
    gateway = FakeVoicesGateway(create_response={"type": "replicated"})
    with pytest.raises(VoiceApiError, match="id"):
        VoiceCatalog(gateway).replicate(source_wav=b"s", consent_wav=b"c", display_name=None)


def test_design_voice_is_always_stored() -> None:
    gateway = FakeVoicesGateway(create_response={"id": "voice_designed", "type": "prompted"})

    created = VoiceCatalog(gateway).design(
        description="A calm, deep narrator with a light Mexican accent", display_name="Narrator"
    )

    _, payload = gateway.calls[0]
    assert payload["store"] is True
    assert payload["voice"]["type"] == "prompted"
    assert payload["voice"]["prompted"] == {
        "input": "A calm, deep narrator with a light Mexican accent"
    }
    assert created.voice_id == "voice_designed"


def test_design_rejects_blank_description() -> None:
    with pytest.raises(ValueError, match="description"):
        VoiceCatalog(FakeVoicesGateway()).design(description="   ", display_name=None)


def test_list_strips_audio_payloads_from_results() -> None:
    gateway = FakeVoicesGateway(
        list_response={
            "voices": [
                {"id": "Puck", "type": "prebuilt", "sample_audio": {"data": "AAAA" * 1000}},
                {"id": "voice_1", "type": "replicated", "display_name": "Mario"},
            ],
            "next_page_token": "next",
        }
    )

    result = VoiceCatalog(gateway).list(types=["replicated"], search="mario", page_size=10)

    assert gateway.calls[0] == (
        "list",
        {"types": ["replicated"], "search": "mario", "page_size": 10, "page_token": None},
    )
    assert result["next_page_token"] == "next"
    assert all("sample_audio" not in voice for voice in result["voices"])
    assert result["voices"][1]["display_name"] == "Mario"


def test_get_and_delete_normalise_resource_names() -> None:
    gateway = FakeVoicesGateway()
    catalog = VoiceCatalog(gateway)

    voice = catalog.get("voices/voice_abc")
    catalog.delete("voice_abc")

    assert gateway.calls == [("get", "voice_abc"), ("delete", "voice_abc")]
    assert "sample_audio" not in voice


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("voices/voice_abc", "voice_abc"), (" voice_abc ", "voice_abc")],
)
def test_normalize_voice_id(raw: str, expected: str) -> None:
    assert normalize_voice_id(raw) == expected


@pytest.mark.parametrize("raw", ["", "voices/", "../x", "voices/a/b"])
def test_normalize_voice_id_rejects_invalid_values(raw: str) -> None:
    with pytest.raises(ValueError):
        normalize_voice_id(raw)
