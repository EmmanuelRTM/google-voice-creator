"""`TokenGateway` backed by `genai.Client(http_options={"api_version": "v1alpha"}).auth_tokens`."""

from __future__ import annotations

from typing import Any


class GeminiTokenGateway:
    def __init__(self, client: Any) -> None:
        self._auth_tokens = client.auth_tokens

    def create(self, config: dict[str, Any]) -> str:
        return str(self._auth_tokens.create(config=config).name)
