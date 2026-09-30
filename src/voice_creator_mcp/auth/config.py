"""Streamable HTTP transport and OAuth resource-server configuration (secure by default).

* Binds to loopback unless told otherwise.
* OAuth is required; `VOICE_CREATOR_AUTH=none` is only accepted on loopback.
* OAuth URLs must use HTTPS except for loopback development setups.
* DNS-rebinding protection is always on, allowing loopback plus the public host.
"""

from __future__ import annotations

import ipaddress
from collections.abc import Mapping
from dataclasses import dataclass
from urllib.parse import urlsplit

from mcp.server.transport_security import TransportSecuritySettings

from voice_creator_mcp.auth.jwt_verifier import DEFAULT_ALGORITHMS
from voice_creator_mcp.errors import ConfigError

AUTH_MODES = ("oauth", "none")
_LOOPBACK_NAMES = {"localhost"}
_LOOPBACK_ALLOWED_HOSTS = ("127.0.0.1:*", "localhost:*", "[::1]:*")
_LOOPBACK_ALLOWED_ORIGINS = ("http://127.0.0.1:*", "http://localhost:*", "http://[::1]:*")


@dataclass(frozen=True)
class OAuthConfig:
    issuer: str
    resource_url: str
    audience: str
    jwks_url: str | None = None
    algorithms: tuple[str, ...] = DEFAULT_ALGORITHMS
    documentation_url: str | None = None


@dataclass(frozen=True)
class HttpConfig:
    host: str = "127.0.0.1"
    port: int = 8765
    path: str = "/mcp"
    auth_mode: str = "oauth"
    issuer: str | None = None
    resource_url: str | None = None
    audience: str | None = None
    jwks_url: str | None = None
    algorithms: tuple[str, ...] = DEFAULT_ALGORITHMS
    documentation_url: str | None = None
    allowed_hosts: tuple[str, ...] = ()
    allowed_origins: tuple[str, ...] = ()

    @classmethod
    def from_env(cls, env: Mapping[str, str]) -> HttpConfig:
        def read(name: str) -> str | None:
            value = env.get(name, "").strip()
            return value or None

        def listing(name: str) -> tuple[str, ...]:
            return tuple(item.strip() for item in (read(name) or "").split(",") if item.strip())

        return cls(
            host=read("VOICE_CREATOR_HTTP_HOST") or "127.0.0.1",
            port=_port(read("VOICE_CREATOR_HTTP_PORT") or "8765"),
            path=read("VOICE_CREATOR_HTTP_PATH") or "/mcp",
            auth_mode=(read("VOICE_CREATOR_AUTH") or "oauth").lower(),
            issuer=read("VOICE_CREATOR_OAUTH_ISSUER"),
            resource_url=read("VOICE_CREATOR_RESOURCE_URL"),
            audience=read("VOICE_CREATOR_OAUTH_AUDIENCE"),
            jwks_url=read("VOICE_CREATOR_OAUTH_JWKS_URL"),
            algorithms=listing("VOICE_CREATOR_OAUTH_ALGORITHMS") or DEFAULT_ALGORITHMS,
            documentation_url=read("VOICE_CREATOR_DOCS_URL"),
            allowed_hosts=listing("VOICE_CREATOR_ALLOWED_HOSTS"),
            allowed_origins=listing("VOICE_CREATOR_ALLOWED_ORIGINS"),
        )

    @property
    def is_loopback(self) -> bool:
        return _is_loopback(self.host)

    @property
    def oauth(self) -> OAuthConfig | None:
        if self.auth_mode != "oauth" or not (self.issuer and self.resource_url):
            return None
        return OAuthConfig(
            issuer=self.issuer,
            resource_url=self.resource_url,
            audience=self.audience or self.resource_url,
            jwks_url=self.jwks_url,
            algorithms=self.algorithms,
            documentation_url=self.documentation_url,
        )

    def validate(self) -> None:
        if self.auth_mode not in AUTH_MODES:
            raise ConfigError(f"VOICE_CREATOR_AUTH must be one of {', '.join(AUTH_MODES)}")
        if not self.path.startswith("/"):
            raise ConfigError("VOICE_CREATOR_HTTP_PATH: the endpoint path must start with '/'")
        if self.auth_mode == "none":
            if not self.is_loopback:
                raise ConfigError(
                    f"Refusing to serve without authentication on non-loopback host {self.host!r}; "
                    "configure OAuth (VOICE_CREATOR_OAUTH_ISSUER, VOICE_CREATOR_RESOURCE_URL)"
                )
            return
        missing = [
            name
            for name, value in (
                ("VOICE_CREATOR_OAUTH_ISSUER", self.issuer),
                ("VOICE_CREATOR_RESOURCE_URL", self.resource_url),
            )
            if not value
        ]
        if missing:
            raise ConfigError(
                f"The HTTP transport requires OAuth; set {', '.join(missing)} "
                "(or VOICE_CREATOR_AUTH=none for loopback-only development)"
            )
        for label, url in (
            ("VOICE_CREATOR_OAUTH_ISSUER", self.issuer),
            ("VOICE_CREATOR_RESOURCE_URL", self.resource_url),
            ("VOICE_CREATOR_OAUTH_JWKS_URL", self.jwks_url),
        ):
            if url:
                _require_https(label, url)

    def transport_security(self) -> TransportSecuritySettings:
        hosts: list[str] = list(_LOOPBACK_ALLOWED_HOSTS) if self.is_loopback else []
        origins: list[str] = list(_LOOPBACK_ALLOWED_ORIGINS) if self.is_loopback else []
        public_host = urlsplit(self.resource_url).netloc if self.resource_url else ""
        if public_host and public_host not in hosts:
            hosts.append(public_host)
        hosts += [host for host in self.allowed_hosts if host not in hosts]
        origins += [origin for origin in self.allowed_origins if origin not in origins]
        return TransportSecuritySettings(
            enable_dns_rebinding_protection=True, allowed_hosts=hosts, allowed_origins=origins
        )


def _port(value: str) -> int:
    try:
        port = int(value)
    except ValueError:
        port = 0
    if not 1 <= port <= 65_535:
        raise ConfigError("VOICE_CREATOR_HTTP_PORT must be a port number between 1 and 65535")
    return port


def _is_loopback(host: str | None) -> bool:
    if not host:
        return False
    if host.lower() in _LOOPBACK_NAMES:
        return True
    try:
        return ipaddress.ip_address(host.strip("[]")).is_loopback
    except ValueError:
        return False


def _require_https(label: str, url: str) -> None:
    parts = urlsplit(url)
    if parts.scheme == "https" or (parts.scheme == "http" and _is_loopback(parts.hostname)):
        return
    raise ConfigError(f"{label} must use https (http is only allowed for loopback): {url}")
