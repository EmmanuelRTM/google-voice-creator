"""Streamable HTTP deployment: stateless transport behind the OAuth resource guard."""

from __future__ import annotations

import logging

from mcp.server.auth.provider import TokenVerifier
from starlette.types import ASGIApp

from voice_creator.config import Settings
from voice_creator_mcp.auth.config import HttpConfig, OAuthConfig
from voice_creator_mcp.auth.http_guard import OAuthResourceGuard
from voice_creator_mcp.auth.jwt_verifier import (
    JwtTokenVerifier,
    discover_jwks_uri,
    jwks_key_resolver,
)
from voice_creator_mcp.auth.scopes import ALL_SCOPES, BASELINE_SCOPES, TOOL_SCOPES
from voice_creator_mcp.server.composition import create_server

logger = logging.getLogger(__name__)

RESOURCE_NAME = "Google Voice Creator"
MAX_REQUEST_BODY_BYTES = 12 * 1024 * 1024  # two base64 WAVs of up to 4 MiB each


def build_http_app(
    settings: Settings, http: HttpConfig, *, verifier: TokenVerifier | None = None
) -> ASGIApp:
    http.validate()
    oauth = http.oauth
    server = create_server(settings, remote=True, authenticated=oauth is not None)
    app = server.streamable_http_app(
        streamable_http_path=http.path,
        stateless_http=True,
        transport_security=http.transport_security(),
        host=http.host,
        max_request_body_size=MAX_REQUEST_BODY_BYTES,
    )
    if oauth is None:
        logger.warning(
            "HTTP transport is running WITHOUT authentication on %s (loopback only)", http.host
        )
        return app
    return OAuthResourceGuard(
        app,
        verifier=verifier or build_jwt_verifier(oauth),
        resource_url=oauth.resource_url,
        authorization_servers=[oauth.issuer],
        mcp_path=http.path,
        scopes_supported=ALL_SCOPES,
        baseline_scopes=BASELINE_SCOPES,
        tool_scopes=TOOL_SCOPES,
        resource_name=RESOURCE_NAME,
        documentation_url=oauth.documentation_url,
    )


def build_jwt_verifier(oauth: OAuthConfig) -> JwtTokenVerifier:
    jwks_uri = oauth.jwks_url or discover_jwks_uri(oauth.issuer)
    return JwtTokenVerifier(
        issuer=oauth.issuer,
        audience=oauth.audience,
        key_resolver=jwks_key_resolver(jwks_uri),
        algorithms=oauth.algorithms,
    )


def run_http(settings: Settings, http: HttpConfig) -> None:
    import uvicorn

    app = build_http_app(settings, http)
    uvicorn.run(app, host=http.host, port=http.port, server_header=False, log_level="info")
