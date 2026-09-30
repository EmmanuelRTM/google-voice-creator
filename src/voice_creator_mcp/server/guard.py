"""Cross-cutting policy around every tool and resource.

For each call: authorize (scopes, when auth is on), rate-limit Gemini-costly
tools, bind the caller's private workspace, translate expected failures into
client-visible `ToolError`s (the SDK hides any other exception's message), and
write an audit record. Tools reach their workspace through `ToolGuard.workspace()`.
"""

from __future__ import annotations

import functools
import logging
import re
import time
from collections.abc import Awaitable, Callable
from contextvars import ContextVar
from pathlib import Path
from typing import Any, TypeVar

import anyio
from mcp.server.mcpserver import Context, MCPServer
from mcp.server.mcpserver.exceptions import ResourceError, ToolError
from mcp.types import ToolAnnotations

from voice_creator_mcp.app.audit import Outcome
from voice_creator_mcp.app.workspace import Workspace, WorkspaceProvider
from voice_creator_mcp.auth.principal import Principal, current_principal
from voice_creator_mcp.auth.scopes import BASELINE_SCOPES, required_scopes
from voice_creator_mcp.errors import (
    AuthorizationError,
    RateLimitError,
    VoiceCreatorError,
)
from voice_creator_mcp.server.options import ServerOptions

logger = logging.getLogger(__name__)

R = TypeVar("R")
F = TypeVar("F", bound=Callable[..., Awaitable[Any]])

COSTLY_TOOLS = frozenset(
    {
        "voice_replicate_profile",
        "voice_design_gemini_voice",
        "voice_list_gemini_voices",
        "voice_delete_gemini_voice",
        "voice_render_dialogue",
        "voice_live_dialogue",
    }
)
_RENDER_ID = re.compile(r"^[A-Za-z0-9_-]{1,80}$")
_workspace: ContextVar[Workspace | None] = ContextVar("voice_creator_workspace", default=None)


class ToolGuard:
    def __init__(self, workspaces: WorkspaceProvider, options: ServerOptions) -> None:
        self._workspaces = workspaces
        self.options = options

    def workspace(self) -> Workspace:
        workspace = _workspace.get()
        if workspace is None:
            raise RuntimeError("ToolGuard.workspace() used outside a guarded call")
        return workspace

    def register(
        self, server: MCPServer, *, name: str, title: str, annotations: ToolAnnotations
    ) -> Callable[[F], F]:
        needed = required_scopes(name)  # fails at startup for a tool without a scope policy

        def decorator(fn: F) -> F:
            @functools.wraps(fn)
            async def guarded(*args: Any, **kwargs: Any) -> Any:
                return await self._call(name, needed, fn, args, kwargs)

            server.add_tool(
                guarded,
                name=name,
                title=title,
                annotations=annotations.model_copy(update={"title": title}),
            )
            return fn

        return decorator

    async def read_render(self, render_id: str, suffix: str) -> Path:
        """Resolve a render file in the caller's workspace (resources, read scope)."""
        principal = current_principal()
        started = time.perf_counter()
        outcome: Outcome = "error"
        try:
            self._check_scopes(principal, BASELINE_SCOPES, "reading renders")
            path = self._workspaces.get(principal).output_dir / f"{render_id}{suffix}"
            if not _RENDER_ID.fullmatch(render_id) or not path.is_file():
                raise ResourceError(f"Render {render_id!r} not found")
            outcome = "ok"
            return path
        except AuthorizationError as exc:
            outcome = "denied"
            raise ResourceError(str(exc)) from exc
        finally:
            self.options.audit.record(
                tool=f"resource:render{suffix}",
                principal=principal,
                outcome=outcome,
                duration_ms=(time.perf_counter() - started) * 1000,
            )

    async def _call(
        self,
        name: str,
        needed: frozenset[str],
        fn: Callable[..., Awaitable[R]],
        args: tuple[Any, ...],
        kwargs: dict[str, Any],
    ) -> R:
        principal = current_principal()
        started = time.perf_counter()
        outcome: Outcome = "error"
        error: str | None = None
        try:
            self._check_scopes(principal, needed, name)
            if name in COSTLY_TOOLS and self.options.rate_limiter:
                self.options.rate_limiter.acquire(principal.workspace_id if principal else "local")
            token = _workspace.set(self._workspaces.get(principal))
            try:
                result = await fn(*args, **kwargs)
            finally:
                _workspace.reset(token)
            outcome = "ok"
            return result
        except (VoiceCreatorError, ValueError) as exc:
            error = type(exc).__name__
            if isinstance(exc, AuthorizationError):
                outcome = "denied"
            elif isinstance(exc, RateLimitError):
                outcome = "rate_limited"
            raise ToolError(str(exc)) from exc
        except Exception as exc:
            error = type(exc).__name__
            raise
        finally:
            self.options.audit.record(
                tool=name,
                principal=principal,
                outcome=outcome,
                duration_ms=(time.perf_counter() - started) * 1000,
                error=error,
            )

    def _check_scopes(
        self, principal: Principal | None, needed: frozenset[str], action: str
    ) -> None:
        if not self.options.auth_enabled:
            return
        if principal is None:
            raise AuthorizationError("Authentication required")
        missing = needed - principal.scopes
        if missing:
            raise AuthorizationError(
                f"insufficient_scope: {action} needs {' '.join(sorted(missing))}; "
                "re-authorize the client to grant it"
            )


async def in_thread(fn: Callable[..., R], *args: Any, **kwargs: Any) -> R:
    """Run blocking work (file I/O, Gemini HTTP calls) off the event loop."""
    return await anyio.to_thread.run_sync(functools.partial(fn, *args, **kwargs))


async def report_progress(ctx: Context, done: int, total: int, message: str) -> None:
    """Best effort: clients that sent no progress token, or direct calls, just skip it."""
    try:
        await ctx.report_progress(done, total, message)
    except Exception as exc:
        logger.debug("Progress notification skipped: %s", type(exc).__name__)
