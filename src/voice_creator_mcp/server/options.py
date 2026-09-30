"""Deployment-dependent policy for the MCP tools."""

from __future__ import annotations

from dataclasses import dataclass, field

from voice_creator_mcp.app.audit import AuditLog
from voice_creator_mcp.app.inputs import PathPolicy
from voice_creator_mcp.app.rate_limit import RateLimiter


@dataclass(frozen=True)
class ServerOptions:
    """Local stdio defaults: trusted caller, readable paths, no limits.

    Remote HTTP deployments turn on scope checks, hide server paths and disable
    path inputs (see `server.composition.create_server`).
    """

    auth_enabled: bool = False
    redact_paths: bool = False
    path_policy: PathPolicy = field(default_factory=lambda: PathPolicy(None))
    rate_limiter: RateLimiter | None = None
    audit: AuditLog = field(default_factory=AuditLog)
