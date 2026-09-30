import json
import logging

import pytest

from voice_creator.errors import RateLimitError
from voice_creator_mcp.app.audit import AuditLog
from voice_creator_mcp.app.rate_limit import RateLimiter
from voice_creator_mcp.auth.principal import Principal


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def test_rate_limiter_allows_up_to_the_limit_per_key() -> None:
    clock = FakeClock()
    limiter = RateLimiter(max_calls=2, window_seconds=60, clock=clock)

    limiter.acquire("alice")
    limiter.acquire("alice")
    limiter.acquire("bob")  # keys are independent

    with pytest.raises(RateLimitError, match="retry in 60s"):
        limiter.acquire("alice")


def test_rate_limiter_window_slides() -> None:
    clock = FakeClock()
    limiter = RateLimiter(max_calls=1, window_seconds=60, clock=clock)
    limiter.acquire("alice")

    clock.now += 61

    limiter.acquire("alice")


def test_rate_limiter_rejects_nonsense_limits() -> None:
    with pytest.raises(ValueError):
        RateLimiter(max_calls=0)


def test_audit_log_records_metadata_but_never_content(caplog: pytest.LogCaptureFixture) -> None:
    principal = Principal(
        issuer="https://auth.example.com",
        subject="user-123",
        client_id="claude",
        scopes=frozenset(),
    )
    audit = AuditLog()

    with caplog.at_level(logging.INFO, logger="voice_creator_mcp.audit"):
        audit.record(
            tool="voice_replicate_profile",
            principal=principal,
            outcome="denied",
            duration_ms=12.345,
            error="AuthorizationError",
        )

    event = json.loads(caplog.records[-1].getMessage())
    assert event == {
        "event": "tool_call",
        "tool": "voice_replicate_profile",
        "workspace": principal.workspace_id,
        "client_id": "claude",
        "outcome": "denied",
        "duration_ms": 12.3,
        "error": "AuthorizationError",
    }
    assert "user-123" not in caplog.text


def test_audit_log_for_local_callers(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.INFO, logger="voice_creator_mcp.audit"):
        AuditLog().record(tool="voice_list_profiles", principal=None, outcome="ok", duration_ms=1)

    event = json.loads(caplog.records[-1].getMessage())
    assert event["workspace"] == "local"
    assert event["client_id"] is None
    assert "error" not in event
