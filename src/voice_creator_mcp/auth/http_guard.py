"""ASGI middleware that makes the MCP endpoint an OAuth 2.1 protected resource.

* Serves RFC 9728 Protected Resource Metadata so clients can discover the
  authorization server and the scopes this server understands.
* Returns `401` with `WWW-Authenticate: Bearer resource_metadata=..., scope=...`
  when a token is missing or invalid, and `403 insufficient_scope` naming the
  scopes needed, which lets clients step up authorization incrementally.
* Uses the `Mcp-Method` / `Mcp-Name` routing headers to demand per-tool scopes
  before the request reaches the server. On 2026-07-28 requests the SDK rejects
  headers that disagree with the body; either way tools re-check their scopes
  (defense in depth), so the headers only decide which challenge to send.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping, Sequence
from urllib.parse import urlsplit

from mcp.server.auth.middleware.auth_context import AuthContextMiddleware
from mcp.server.auth.middleware.bearer_auth import AuthenticatedUser
from mcp.server.auth.provider import TokenVerifier
from mcp.server.auth.routes import build_resource_metadata_url
from mcp.shared.auth import ProtectedResourceMetadata
from pydantic import AnyHttpUrl
from starlette.authentication import AuthCredentials
from starlette.datastructures import Headers
from starlette.types import ASGIApp, Message, Receive, Scope, Send

ROOT_METADATA_PATH = "/.well-known/oauth-protected-resource"


class OAuthResourceGuard:
    def __init__(
        self,
        app: ASGIApp,
        *,
        verifier: TokenVerifier,
        resource_url: str,
        authorization_servers: Sequence[str],
        mcp_path: str,
        scopes_supported: Sequence[str],
        baseline_scopes: Iterable[str],
        tool_scopes: Mapping[str, Iterable[str]],
        resource_name: str | None = None,
        documentation_url: str | None = None,
        max_inspected_body: int = 12 * 1024 * 1024,
    ) -> None:
        self._app = app
        self._authenticated_app = AuthContextMiddleware(app)
        self._verifier = verifier
        self._mcp_path = mcp_path.rstrip("/") or "/"
        self._baseline = frozenset(baseline_scopes)
        self._tool_scopes = {name: frozenset(scopes) for name, scopes in tool_scopes.items()}
        self._all_tool_scopes = frozenset().union(*self._tool_scopes.values())
        self._max_inspected_body = max_inspected_body
        self._metadata_url = str(build_resource_metadata_url(AnyHttpUrl(resource_url)))
        self._metadata_paths = {urlsplit(self._metadata_url).path, ROOT_METADATA_PATH}
        # Validate raw strings through the model: it preserves empty URL paths, so issuer
        # identifiers keep their exact form (RFC 8414 compares them character for character).
        metadata = ProtectedResourceMetadata.model_validate(
            {
                "resource": resource_url,
                "authorization_servers": list(authorization_servers),
                "scopes_supported": list(scopes_supported),
                "resource_name": resource_name,
                "resource_documentation": documentation_url,
            }
        )
        self._metadata_body = json.dumps(
            metadata.model_dump(mode="json", exclude_none=True)
        ).encode()

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return
        path = scope["path"]
        if path in self._metadata_paths:
            await self._send_metadata(scope, send)
            return
        if not self._protects(path):
            await self._app(scope, receive, send)
            return

        headers = Headers(scope=scope)
        token = _bearer_token(headers.get("authorization"))
        if token is None:
            await self._challenge(send, 401, self._baseline)
            return
        access = await self._verifier.verify_token(token)
        if access is None:
            await self._challenge(
                send, 401, self._baseline, "invalid_token", "The access token is invalid or expired"
            )
            return

        needed = set(self._baseline) | self._tool_scopes_for(headers)
        if self._needs_body_inspection(scope, headers, access.scopes):
            body, receive = await _buffer_body(receive, self._max_inspected_body)
            needed |= self._scopes_in_body(body)
        if needed - set(access.scopes):
            await self._challenge(
                send,
                403,
                frozenset(needed),
                "insufficient_scope",
                f"This request needs the scopes: {' '.join(sorted(needed))}",
            )
            return

        scope["user"] = AuthenticatedUser(access)
        scope["auth"] = AuthCredentials(access.scopes)
        await self._authenticated_app(scope, receive, send)

    def _tool_scopes_for(self, headers: Headers) -> frozenset[str]:
        """Scopes of the tool a request calls, read from its routing headers.

        2026-07-28 clients send `Mcp-Method: tools/call` and `Mcp-Name`; clients on the
        older handshake send only `Mcp-Name`. The headers are a hint for issuing a proper
        step-up challenge; tools enforce their scopes themselves, so a spoofed header
        cannot grant access.
        """
        method = headers.get("mcp-method")
        if method not in (None, "tools/call"):
            return frozenset()
        return self._tool_scopes.get(headers.get("mcp-name", ""), frozenset())

    def _needs_body_inspection(self, scope: Scope, headers: Headers, granted: list[str]) -> bool:
        """Clients before 2026-07-28 send no routing headers; read the JSON-RPC body instead,
        but only when the token lacks a tool scope the body could require."""
        return (
            scope["method"] == "POST"
            and headers.get("mcp-method") is None
            and not self._all_tool_scopes <= set(granted)
        )

    def _scopes_in_body(self, body: bytes | None) -> frozenset[str]:
        try:
            payload = json.loads(body) if body else None
        except ValueError:
            return frozenset()  # the MCP server reports the parse error
        requests = payload if isinstance(payload, list) else [payload]
        scopes: set[str] = set()
        for request in requests:
            if isinstance(request, dict) and request.get("method") == "tools/call":
                params = request.get("params")
                name = params.get("name") if isinstance(params, dict) else None
                scopes |= (
                    self._tool_scopes.get(name, frozenset()) if isinstance(name, str) else set()
                )
        return frozenset(scopes)

    def _protects(self, path: str) -> bool:
        return path == self._mcp_path or path.startswith(f"{self._mcp_path}/")

    async def _send_metadata(self, scope: Scope, send: Send) -> None:
        cors = [
            (b"access-control-allow-origin", b"*"),
            (b"access-control-allow-methods", b"GET, OPTIONS"),
        ]
        if scope["method"] == "OPTIONS":
            await _respond(send, 204, b"", cors)
            return
        await _respond(
            send, 200, self._metadata_body, [(b"content-type", b"application/json"), *cors]
        )

    async def _challenge(
        self,
        send: Send,
        status: int,
        scopes: frozenset[str],
        error: str | None = None,
        description: str | None = None,
    ) -> None:
        parts = []
        if error:  # RFC 6750 3.1: no error code when the request carried no credentials
            parts += [f'error="{error}"', f'error_description="{description}"']
        parts += [
            f'scope="{" ".join(sorted(scopes))}"',
            f'resource_metadata="{self._metadata_url}"',
        ]
        body = json.dumps(
            {
                "error": error or "unauthorized",
                "error_description": description or "Authentication required",
            }
        ).encode()
        await _respond(
            send,
            status,
            body,
            [
                (b"content-type", b"application/json"),
                (b"www-authenticate", f"Bearer {', '.join(parts)}".encode()),
                (b"access-control-expose-headers", b"WWW-Authenticate"),
            ],
        )


async def _buffer_body(receive: Receive, limit: int) -> tuple[bytes | None, Receive]:
    """Read the request body (up to `limit` bytes) and return a receive that replays it."""
    messages: list[Message] = []
    size = 0
    while True:
        message = await receive()
        messages.append(message)
        if message["type"] != "http.request":
            break
        size += len(message.get("body", b""))
        if size > limit or not message.get("more_body"):
            break
    complete = size <= limit and messages[-1].get("more_body") is not True
    body = b"".join(m.get("body", b"") for m in messages) if complete else None

    async def replay() -> Message:
        return messages.pop(0) if messages else await receive()

    return body, replay


def _bearer_token(authorization: str | None) -> str | None:
    if not authorization:
        return None
    scheme, _, credentials = authorization.partition(" ")
    if scheme.lower() != "bearer" or not credentials.strip():
        return None
    return credentials.strip()


async def _respond(
    send: Send, status: int, body: bytes, headers: list[tuple[bytes, bytes]]
) -> None:
    await send(
        {
            "type": "http.response.start",
            "status": status,
            "headers": [*headers, (b"content-length", str(len(body)).encode())],
        }
    )
    await send({"type": "http.response.body", "body": body})
