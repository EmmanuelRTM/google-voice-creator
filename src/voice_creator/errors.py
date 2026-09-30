"""Domain errors. Every error the MCP tools surface to clients derives from these."""


class VoiceCreatorError(Exception):
    """Base class for all expected, user-facing failures."""


class ConfigError(VoiceCreatorError):
    """Missing or invalid configuration (for example, no API key)."""


class AudioFormatError(VoiceCreatorError):
    """Audio that cannot be read, or that does not meet a recording requirement."""


class ScriptParseError(VoiceCreatorError):
    """A dialogue script that does not follow the `Speaker (style): text` format."""


class CastError(VoiceCreatorError):
    """A dialogue speaker without a usable voice."""


class ProfileError(VoiceCreatorError):
    """A local voice profile that is missing, invalid or already exists."""


class VoiceApiError(VoiceCreatorError):
    """An unexpected response from the Gemini Voices API."""


class SynthesisError(VoiceCreatorError):
    """Text-to-speech returned no usable audio."""


class LiveConfigError(VoiceCreatorError):
    """A Live session option that the chosen model does not support."""


class LiveSessionError(VoiceCreatorError):
    """A Live session that timed out, closed early or produced no audio."""


class LiveConnectionError(LiveSessionError):
    """The Live session could not be established (for example, a rejected consent signature)."""


class AuthorizationError(VoiceCreatorError):
    """The caller is not authenticated or lacks the scope a tool needs."""


class PathNotAllowedError(VoiceCreatorError):
    """A file path outside the directories this server may read."""


class RateLimitError(VoiceCreatorError):
    """Too many costly calls in the current window."""

    def __init__(self, message: str, *, retry_after: int = 60) -> None:
        super().__init__(message)
        self.retry_after = retry_after
