"""A small check runner: ordered checks, prerequisites, timeouts, and a redacted report.

Reports are meant to be shared (pasted in an issue, attached to a PR), so every
string in them goes through redaction: configured secrets, Gemini API keys and
ephemeral Live tokens never appear, even if an upstream error message echoes them.
"""

from __future__ import annotations

import asyncio
import re
import time
from collections.abc import Awaitable, Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal

from voice_creator.errors import VoiceCreatorError

Status = Literal["pass", "warn", "fail", "skip"]
STATUSES: tuple[Status, ...] = ("pass", "warn", "fail", "skip")
REDACTED = "[redacted]"
_SECRET_PATTERNS = (
    re.compile(r"auth_tokens/[A-Za-z0-9._~-]+"),  # ephemeral Live tokens
    re.compile(r"\bAIza[0-9A-Za-z_-]{20,}"),  # legacy standard API keys
    re.compile(r"\bAQ\.[0-9A-Za-z_.-]{20,}"),  # auth keys
    re.compile(r"(?i)(bearer\s+)[A-Za-z0-9._~+/=-]{8,}"),
)


@dataclass(frozen=True)
class Outcome:
    status: Status
    summary: str
    details: dict[str, Any] = field(default_factory=dict)


def passed(summary: str, **details: Any) -> Outcome:
    return Outcome("pass", summary, details)


def warned(summary: str, **details: Any) -> Outcome:
    return Outcome("warn", summary, details)


def failed(summary: str, **details: Any) -> Outcome:
    return Outcome("fail", summary, details)


def skipped(summary: str, **details: Any) -> Outcome:
    return Outcome("skip", summary, details)


@dataclass(frozen=True)
class Check:
    name: str
    run: Callable[[], Awaitable[Outcome]]
    requires: tuple[str, ...] = ()
    timeout_s: float = 180.0


@dataclass(frozen=True)
class CheckResult:
    name: str
    status: Status
    summary: str
    details: dict[str, Any]
    seconds: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "status": self.status,
            "summary": self.summary,
            "details": self.details,
            "seconds": self.seconds,
        }


@dataclass(frozen=True)
class Report:
    results: tuple[CheckResult, ...]
    meta: dict[str, Any] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return all(result.status != "fail" for result in self.results)

    def counts(self) -> dict[str, int]:
        return {status: sum(r.status == status for r in self.results) for status in STATUSES}

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "counts": self.counts(),
            "meta": self.meta,
            "checks": [result.to_dict() for result in self.results],
        }

    def to_markdown(self) -> str:
        lines = [
            "# Gemini verification report",
            "",
            *(f"- **{key}**: {value}" for key, value in self.meta.items()),
            "",
            "| Check | Status | Summary | Seconds |",
            "| --- | --- | --- | --- |",
        ]
        for result in self.results:
            summary = result.summary.replace("|", "\\|").replace("\n", " ")
            lines.append(f"| {result.name} | {result.status} | {summary} | {result.seconds} |")
        return "\n".join(lines) + "\n"


async def run_checks(
    checks: Sequence[Check],
    *,
    secrets: Iterable[str] = (),
    meta: Mapping[str, Any] | None = None,
) -> Report:
    """Run checks in order; a check whose prerequisite failed or was skipped is skipped."""
    redact = _Redactor(secrets)
    usable: set[str] = set()
    results = []
    for check in checks:
        missing = [name for name in check.requires if name not in usable]
        started = time.monotonic()
        if missing:
            outcome = skipped(f"Needs {', '.join(missing)} to pass first")
        else:
            outcome = await _run_one(check)
        if outcome.status in ("pass", "warn"):
            usable.add(check.name)
        results.append(
            CheckResult(
                name=check.name,
                status=outcome.status,
                summary=redact(outcome.summary),
                details=redact(outcome.details),
                seconds=round(time.monotonic() - started, 2),
            )
        )
    return Report(tuple(results), redact(dict(meta or {})))


async def _run_one(check: Check) -> Outcome:
    try:
        async with asyncio.timeout(check.timeout_s):
            return await check.run()
    except TimeoutError:
        return failed(f"timed out after {check.timeout_s:g} s")
    except VoiceCreatorError as exc:
        return failed(str(exc))
    except Exception as exc:  # report every failure; a verification run must not crash
        return failed(f"{type(exc).__name__}: {exc}")


class _Redactor:
    def __init__(self, secrets: Iterable[str]) -> None:
        self._secrets = sorted({s for s in secrets if s and len(s) >= 6}, key=len, reverse=True)

    def __call__(self, value: Any) -> Any:
        if isinstance(value, str):
            return self._text(value)
        if isinstance(value, Mapping):
            return {key: self(item) for key, item in value.items()}
        if isinstance(value, list | tuple):
            return [self(item) for item in value]
        return value

    def _text(self, text: str) -> str:
        for secret in self._secrets:
            text = text.replace(secret, REDACTED)
        for pattern in _SECRET_PATTERNS:
            text = pattern.sub(
                lambda m: (m.group(1) if m.lastindex else "") + REDACTED,
                text,
            )
        return text
