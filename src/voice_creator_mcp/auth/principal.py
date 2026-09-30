"""The authenticated caller, derived from the validated access token of the request."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

from mcp.server.auth.middleware.auth_context import get_access_token
from mcp.server.auth.provider import AccessToken


@dataclass(frozen=True)
class Principal:
    issuer: str | None
    subject: str
    client_id: str
    scopes: frozenset[str]

    @classmethod
    def from_access_token(cls, token: AccessToken) -> Principal:
        claims = token.claims or {}
        return cls(
            issuer=claims.get("iss"),
            subject=token.subject or f"client:{token.client_id}",
            client_id=token.client_id,
            scopes=frozenset(token.scopes),
        )

    @property
    def workspace_id(self) -> str:
        """Opaque, stable ID for per-user storage. `sub` is only unique per issuer."""
        material = f"{self.issuer or ''}\x00{self.subject}".encode()
        return hashlib.sha256(material).hexdigest()[:32]


def current_principal() -> Principal | None:
    """The principal of the in-flight MCP request, or None on unauthenticated transports."""
    token = get_access_token()
    return Principal.from_access_token(token) if token else None
