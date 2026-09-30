"""Composition root: wire settings, the Gemini backend and the deployment policy."""

from __future__ import annotations

from mcp.server.mcpserver import MCPServer

from voice_creator.config import Settings
from voice_creator.studio.backend import Backend, GeminiBackend
from voice_creator_mcp.app.inputs import PathPolicy
from voice_creator_mcp.app.rate_limit import RateLimiter
from voice_creator_mcp.app.workspace import WorkspaceProvider
from voice_creator_mcp.server.core import build_server
from voice_creator_mcp.server.options import ServerOptions

DEFAULT_REMOTE_RATE_LIMIT_PER_MINUTE = 30


def create_server(
    settings: Settings | None = None,
    *,
    remote: bool = False,
    authenticated: bool = False,
    backend: Backend | None = None,
) -> MCPServer:
    """Build the server for a deployment.

    * Local (stdio): one trusted user, file paths allowed (optionally confined by
      VOICE_CREATOR_ALLOWED_DIRS), no rate limit unless configured.
    * Remote (HTTP): server paths hidden and path inputs disabled unless
      VOICE_CREATOR_ALLOWED_DIRS is set; Gemini-costly tools rate-limited.
    * Authenticated: per-tool scope checks and a private workspace per principal.

    Gemini is always called with the server's own credential; the caller's OAuth
    token is never forwarded (no token passthrough).
    """
    settings = settings or Settings.from_env()
    backend = backend or GeminiBackend(settings)
    workspaces = WorkspaceProvider(
        data_dir=settings.data_dir,
        output_dir=settings.output_dir,
        backend=backend,
        default_live_model=settings.live_model,
        isolate=authenticated,
    )
    roots = settings.allowed_dirs
    if remote and roots is None:
        roots = ()
    limit = settings.rate_limit_per_minute or (
        DEFAULT_REMOTE_RATE_LIMIT_PER_MINUTE if remote else None
    )
    options = ServerOptions(
        auth_enabled=authenticated,
        redact_paths=remote,
        path_policy=PathPolicy(roots),
        rate_limiter=RateLimiter(limit) if limit else None,
    )
    return build_server(workspaces, options)
