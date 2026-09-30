import asyncio
import json

from voice_creator.diagnostics.report import (
    Check,
    failed,
    passed,
    run_checks,
    skipped,
    warned,
)
from voice_creator.errors import ProfileError

API_KEY = "AIzaSyD-this-is-a-fake-key-for-tests-000"


async def ok() -> object:
    return passed("fine", seconds=1.5)


async def boom() -> object:
    raise ProfileError("Profile 'x' not found")


async def crash() -> object:
    raise RuntimeError(f"upstream said: bad key {API_KEY}")


async def slow() -> object:
    await asyncio.sleep(1)
    return passed("never")


async def test_statuses_and_exceptions_become_results() -> None:
    report = await run_checks(
        [
            Check("a", ok),
            Check("b", lambda: _value(warned("careful"))),
            Check("c", lambda: _value(skipped("not requested"))),
            Check("d", boom),
            Check("e", lambda: _value(failed("no audio", model="m"))),
        ]
    )

    assert [(r.name, r.status) for r in report.results] == [
        ("a", "pass"),
        ("b", "warn"),
        ("c", "skip"),
        ("d", "fail"),
        ("e", "fail"),
    ]
    assert report.results[0].details == {"seconds": 1.5}
    assert report.results[3].summary == "Profile 'x' not found"
    assert report.ok is False


async def test_dependents_are_skipped_when_a_prerequisite_fails() -> None:
    report = await run_checks(
        [Check("profile", boom), Check("live", ok, requires=("profile",)), Check("other", ok)]
    )

    live = report.results[1]
    assert live.status == "skip"
    assert "profile" in live.summary
    assert report.results[2].status == "pass"


async def test_a_warning_satisfies_dependents() -> None:
    report = await run_checks(
        [Check("a", lambda: _value(warned("meh"))), Check("b", ok, requires=("a",))]
    )
    assert report.results[1].status == "pass"


async def test_timeouts_fail_the_check() -> None:
    report = await run_checks([Check("slow", slow, timeout_s=0.01)])
    assert report.results[0].status == "fail"
    assert "timed out" in report.results[0].summary


async def test_secrets_are_redacted_everywhere() -> None:
    async def leaky() -> object:
        return passed("ok", token="auth_tokens/abc123", nested={"key": API_KEY})

    report = await run_checks([Check("crash", crash), Check("leaky", leaky)], secrets=[API_KEY])
    text = json.dumps(report.to_dict()) + report.to_markdown()

    assert API_KEY not in text
    assert "auth_tokens/abc123" not in text
    assert "[redacted]" in text


async def test_report_serialization() -> None:
    report = await run_checks([Check("a", ok), Check("b", boom)], meta={"sdk": "2.25"})

    data = report.to_dict()
    assert data["ok"] is False
    assert data["counts"] == {"pass": 1, "warn": 0, "fail": 1, "skip": 0}
    assert data["meta"] == {"sdk": "2.25"}
    assert data["checks"][0]["name"] == "a"
    markdown = report.to_markdown()
    assert "| a | pass |" in markdown
    assert "| b | fail |" in markdown


async def _value(outcome: object) -> object:
    return outcome
