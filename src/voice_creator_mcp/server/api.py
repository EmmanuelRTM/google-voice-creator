"""HTTP API next to the MCP endpoint, for apps that talk to Gemini Live themselves.

`POST /v1/live-tokens` implements Google's client-to-server pattern: an authenticated
user gets a short-lived, limited-use Live token whose config (model, cloned voice,
instructions) is locked server-side, so the browser or phone never sees the API key
and cannot change the voice. The token is a credential, so it is returned only over
this API (never through MCP tools, whose results land in a model's context).
"""

from __future__ import annotations

from typing import Literal

from mcp.server.mcpserver import MCPServer
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from voice_creator.errors import (
    AuthorizationError,
    ConfigError,
    ProfileError,
    RateLimitError,
    VoiceCreatorError,
)
from voice_creator.live.tokens import MAX_USES
from voice_creator_mcp.auth.scopes import API_ROUTE_SCOPES, BASELINE_SCOPES, LIVE_TOKENS_PATH
from voice_creator_mcp.server.guard import ToolGuard, in_thread

NO_STORE = {"Cache-Control": "no-store"}


class LiveTokenRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    profile: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]{0,63}$")
    mode: Literal["improvise", "verbatim"] = "improvise"
    persona: str | None = Field(None, max_length=2000)
    model: Literal["gemini-3.8-live", "gemini-3.8-live-extended-thinking"] | None = None
    thinking_level: Literal["low", "medium", "high"] | None = None
    language_code: str | None = Field(None, max_length=35)
    extra_instructions: str | None = Field(None, max_length=2000)
    uses: int = Field(1, ge=1, le=MAX_USES)
    ttl_minutes: int = Field(30, ge=1, le=60)


def register_api_routes(server: MCPServer, guard: ToolGuard) -> None:
    needed = BASELINE_SCOPES | API_ROUTE_SCOPES[LIVE_TOKENS_PATH]

    @server.custom_route(LIVE_TOKENS_PATH, methods=["POST"])
    async def create_live_token(request: Request) -> Response:
        try:
            payload = LiveTokenRequest.model_validate(await request.json())
        except (ValidationError, ValueError) as exc:
            return error_response(400, "invalid_request", _first_error(exc))
        try:
            async with guard.guarded("api:live_tokens", needed, costly=True) as workspace:
                token = await in_thread(
                    workspace.studio.issue_live_token,
                    payload.profile,
                    **payload.model_dump(exclude={"profile"}),
                )
        except VoiceCreatorError as exc:
            return error_for(exc)
        except ValueError as exc:
            return error_response(400, "invalid_request", str(exc))
        return JSONResponse(token.to_dict(), headers=NO_STORE)


def classify(exc: VoiceCreatorError) -> tuple[int, str, str]:
    """(HTTP status, error code, safe description) for a domain error."""
    if isinstance(exc, AuthorizationError):
        return 403, "insufficient_scope", str(exc)
    if isinstance(exc, RateLimitError):
        return 429, "rate_limited", str(exc)
    if isinstance(exc, ConfigError):
        return 503, "server_misconfigured", "The server is not configured for Gemini"
    if isinstance(exc, ProfileError) and "not found" in str(exc):
        return 404, "not_found", str(exc)
    return 400, "invalid_request", str(exc)


def error_for(exc: VoiceCreatorError) -> JSONResponse:
    """Map domain errors to HTTP without leaking internals."""
    status, code, description = classify(exc)
    response = error_response(status, code, description)
    if isinstance(exc, RateLimitError):
        response.headers["Retry-After"] = str(exc.retry_after)
    return response


def error_response(status: int, error: str, description: str) -> JSONResponse:
    return JSONResponse(
        {"error": error, "error_description": description}, status_code=status, headers=NO_STORE
    )


def _first_error(exc: Exception) -> str:
    if isinstance(exc, ValidationError):
        first = exc.errors()[0]
        location = ".".join(str(part) for part in first["loc"]) or "body"
        return f"{location}: {first['msg']}"
    return "Request body must be a JSON object"
