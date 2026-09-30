import hashlib
import hmac
import json
import time
from typing import Any

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from jwt.utils import base64url_encode

from voice_creator.errors import ConfigError
from voice_creator_mcp.auth.jwt_verifier import JwtTokenVerifier, discover_jwks_uri

ISSUER = "https://auth.example.com/"
AUDIENCE = "https://voice.example.com/mcp"
SIGNING_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
OTHER_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
PUBLIC_KEY = SIGNING_KEY.public_key()


def claims(**overrides: Any) -> dict[str, Any]:
    now = int(time.time())
    values = {
        "iss": ISSUER,
        "aud": AUDIENCE,
        "sub": "user-123",
        "client_id": "claude",
        "scope": "voice:read dialogue:generate",
        "iat": now,
        "exp": now + 300,
    }
    values.update(overrides)
    return {key: value for key, value in values.items() if value is not None}


def sign(payload: dict[str, Any], key: Any = SIGNING_KEY, algorithm: str = "RS256") -> str:
    return jwt.encode(payload, key, algorithm=algorithm, headers={"kid": "k1"})


@pytest.fixture
def verifier() -> JwtTokenVerifier:
    return JwtTokenVerifier(issuer=ISSUER, audience=AUDIENCE, key_resolver=lambda _: PUBLIC_KEY)


async def test_valid_token_becomes_an_access_token(verifier: JwtTokenVerifier) -> None:
    raw = sign(claims())

    access = await verifier.verify_token(raw)

    assert access is not None
    assert access.subject == "user-123"
    assert access.client_id == "claude"
    assert access.scopes == ["voice:read", "dialogue:generate"]
    assert access.resource == AUDIENCE
    assert access.claims == {"iss": ISSUER}
    assert access.expires_at is not None


async def test_audience_list_containing_this_server_is_accepted(verifier) -> None:
    raw = sign(claims(aud=["https://other.example", AUDIENCE]))
    assert await verifier.verify_token(raw) is not None


async def test_scp_claim_is_supported(verifier: JwtTokenVerifier) -> None:
    access = await verifier.verify_token(sign(claims(scope=None, scp=["voice:read"])))
    assert access.scopes == ["voice:read"]


async def test_azp_is_used_when_client_id_is_absent(verifier: JwtTokenVerifier) -> None:
    access = await verifier.verify_token(sign(claims(client_id=None, azp="desktop-app")))
    assert access.client_id == "desktop-app"


@pytest.mark.parametrize(
    "bad_claims",
    [
        {"aud": "https://another-resource.example/mcp"},  # token issued for another server
        {"iss": "https://evil.example/"},
        {"exp": int(time.time()) - 3600},
        {"nbf": int(time.time()) + 3600},
        {"sub": None},
        {"exp": None},
    ],
)
async def test_rejects_invalid_claims(verifier: JwtTokenVerifier, bad_claims: dict) -> None:
    assert await verifier.verify_token(sign(claims(**bad_claims))) is None


async def test_rejects_tokens_signed_by_another_key(verifier: JwtTokenVerifier) -> None:
    assert await verifier.verify_token(sign(claims(), key=OTHER_KEY)) is None


async def test_rejects_unsigned_tokens(verifier: JwtTokenVerifier) -> None:
    unsigned = jwt.encode(claims(), None, algorithm="none")
    assert await verifier.verify_token(unsigned) is None


async def test_rejects_hmac_key_confusion(verifier: JwtTokenVerifier) -> None:
    """Classic attack: sign with HS256 using the server's *public* key as the secret."""
    public_pem = PUBLIC_KEY.public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
    )
    header = base64url_encode(b'{"alg":"HS256","typ":"JWT"}')
    body = base64url_encode(json.dumps(claims()).encode())
    signature = hmac.new(public_pem, header + b"." + body, hashlib.sha256).digest()
    forged = (header + b"." + body + b"." + base64url_encode(signature)).decode()

    assert await verifier.verify_token(forged) is None


async def test_key_resolution_failures_are_rejections() -> None:
    def broken(_: str) -> Any:
        raise jwt.PyJWKClientError("JWKS unreachable")

    verifier = JwtTokenVerifier(issuer=ISSUER, audience=AUDIENCE, key_resolver=broken)
    assert await verifier.verify_token(sign(claims())) is None


async def test_garbage_is_rejected(verifier: JwtTokenVerifier) -> None:
    assert await verifier.verify_token("not-a-jwt") is None


@pytest.mark.parametrize("algorithms", [("HS256",), ("none",), ()])
def test_only_asymmetric_algorithms_are_allowed(algorithms: tuple[str, ...]) -> None:
    with pytest.raises(ValueError, match="algorithm"):
        JwtTokenVerifier(
            issuer=ISSUER,
            audience=AUDIENCE,
            key_resolver=lambda _: PUBLIC_KEY,
            algorithms=algorithms,
        )


# --- Discovery ------------------------------------------------------------


def test_discovery_uses_rfc8414_metadata_with_path_insertion() -> None:
    requested: list[str] = []

    def http_get(url: str) -> dict[str, Any] | None:
        requested.append(url)
        return {
            "issuer": "https://auth.example.com/tenant",
            "jwks_uri": "https://auth.example.com/jwks",
        }

    uri = discover_jwks_uri("https://auth.example.com/tenant", http_get=http_get)

    assert uri == "https://auth.example.com/jwks"
    assert requested == ["https://auth.example.com/.well-known/oauth-authorization-server/tenant"]


def test_discovery_falls_back_to_openid_configuration() -> None:
    requested: list[str] = []

    def http_get(url: str) -> dict[str, Any] | None:
        requested.append(url)
        if "openid-configuration" in url:
            return {"issuer": ISSUER, "jwks_uri": "https://auth.example.com/.well-known/jwks.json"}
        return None

    assert discover_jwks_uri(ISSUER, http_get=http_get).endswith("jwks.json")
    assert requested[-1] == "https://auth.example.com/.well-known/openid-configuration"


def test_discovery_rejects_issuer_mix_up() -> None:
    def http_get(url: str) -> dict[str, Any]:
        return {"issuer": "https://attacker.example/", "jwks_uri": "https://attacker.example/jwks"}

    with pytest.raises(ConfigError, match="issuer"):
        discover_jwks_uri(ISSUER, http_get=http_get)


def test_discovery_requires_a_jwks_uri() -> None:
    with pytest.raises(ConfigError, match="jwks_uri"):
        discover_jwks_uri(ISSUER, http_get=lambda _: None)


@pytest.mark.parametrize(
    ("claim", "value"),
    [("permissions", ["voice:read", "voice:clone"]), ("roles", ["voice:read", "voice:clone"])],
)
async def test_extra_scope_claims_can_be_enabled(claim: str, value: list[str]) -> None:
    """Auth0 RBAC puts permissions in `permissions`; Entra app tokens use `roles`."""
    verifier = JwtTokenVerifier(
        issuer=ISSUER,
        audience=AUDIENCE,
        key_resolver=lambda _: PUBLIC_KEY,
        scope_claims=("scope", "scp", claim),
    )

    access = await verifier.verify_token(sign(claims(scope="openid", **{claim: value})))

    assert set(access.scopes) == {"openid", "voice:read", "voice:clone"}


async def test_extra_scope_claims_are_ignored_by_default(verifier: JwtTokenVerifier) -> None:
    access = await verifier.verify_token(
        sign(claims(scope="voice:read", permissions=["voice:clone"]))
    )
    assert access.scopes == ["voice:read"]
