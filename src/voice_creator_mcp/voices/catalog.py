"""Gemini Voices API (`/v1beta/voices`): design, replicate and manage custom voices.

Replicated voices need a source recording plus a spoken consent statement from the
same adult speaker. `store=True` keeps the voice in the project (quota-limited) and
returns an ID; `store=False` returns a short-lived, client-held `voicekey_...` instead.
Designed ("prompted") voices must be stored.
"""

from __future__ import annotations

import base64
import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Protocol

from voice_creator_mcp.errors import VoiceApiError

_VOICE_ID = re.compile(r"^[A-Za-z0-9_\-]+$")
_HEAVY_FIELDS = ("sample_audio", "usage")


class VoicesGateway(Protocol):
    def create(self, voice: dict[str, Any], store: bool) -> dict[str, Any]: ...

    def list(
        self,
        *,
        types: Sequence[str] | None,
        search: str | None,
        page_size: int | None,
        page_token: str | None,
    ) -> dict[str, Any]: ...

    def get(self, voice_id: str) -> dict[str, Any]: ...

    def delete(self, voice_id: str) -> None: ...


@dataclass(frozen=True)
class CreatedVoice:
    voice_type: str
    voice_id: str | None
    voice_key: str | None
    expire_time: str | None
    display_name: str | None

    @property
    def synthesis_voice(self) -> str:
        return self.voice_id or self.voice_key or ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "voice_type": self.voice_type,
            "voice_id": self.voice_id,
            "has_voice_key": self.voice_key is not None,
            "expire_time": self.expire_time,
            "display_name": self.display_name,
        }


def normalize_voice_id(value: str) -> str:
    """Accept `voice_abc` or `voices/voice_abc`; return the bare ID for API paths."""
    voice_id = value.strip().removeprefix("voices/")
    if not _VOICE_ID.fullmatch(voice_id):
        raise ValueError(f"Invalid voice id {value!r}")
    return voice_id


class VoiceCatalog:
    def __init__(self, gateway: VoicesGateway) -> None:
        self._gateway = gateway

    def replicate(
        self,
        *,
        source_wav: bytes,
        consent_wav: bytes,
        display_name: str | None,
        store: bool = True,
        language_code: str | None = None,
    ) -> CreatedVoice:
        voice = _voice_resource(
            "replicated",
            display_name=display_name,
            language_code=language_code,
            replicated={
                "source_audio": _audio(source_wav),
                "consent_audio": _audio(consent_wav),
            },
        )
        return self._create(voice, store=store)

    def design(
        self, *, description: str, display_name: str | None, language_code: str | None = None
    ) -> CreatedVoice:
        if not description.strip():
            raise ValueError("A voice description is required to design a voice")
        voice = _voice_resource(
            "prompted",
            display_name=display_name,
            language_code=language_code,
            prompted={"input": description.strip()},
        )
        return self._create(voice, store=True)

    def list(
        self,
        *,
        types: Sequence[str] | None = None,
        search: str | None = None,
        page_size: int | None = None,
        page_token: str | None = None,
    ) -> dict[str, Any]:
        response = self._gateway.list(
            types=list(types) if types else None,
            search=search,
            page_size=page_size,
            page_token=page_token,
        )
        return {
            "voices": [_summary(voice) for voice in response.get("voices") or []],
            "next_page_token": response.get("next_page_token") or None,
        }

    def get(self, voice_id: str) -> dict[str, Any]:
        return _summary(self._gateway.get(normalize_voice_id(voice_id)))

    def delete(self, voice_id: str) -> None:
        self._gateway.delete(normalize_voice_id(voice_id))

    def _create(self, voice: dict[str, Any], *, store: bool) -> CreatedVoice:
        response = self._gateway.create(voice, store)
        created = CreatedVoice(
            voice_type=response.get("type", voice["type"]),
            voice_id=response.get("id"),
            voice_key=response.get("key"),
            expire_time=_as_text(response.get("expire_time")),
            display_name=response.get("display_name"),
        )
        if store and not created.voice_id:
            raise VoiceApiError("Voices API response did not include a voice id")
        if not store and not created.voice_key:
            raise VoiceApiError("Voices API response did not include a voice key")
        return created


def _voice_resource(voice_type: str, **fields: Any) -> dict[str, Any]:
    return {"type": voice_type, **{key: value for key, value in fields.items() if value}}


def _audio(wav: bytes) -> dict[str, str]:
    return {"data": base64.b64encode(wav).decode("ascii"), "mime_type": "audio/wav"}


def _summary(voice: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in voice.items() if key not in _HEAVY_FIELDS}


def _as_text(value: Any) -> str | None:
    return None if value is None else str(value)
