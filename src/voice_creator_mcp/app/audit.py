"""Structured audit trail of tool calls (JSON lines on the `voice_creator_mcp.audit` logger).

Voice cloning is sensitive, so every call is recorded with who (an opaque workspace
ID and the OAuth client), what (tool name), and the outcome. Arguments, audio,
transcripts, tokens and raw subjects are never logged.
"""

from __future__ import annotations

import json
import logging
from typing import Literal

from voice_creator_mcp.auth.principal import Principal

Outcome = Literal["ok", "denied", "rate_limited", "error"]

audit_logger = logging.getLogger("voice_creator_mcp.audit")


class AuditLog:
    def __init__(self, logger: logging.Logger = audit_logger) -> None:
        self._logger = logger

    def record(
        self,
        *,
        tool: str,
        principal: Principal | None,
        outcome: Outcome,
        duration_ms: float,
        error: str | None = None,
    ) -> None:
        event: dict[str, object] = {
            "event": "tool_call",
            "tool": tool,
            "workspace": principal.workspace_id if principal else "local",
            "client_id": principal.client_id if principal else None,
            "outcome": outcome,
            "duration_ms": round(duration_ms, 1),
        }
        if error:
            event["error"] = error
        self._logger.info(json.dumps(event))
