import pytest

from voice_creator.errors import ConfigError
from voice_creator_mcp.auth.config import HttpConfig

OAUTH_ENV = {
    "VOICE_CREATOR_OAUTH_ISSUER": "https://auth.example.com/",
    "VOICE_CREATOR_RESOURCE_URL": "https://voice.example.com/mcp",
}


def test_defaults_bind_to_loopback_and_require_oauth() -> None:
    config = HttpConfig.from_env({})

    assert (config.host, config.port, config.path) == ("127.0.0.1", 8765, "/mcp")
    assert config.is_loopback
    with pytest.raises(ConfigError, match="VOICE_CREATOR_OAUTH_ISSUER"):
        config.validate()


def test_oauth_configuration_from_env() -> None:
    config = HttpConfig.from_env(
        {
            **OAUTH_ENV,
            "VOICE_CREATOR_OAUTH_JWKS_URL": "https://auth.example.com/jwks",
            "VOICE_CREATOR_OAUTH_ALGORITHMS": "ES256, RS256",
        }
    )
    config.validate()

    assert config.oauth is not None
    assert config.oauth.issuer == "https://auth.example.com/"
    assert config.oauth.resource_url == "https://voice.example.com/mcp"
    assert config.oauth.audience == "https://voice.example.com/mcp"
    assert config.oauth.jwks_url == "https://auth.example.com/jwks"
    assert config.oauth.algorithms == ("ES256", "RS256")


def test_audience_can_differ_from_resource_url() -> None:
    config = HttpConfig.from_env({**OAUTH_ENV, "VOICE_CREATOR_OAUTH_AUDIENCE": "api://voice"})
    assert config.oauth.audience == "api://voice"


def test_unauthenticated_mode_is_loopback_only() -> None:
    HttpConfig.from_env({"VOICE_CREATOR_AUTH": "none"}).validate()

    exposed = HttpConfig.from_env(
        {"VOICE_CREATOR_AUTH": "none", "VOICE_CREATOR_HTTP_HOST": "0.0.0.0"}
    )
    with pytest.raises(ConfigError, match="non-loopback"):
        exposed.validate()


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("VOICE_CREATOR_OAUTH_ISSUER", "http://auth.example.com"),
        ("VOICE_CREATOR_RESOURCE_URL", "http://voice.example.com/mcp"),
    ],
)
def test_oauth_urls_must_use_https_off_loopback(key: str, value: str) -> None:
    with pytest.raises(ConfigError, match="https"):
        HttpConfig.from_env({**OAUTH_ENV, key: value}).validate()


def test_http_is_allowed_for_local_development_urls() -> None:
    HttpConfig.from_env(
        {
            "VOICE_CREATOR_OAUTH_ISSUER": "http://localhost:9000",
            "VOICE_CREATOR_RESOURCE_URL": "http://127.0.0.1:8765/mcp",
        }
    ).validate()


@pytest.mark.parametrize(
    ("key", "value", "message"),
    [
        ("VOICE_CREATOR_AUTH", "basic", "VOICE_CREATOR_AUTH"),
        ("VOICE_CREATOR_HTTP_PORT", "70000", "port"),
        ("VOICE_CREATOR_HTTP_PORT", "abc", "port"),
        ("VOICE_CREATOR_HTTP_PATH", "mcp", "path"),
    ],
)
def test_invalid_values_are_reported(key: str, value: str, message: str) -> None:
    with pytest.raises(ConfigError, match=message):
        HttpConfig.from_env({**OAUTH_ENV, key: value}).validate()


def test_transport_security_allows_the_public_host_and_loopback() -> None:
    config = HttpConfig.from_env(
        {**OAUTH_ENV, "VOICE_CREATOR_ALLOWED_ORIGINS": "https://claude.ai"}
    )

    security = config.transport_security()

    assert security.enable_dns_rebinding_protection is True
    assert "voice.example.com" in security.allowed_hosts
    assert "127.0.0.1:*" in security.allowed_hosts
    assert security.allowed_origins[-1] == "https://claude.ai"


def test_transport_security_on_public_interfaces_omits_loopback() -> None:
    config = HttpConfig.from_env(
        {
            **OAUTH_ENV,
            "VOICE_CREATOR_HTTP_HOST": "0.0.0.0",
            "VOICE_CREATOR_ALLOWED_HOSTS": "voice.internal:8765",
        }
    )

    security = config.transport_security()

    assert "127.0.0.1:*" not in security.allowed_hosts
    assert security.allowed_hosts == ["voice.example.com", "voice.internal:8765"]


def test_scope_claims_are_configurable() -> None:
    config = HttpConfig.from_env(
        {**OAUTH_ENV, "VOICE_CREATOR_OAUTH_SCOPE_CLAIMS": "scope, permissions"}
    )
    assert config.oauth.scope_claims == ("scope", "permissions")


def test_scope_claims_default_to_scope_and_scp() -> None:
    assert HttpConfig.from_env(OAUTH_ENV).oauth.scope_claims == ("scope", "scp")
