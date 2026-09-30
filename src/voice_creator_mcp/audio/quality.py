"""Pre-flight checks for voice recordings before they are sent to Gemini.

Hard limits (errors) come from the Gemini API contracts; signal checks (warnings)
catch the recording problems that most often degrade a replicated voice.
"""

from __future__ import annotations

import math
import sys
from array import array
from dataclasses import asdict, dataclass

from voice_creator_mcp.audio.pcm import PcmAudio

_FULL_SCALE = 32768.0
_FLOOR_DBFS = -120.0
_CLIP_THRESHOLD = 32_735
_CLIP_RATIO_WARNING = 0.001
_QUIET_RMS_DBFS = -40.0
_SILENT_WINDOW_DBFS = -50.0
_SILENCE_RATIO_WARNING = 0.4
_WINDOW_SECONDS = 0.02


@dataclass(frozen=True)
class SampleRequirements:
    label: str
    min_seconds: float
    max_seconds: float
    sample_rate: int
    sample_rate_required: bool = True
    channels: int = 1
    sample_width: int = 2


# Live API (Gemini Developer API): WAV, 16-bit LE, 24 kHz, 10-20 s.
LIVE_VOICE_SAMPLE = SampleRequirements("Live API voice sample", 10, 20, 24_000)
# Voices API accepts other encodings; 24 kHz mono 16-bit WAV is the recommended input.
VOICES_API_SOURCE = SampleRequirements(
    "Voices API source audio", 10, 30, 24_000, sample_rate_required=False
)
# The spoken consent statement; the same speaker, microphone and room as the sample.
CONSENT_RECORDING = SampleRequirements("consent recording", 3, 30, 24_000)


@dataclass(frozen=True)
class QualityReport:
    requirement: str
    duration_seconds: float
    sample_rate: int
    channels: int
    peak_dbfs: float
    rms_dbfs: float
    silence_ratio: float
    errors: tuple[str, ...]
    warnings: tuple[str, ...]

    @property
    def ok(self) -> bool:
        return not self.errors

    def to_dict(self) -> dict[str, object]:
        payload = asdict(self)
        payload["errors"] = list(self.errors)
        payload["warnings"] = list(self.warnings)
        payload["ok"] = self.ok
        return payload


def assess(audio: PcmAudio, requirements: SampleRequirements) -> QualityReport:
    errors = _format_errors(audio, requirements)
    warnings: list[str] = []
    if audio.sample_rate != requirements.sample_rate and not requirements.sample_rate_required:
        warnings.append(
            f"{requirements.label}: {requirements.sample_rate} Hz is recommended "
            f"(got {audio.sample_rate} Hz)"
        )

    peak_dbfs = rms_dbfs = _FLOOR_DBFS
    silence_ratio = 0.0
    if audio.sample_width == 2 and audio.frame_count:
        samples = _samples(audio)
        peak_dbfs = _dbfs(max(abs(value) for value in samples))
        rms_dbfs = _dbfs(_rms(samples))
        silence_ratio = _silence_ratio(samples, audio.sample_rate * audio.channels)
        warnings.extend(_signal_warnings(samples, rms_dbfs, silence_ratio))

    return QualityReport(
        requirement=requirements.label,
        duration_seconds=round(audio.duration_seconds, 3),
        sample_rate=audio.sample_rate,
        channels=audio.channels,
        peak_dbfs=round(peak_dbfs, 1),
        rms_dbfs=round(rms_dbfs, 1),
        silence_ratio=round(silence_ratio, 3),
        errors=tuple(errors),
        warnings=tuple(warnings),
    )


def _format_errors(audio: PcmAudio, req: SampleRequirements) -> list[str]:
    errors = []
    duration = audio.duration_seconds
    if not req.min_seconds <= duration <= req.max_seconds:
        errors.append(
            f"{req.label} must be between {req.min_seconds:g} and {req.max_seconds:g} seconds "
            f"(got {duration:.1f} s)"
        )
    if req.sample_rate_required and audio.sample_rate != req.sample_rate:
        errors.append(
            f"{req.label} must be {req.sample_rate} Hz (got {audio.sample_rate} Hz); "
            f"resample with `ffmpeg -i in.wav -ar {req.sample_rate} out.wav`"
        )
    if audio.channels != req.channels:
        errors.append(f"{req.label} must be mono (got {audio.channels} channels)")
    if audio.sample_width != req.sample_width:
        errors.append(f"{req.label} must be 16-bit PCM (got {audio.sample_width * 8}-bit)")
    return errors


def _signal_warnings(samples: array, rms_dbfs: float, silence_ratio: float) -> list[str]:
    warnings = []
    clipped = sum(1 for value in samples if abs(value) >= _CLIP_THRESHOLD)
    if clipped / len(samples) > _CLIP_RATIO_WARNING:
        warnings.append("possible clipping: lower the input gain and re-record")
    if rms_dbfs < _QUIET_RMS_DBFS:
        warnings.append(
            f"recording is very quiet ({rms_dbfs:.1f} dBFS RMS); move closer to the mic"
        )
    if silence_ratio > _SILENCE_RATIO_WARNING:
        warnings.append(
            f"{silence_ratio:.0%} of the clip is silence; trim it so the model hears more speech"
        )
    return warnings


def _samples(audio: PcmAudio) -> array:
    samples = array("h", audio.data)
    if sys.byteorder == "big":
        samples.byteswap()
    return samples


def _rms(samples: array) -> float:
    return math.sqrt(sum(value * value for value in samples) / len(samples))


def _dbfs(amplitude: float) -> float:
    return 20 * math.log10(amplitude / _FULL_SCALE) if amplitude > 0 else _FLOOR_DBFS


def _silence_ratio(samples: array, values_per_second: int) -> float:
    window = max(1, int(values_per_second * _WINDOW_SECONDS))
    windows = [samples[start : start + window] for start in range(0, len(samples), window)]
    silent = sum(1 for chunk in windows if _dbfs(_rms(chunk)) < _SILENT_WINDOW_DBFS)
    return silent / len(windows)
