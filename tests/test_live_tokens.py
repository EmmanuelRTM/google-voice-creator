from datetime import UTC, datetime, timedelta
from types import SimpleNamespace as NS

import pytest

from voice_creator.errors import LiveConfigError
from voice_creator.gemini.token_gateway import GeminiTokenGateway
from voice_creator.live.config import (
    GEMINI_38_LIVE,
    GEMINI_38_LIVE_EXTENDED_THINKING,
    LiveSessionOptions,
    ReplicatedVoice,
)
from voice_creator.live.tokens import LIVE_TOKEN_API_VERSION, LiveTokenIssuer

from fakes import FakeTokenGateway

NOW = datetime(2026, 9, 30, 12, 0, tzinfo=UTC)
VOICE = ReplicatedVoice(sample_wav=b"sample", consent_signature="sig")


@pytest.fixture
def gateway() -> FakeTokenGateway:
    return FakeTokenGateway()


@pytest.fixture
def issuer(gateway: FakeTokenGateway) -> LiveTokenIssuer:
    return LiveTokenIssuer(gateway, clock=lambda: NOW)


def test_token_locks_model_voice_and_instructions(issuer, gateway) -> None:
    token = issuer.issue(
        LiveSessionOptions(model=GEMINI_38_LIVE, voice=VOICE, system_instruction="Be Mario.")
    )

    (request,) = gateway.requests
    assert request["uses"] == 1
    assert request["expire_time"] == NOW + timedelta(minutes=30)
    assert request["new_session_expire_time"] == NOW + timedelta(minutes=1)
    constraints = request["live_connect_constraints"]
    assert constraints["model"] == GEMINI_38_LIVE
    assert constraints["config"]["system_instruction"] == "Be Mario."
    replicated = constraints["config"]["speech_config"]["voice_config"]["replicated_voice_config"]
    assert replicated["voice_consent_signature"] == {"signature": "sig"}
    # Omitting lock_additional_fields locks the whole constrained config.
    assert "lock_additional_fields" not in request

    assert token.token == "auth_tokens/token-1"
    assert token.api_version == LIVE_TOKEN_API_VERSION == "v1alpha"
    assert token.model == GEMINI_38_LIVE
    assert token.expire_time == (NOW + timedelta(minutes=30)).isoformat()


def test_custom_uses_and_lifetimes(issuer, gateway) -> None:
    issuer.issue(
        LiveSessionOptions(voice=VOICE),
        uses=3,
        ttl=timedelta(minutes=10),
        start_window=timedelta(minutes=2),
    )
    request = gateway.requests[0]
    assert request["uses"] == 3
    assert request["expire_time"] == NOW + timedelta(minutes=10)
    assert request["new_session_expire_time"] == NOW + timedelta(minutes=2)


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"uses": 0}, "uses"),
        ({"uses": 6}, "uses"),
        ({"ttl": timedelta(hours=2)}, "ttl"),
        ({"start_window": timedelta(minutes=45)}, "start_window"),
    ],
)
def test_token_policy_bounds(issuer, kwargs, message) -> None:
    with pytest.raises(ValueError, match=message):
        issuer.issue(LiveSessionOptions(voice=VOICE), **kwargs)


def test_model_rules_are_enforced_before_calling_google(issuer, gateway) -> None:
    with pytest.raises(LiveConfigError):
        issuer.issue(LiveSessionOptions(model=GEMINI_38_LIVE, voice=VOICE, thinking_level="high"))
    assert gateway.requests == []


def test_extended_thinking_tokens_carry_the_thinking_level(issuer, gateway) -> None:
    issuer.issue(
        LiveSessionOptions(
            model=GEMINI_38_LIVE_EXTENDED_THINKING, voice=VOICE, thinking_level="low"
        )
    )
    config = gateway.requests[0]["live_connect_constraints"]["config"]
    assert config["thinking_config"] == {"thinking_level": "LOW"}


def test_to_dict_is_ready_for_a_client(issuer) -> None:
    payload = issuer.issue(LiveSessionOptions(voice=VOICE)).to_dict()
    assert set(payload) == {
        "token",
        "model",
        "api_version",
        "uses",
        "expire_time",
        "new_session_expire_time",
    }


def test_gemini_gateway_uses_the_v1alpha_auth_tokens_api() -> None:
    requests = []

    class AuthTokens:
        def create(self, *, config):
            requests.append(config)
            return NS(name="auth_tokens/xyz")

    gateway = GeminiTokenGateway(NS(auth_tokens=AuthTokens()))

    assert gateway.create({"uses": 1}) == "auth_tokens/xyz"
    assert requests == [{"uses": 1}]
