"""Verify the voice core end to end against the real Gemini API.

report = await verify_gemini(VoiceCreator(), VerifyPlan(sample=..., consent=...))
print(report.to_markdown())
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
from importlib.metadata import PackageNotFoundError, version

from voice_creator import __version__
from voice_creator.client import VoiceCreator
from voice_creator.diagnostics.gemini_checks import GeminiChecks, TokenConnector, VerifyPlan
from voice_creator.diagnostics.report import Check, CheckResult, Report, run_checks

__all__ = ["Check", "CheckResult", "Report", "VerifyPlan", "run_checks", "verify_gemini"]


async def verify_gemini(
    creator: VoiceCreator,
    plan: VerifyPlan,
    *,
    token_connector: TokenConnector | None = None,
    extra_checks: Sequence[Check] = (),
    secrets: Sequence[str] = (),
) -> Report:
    """Run the core checks, then `extra_checks`; `secrets` are redacted from the report."""
    checks = GeminiChecks(creator, plan, token_connector=token_connector).checks()
    settings = creator.settings
    return await run_checks(
        [*checks, *extra_checks],
        secrets=[settings.api_key or "", *secrets],
        meta={
            "generated": datetime.now(UTC).isoformat(timespec="seconds"),
            "voice-creator": __version__,
            "google-genai": _version("google-genai"),
            "live_model": settings.live_model,
            "tts_model": settings.tts_model,
            "profile": plan.profile,
        },
    )


def _version(package: str) -> str:
    try:
        return version(package)
    except PackageNotFoundError:
        return "not installed"
