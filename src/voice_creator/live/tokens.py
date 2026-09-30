"""Ephemeral Live API tokens locked to a voice: Google's client-to-server pattern.

A browser or mobile app should never hold the Gemini API key. Instead the backend
mints a short-lived, limited-use token (`auth_tokens.create`, API version v1alpha)
whose `live_connect_constraints` fix the model and the whole session config, which
includes the cloned voice and the system instructions. The client then connects to
the Live API directly with that token, at the lowest latency, and cannot change the
voice or the instructions.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol

from voice_creator.live.config import LiveSessionOptions, build_live_config

LIVE_TOKEN_API_VERSION = "v1alpha"
DEFAULT_TOKEN_TTL = timedelta(minutes=30)  # Google's default expire_time
DEFAULT_START_WINDOW = timedelta(minutes=1)  # Google's default new_session_expire_time
MAX_TOKEN_TTL = timedelta(hours=1)
MAX_START_WINDOW = timedelta(minutes=10)
MAX_USES = 5


class TokenGateway(Protocol):
    def create(self, config: dict[str, Any]) -> str:
        """Create an auth token and return its name (the value clients use as API key)."""
        ...


@dataclass(frozen=True)
class LiveToken:
    token: str
    model: str
    api_version: str
    uses: int
    expire_time: str
    new_session_expire_time: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class LiveTokenIssuer:
    def __init__(
        self, gateway: TokenGateway, *, clock: Callable[[], datetime] | None = None
    ) -> None:
        self._gateway = gateway
        self._clock = clock or (lambda: datetime.now(UTC))

    def issue(
        self,
        options: LiveSessionOptions,
        *,
        uses: int = 1,
        ttl: timedelta = DEFAULT_TOKEN_TTL,
        start_window: timedelta = DEFAULT_START_WINDOW,
    ) -> LiveToken:
        if not 1 <= uses <= MAX_USES:
            raise ValueError(f"uses must be between 1 and {MAX_USES}")
        if not timedelta(0) < ttl <= MAX_TOKEN_TTL:
            raise ValueError(f"ttl must be positive and at most {MAX_TOKEN_TTL}")
        if not timedelta(0) < start_window <= min(ttl, MAX_START_WINDOW):
            raise ValueError(f"start_window must be positive and at most {MAX_START_WINDOW}")

        config = build_live_config(options)  # enforces the model's rules before calling Google
        now = self._clock()
        expire_time, start_by = now + ttl, now + start_window
        name = self._gateway.create(
            {
                "uses": uses,
                "expire_time": expire_time,
                "new_session_expire_time": start_by,
                # No `lock_additional_fields`: the entire constrained config is locked.
                "live_connect_constraints": {"model": options.model, "config": config},
            }
        )
        return LiveToken(
            token=name,
            model=options.model,
            api_version=LIVE_TOKEN_API_VERSION,
            uses=uses,
            expire_time=expire_time.isoformat(),
            new_session_expire_time=start_by.isoformat(),
        )
