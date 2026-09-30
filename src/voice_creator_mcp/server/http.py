"""Streamable HTTP deployment: stateless transport behind the OAuth resource guard."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable

from mcp.server.auth.provider import TokenVerifier
from starlette.types import ASGIApp, Receive, Scope, Send
from starlette.websockets import WebSocket

from voice_creator.config import Settings
from voice_creator.studio.backend import Backend
from voice_creator_mcp.auth.config import HttpConfig, OAuthConfig
from voice_creator_mcp.auth.http_guard import OAuthResourceGuard
from voice_creator_mcp.auth.jwt_verifier import (
    JwtTokenVerifier,
    discover_jwks_uri,
    jwks_key_resolver,
)
from voice_creator_mcp.auth.scopes import (
    ALL_SCOPES,
    API_ROUTE_SCOPES,
    BASELINE_SCOPES,
    LIVE_RELAY_PATH,
    TOOL_SCOPES,
)
from voice_creator_mcp.server.composition import create_components
from voice_creator_mcp.server.realtime_ws import realtime_endpoint

logger = logging.getLogger(__name__)

RESOURCE_NAME = "Google Voice Creator"
MAX_REQUEST_BODY_BYTES = 12 * 1024 * 1024  # two base64 WAVs of up to 4 MiB each


def build_http_app(
    settings: Settings,
    http: HttpConfig,
    *,
    verifier: TokenVerifier | None = None,
    backend: Backend | None = None,
) -> ASGIApp:
    http.validate()
    oauth = http.oauth
    server, guard = create_components(
        settings, remote=True, authenticated=oauth is not None, backend=backend
    )
    security = http.transport_security()
    mcp_app = server.streamable_http_app(
        streamable_http_path=http.path,
        stateless_http=True,
        transport_security=security,
        host=http.host,
        max_request_body_size=MAX_REQUEST_BODY_BYTES,
    )
    app = with_realtime_route(
        mcp_app, realtime_endpoint(guard, allowed_origins=security.allowed_origins)
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
        api_scopes=API_ROUTE_SCOPES,
        resource_name=RESOURCE_NAME,
        documentation_url=oauth.documentation_url,
    )


def with_realtime_route(app: ASGIApp, endpoint: Callable[[WebSocket], Awaitable[None]]) -> ASGIApp:
    """Serve the `/v1/live` WebSocket next to the MCP app (which owns the lifespan)."""

    async def dispatch(scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "websocket" and scope["path"].rstrip("/") == LIVE_RELAY_PATH:
            await endpoint(WebSocket(scope, receive, send))
            return
        await app(scope, receive, send)

    return dispatch


def build_jwt_verifier(oauth: OAuthConfig) -> JwtTokenVerifier:
    jwks_uri = oauth.jwks_url or discover_jwks_uri(oauth.issuer)
    return JwtTokenVerifier(
        issuer=oauth.issuer,
        audience=oauth.audience,
        key_resolver=jwks_key_resolver(jwks_uri),
        algorithms=oauth.algorithms,
        scope_claims=oauth.scope_claims,
    )


def run_http(settings: Settings, http: HttpConfig) -> None:
    import uvicorn

    app = build_http_app(settings, http)
    uvicorn.run(app, host=http.host, port=http.port, server_header=False, log_level="info")
