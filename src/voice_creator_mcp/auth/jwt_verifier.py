"""Validate JWT access tokens (RFC 9068) issued by an external OAuth 2.1 authorization server.

The server is a resource server only: it never issues tokens and never forwards the
caller's token anywhere (no token passthrough). A token is accepted only when its
signature verifies against the issuer's JWKS with an allow-listed asymmetric
algorithm, and `iss`, `aud` (this server, RFC 8707), `exp` and `sub` are valid.
Every failure is a rejection (fail closed).
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Sequence
from typing import Any
from urllib.parse import urlsplit

import anyio
import httpx
import jwt
from mcp.server.auth.provider import AccessToken

from voice_creator_mcp.errors import ConfigError

logger = logging.getLogger(__name__)

DEFAULT_ALGORITHMS: tuple[str, ...] = (
    "RS256",
    "RS384",
    "RS512",
    "PS256",
    "PS384",
    "PS512",
    "ES256",
    "ES384",
    "ES512",
    "EdDSA",
)
_REQUIRED_CLAIMS = ["exp", "iss", "aud", "sub"]

KeyResolver = Callable[[str], Any]
"""Returns the verification key for a raw JWT (e.g. by its `kid` from a JWKS)."""


class JwtTokenVerifier:
    def __init__(
        self,
        *,
        issuer: str,
        audience: str,
        key_resolver: KeyResolver,
        algorithms: Sequence[str] = DEFAULT_ALGORITHMS,
        leeway_seconds: int = 30,
    ) -> None:
        unsafe = [
            alg for alg in algorithms if alg.upper().startswith("HS") or alg.lower() == "none"
        ]
        if not algorithms or unsafe:
            raise ValueError(
                f"Only asymmetric JWT algorithms are allowed (rejected: {unsafe or 'empty list'})"
            )
        self._issuer = issuer
        self._audience = audience
        self._key_resolver = key_resolver
        self._algorithms = list(algorithms)
        self._leeway = leeway_seconds

    async def verify_token(self, token: str) -> AccessToken | None:
        try:
            key = await anyio.to_thread.run_sync(self._key_resolver, token)
            claims = jwt.decode(
                token,
                key,
                algorithms=self._algorithms,
                audience=self._audience,
                issuer=self._issuer,
                leeway=self._leeway,
                options={"require": _REQUIRED_CLAIMS},
            )
        except Exception as exc:  # fail closed on any verification problem
            logger.info("Rejected access token: %s", type(exc).__name__)
            return None

        return AccessToken(
            token=token,
            client_id=str(claims.get("client_id") or claims.get("azp") or "unknown"),
            scopes=parse_scopes(claims),
            expires_at=int(claims["exp"]),
            resource=self._audience,
            subject=str(claims["sub"]),
            claims={"iss": claims["iss"]},
        )


def parse_scopes(claims: dict[str, Any]) -> list[str]:
    """Scopes from `scope` (space-delimited, RFC 9068) or `scp` (list, e.g. Entra/Okta)."""
    raw = claims.get("scope", claims.get("scp"))
    if isinstance(raw, str):
        return raw.split()
    if isinstance(raw, list):
        return [str(scope) for scope in raw]
    return []


def jwks_key_resolver(jwks_uri: str, *, cache_seconds: int = 300) -> KeyResolver:
    client = jwt.PyJWKClient(jwks_uri, cache_keys=True, lifespan=cache_seconds)
    return lambda token: client.get_signing_key_from_jwt(token).key


def discover_jwks_uri(
    issuer: str, *, http_get: Callable[[str], dict[str, Any] | None] | None = None
) -> str:
    """Find the issuer's JWKS via RFC 8414 metadata, falling back to OpenID discovery."""
    fetch = http_get or _get_json
    for url in _metadata_urls(issuer):
        metadata = fetch(url)
        if not metadata:
            continue
        if str(metadata.get("issuer", "")).rstrip("/") != issuer.rstrip("/"):
            raise ConfigError(
                f"Authorization server metadata at {url} names issuer "
                f"{metadata.get('issuer')!r}, expected {issuer!r} (possible mix-up attack)"
            )
        jwks_uri = metadata.get("jwks_uri")
        if jwks_uri:
            return str(jwks_uri)
    raise ConfigError(
        f"Could not discover a jwks_uri for issuer {issuer!r}; set VOICE_CREATOR_OAUTH_JWKS_URL"
    )


def _metadata_urls(issuer: str) -> list[str]:
    parts = urlsplit(issuer)
    base = f"{parts.scheme}://{parts.netloc}"
    path = parts.path.rstrip("/")
    return [
        f"{base}/.well-known/oauth-authorization-server{path}",
        f"{base}{path}/.well-known/openid-configuration",
    ]


def _get_json(url: str) -> dict[str, Any] | None:
    try:
        response = httpx.get(url, timeout=10.0, follow_redirects=False)
    except httpx.HTTPError as exc:
        logger.warning("Metadata discovery failed for %s: %s", url, type(exc).__name__)
        return None
    if response.status_code != 200:
        return None
    return response.json()
