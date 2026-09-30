"""Typed tool results. The SDK publishes these as each tool's `outputSchema` and returns
them as `structuredContent` (plus a JSON text block for older clients)."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

RENDER_URI = "voice://renders/{render_id}/{part}"


def render_uri(render_id: str, part: str) -> str:
    return RENDER_URI.format(render_id=render_id, part=part)


class QualityReportInfo(BaseModel):
    requirement: str
    ok: bool = Field(description="True when the recording meets every hard requirement")
    duration_seconds: float
    sample_rate: int
    channels: int
    peak_dbfs: float
    rms_dbfs: float
    silence_ratio: float
    errors: list[str] = Field(description="Blocking problems; fix these and re-record")
    warnings: list[str] = Field(description="Quality issues that may degrade the clone")


class ProfileInfo(BaseModel):
    name: str
    display_name: str
    language_code: str | None
    created_at: str
    has_consent_signature: bool
    voice_id: str | None = Field(description="Stored Gemini voice id, usable in cast maps")
    voice_key: str | None = Field(description="Masked stateless voice key, if any")
    voice_expire_time: str | None
    sample_path: str | None = Field(None, description="Server path (local deployments only)")
    consent_path: str | None = Field(None, description="Server path (local deployments only)")


class RegisteredProfile(BaseModel):
    profile: ProfileInfo
    sample_report: QualityReportInfo
    consent_report: QualityReportInfo


class ProfileList(BaseModel):
    profiles: list[ProfileInfo]


class DeletedProfile(BaseModel):
    deleted: str
    remote_voice_deleted: str | None


class GeminiVoiceInfo(BaseModel):
    voice_type: str
    voice_id: str | None
    has_voice_key: bool
    expire_time: str | None
    display_name: str | None


class GeminiVoiceList(BaseModel):
    voices: list[dict[str, Any]] = Field(description="Stored voices first, then prebuilt ones")
    next_page_token: str | None = Field(description="Pass as page_token for the next page")


class DeletedVoice(BaseModel):
    deleted: str


class ScriptLine(BaseModel):
    speaker: str
    text: str
    style: str | None


class ScriptPreview(BaseModel):
    speakers: list[str]
    lines: list[ScriptLine]


class Segment(BaseModel):
    index: int
    speaker: str
    text: str
    style: str | None
    start_seconds: float
    end_seconds: float


class RenderOutput(BaseModel):
    render_id: str
    audio_uri: str = Field(description="MCP resource with the WAV audio")
    transcript_uri: str = Field(description="MCP resource with the transcript")
    audio_path: str | None = Field(description="Server path (local deployments only)")
    transcript_path: str | None = Field(description="Server path (local deployments only)")
    duration_seconds: float
    segments: list[Segment]


class LiveTurnInfo(BaseModel):
    prompt: str
    transcript: str
    audio_seconds: float
    interrupted: bool


class LiveOutput(BaseModel):
    render_id: str
    audio_uri: str
    transcript_uri: str
    audio_path: str | None
    transcript_path: str | None
    transcript: str
    duration_seconds: float
    model: str
    turns: list[LiveTurnInfo]
    warnings: list[str]


def profile_info(data: dict[str, Any], *, redact: bool) -> ProfileInfo:
    info = ProfileInfo.model_validate(data)
    return info.model_copy(update={"sample_path": None, "consent_path": None}) if redact else info


def output_links(data: dict[str, Any], *, redact: bool) -> dict[str, Any]:
    render_id = data["render_id"]
    return {
        "render_id": render_id,
        "audio_uri": render_uri(render_id, "audio"),
        "transcript_uri": render_uri(render_id, "transcript"),
        "audio_path": None if redact else data["audio_path"],
        "transcript_path": None if redact else data["transcript_path"],
    }
