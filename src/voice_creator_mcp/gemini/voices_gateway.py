"""`VoicesGateway` backed by `genai.Client().voices` (google-genai >= 2.25)."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any


class GeminiVoicesGateway:
    def __init__(self, client: Any) -> None:
        self._voices = client.voices

    def create(self, voice: dict[str, Any], store: bool) -> dict[str, Any]:
        return _to_dict(self._voices.create(voice=voice, store=store))

    def list(
        self,
        *,
        types: Sequence[str] | None,
        search: str | None,
        page_size: int | None,
        page_token: str | None,
    ) -> dict[str, Any]:
        response = self._voices.list(
            type_=types, search=search, page_size=page_size, page_token=page_token
        )
        return _to_dict(response)

    def get(self, voice_id: str) -> dict[str, Any]:
        return _to_dict(self._voices.get(voice_id))

    def delete(self, voice_id: str) -> None:
        self._voices.delete(voice_id)


def _to_dict(model: Any) -> dict[str, Any]:
    if isinstance(model, dict):
        return model
    return model.model_dump(mode="json", exclude_none=True)
