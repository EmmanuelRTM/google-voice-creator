"""Voice management tools: recordings, private voice profiles and Gemini voices."""

from typing import Annotated, Literal

from mcp.server.mcpserver import MCPServer
from mcp.types import ToolAnnotations
from pydantic import Field

from voice_creator_mcp.app.inputs import MAX_AUDIO_BYTES, load_audio_input
from voice_creator_mcp.server import schemas as out
from voice_creator_mcp.server.guard import ToolGuard, in_thread

_BASE64_MAX_CHARS = (MAX_AUDIO_BYTES + 2) // 3 * 4 + 4096  # slack for line breaks

WavPath = Annotated[
    str | None,
    Field(description="Path to a WAV file readable by the server (local servers)", max_length=4096),
]
WavBase64 = Annotated[
    str | None,
    Field(description="WAV file bytes as base64 (works everywhere)", max_length=_BASE64_MAX_CHARS),
]
ProfileName = Annotated[
    str,
    Field(
        description="Profile id: 1-64 lowercase letters, digits, '-' or '_'",
        pattern=r"^[a-z0-9][a-z0-9_-]{0,63}$",
    ),
]
LanguageCode = Annotated[
    str | None, Field(description="BCP-47 language, e.g. es-MX", max_length=35)
]

_LOCAL_READ = ToolAnnotations(read_only_hint=True, idempotent_hint=True, open_world_hint=False)
_LOCAL_WRITE = ToolAnnotations(read_only_hint=False, destructive_hint=False, open_world_hint=False)
_LOCAL_DELETE = ToolAnnotations(read_only_hint=False, destructive_hint=True, open_world_hint=True)
_REMOTE_READ = ToolAnnotations(read_only_hint=True, idempotent_hint=True, open_world_hint=True)
_REMOTE_WRITE = ToolAnnotations(read_only_hint=False, destructive_hint=False, open_world_hint=True)
_REMOTE_DELETE = ToolAnnotations(read_only_hint=False, destructive_hint=True, open_world_hint=True)


def register_voice_tools(server: MCPServer, guard: ToolGuard) -> None:
    redact = guard.options.redact_paths
    policy = guard.options.path_policy

    @guard.register(
        server,
        name="voice_validate_recording",
        title="Validate a voice recording",
        annotations=_LOCAL_READ,
    )
    async def voice_validate_recording(
        purpose: Annotated[
            Literal["live", "voices_api", "consent"],
            Field(description="Which Gemini requirement to check against"),
        ] = "live",
        wav_path: WavPath = None,
        wav_base64: WavBase64 = None,
    ) -> out.QualityReportInfo:
        """Check a WAV's length, format, loudness, clipping and silence before using it.

        Give exactly one of wav_path or wav_base64. `ok` is false when a hard Gemini
        requirement fails (see `errors`); `warnings` flag quality problems.
        """
        wav = await in_thread(
            load_audio_input, path=wav_path, base64_data=wav_base64, policy=policy, label="wav"
        )
        report = await in_thread(guard.workspace().library.validate_recording, wav, purpose)
        return out.QualityReportInfo.model_validate(report.to_dict())

    @guard.register(
        server,
        name="voice_create_profile",
        title="Create a voice profile",
        annotations=_LOCAL_WRITE,
    )
    async def voice_create_profile(
        name: ProfileName,
        display_name: Annotated[str, Field(description="Speaker name", max_length=100)],
        sample_path: WavPath = None,
        sample_base64: WavBase64 = None,
        consent_path: WavPath = None,
        consent_base64: WavBase64 = None,
        language_code: LanguageCode = None,
        overwrite: bool = False,
    ) -> out.RegisteredProfile:
        """Store a private voice profile: a 10-20 s, 24 kHz mono 16-bit WAV of the speaker
        plus a WAV of the same speaker reading Google's consent statement."""
        sample = await in_thread(
            load_audio_input,
            path=sample_path,
            base64_data=sample_base64,
            policy=policy,
            label="sample",
        )
        consent = await in_thread(
            load_audio_input,
            path=consent_path,
            base64_data=consent_base64,
            policy=policy,
            label="consent",
        )
        result = await in_thread(
            guard.workspace().library.register_profile,
            name,
            display_name=display_name,
            sample_wav=sample,
            consent_wav=consent,
            language_code=language_code,
            overwrite=overwrite,
        )
        return out.RegisteredProfile(
            profile=out.profile_info(result["profile"], redact=redact),
            sample_report=out.QualityReportInfo.model_validate(result["sample_report"]),
            consent_report=out.QualityReportInfo.model_validate(result["consent_report"]),
        )

    @guard.register(
        server,
        name="voice_replicate_profile",
        title="Clone a profile into a Gemini voice",
        annotations=_REMOTE_WRITE,
    )
    async def voice_replicate_profile(
        name: ProfileName,
        store: Annotated[
            bool,
            Field(
                description="true: stored Gemini voice id (up to 1 year); "
                "false: client-held key that expires after about 7 days"
            ),
        ] = True,
    ) -> out.ProfileInfo:
        """Create a Gemini TTS voice from a profile so casts can use `profile:<name>`."""
        profile = await in_thread(guard.workspace().library.replicate_profile, name, store=store)
        return out.profile_info(profile, redact=redact)

    @guard.register(
        server, name="voice_list_profiles", title="List voice profiles", annotations=_LOCAL_READ
    )
    async def voice_list_profiles() -> out.ProfileList:
        """List your voice profiles (secrets masked)."""
        profiles = await in_thread(guard.workspace().library.list_profiles)
        return out.ProfileList(profiles=[out.profile_info(p, redact=redact) for p in profiles])

    @guard.register(
        server,
        name="voice_delete_profile",
        title="Delete a voice profile",
        annotations=_LOCAL_DELETE,
    )
    async def voice_delete_profile(
        name: ProfileName,
        delete_remote_voice: Annotated[
            bool, Field(description="Also delete the stored Gemini voice made from it")
        ] = False,
    ) -> out.DeletedProfile:
        """Permanently delete a profile's recordings, optionally with its Gemini voice."""
        result = await in_thread(
            guard.workspace().library.delete_profile, name, delete_remote=delete_remote_voice
        )
        return out.DeletedProfile.model_validate(result)

    @guard.register(
        server,
        name="voice_design_gemini_voice",
        title="Design a Gemini voice from text",
        annotations=_REMOTE_WRITE,
    )
    async def voice_design_gemini_voice(
        description: Annotated[
            str,
            Field(description="Timbre, age, accent and pace, in plain words", max_length=2000),
        ],
        display_name: Annotated[str | None, Field(max_length=100)] = None,
        language_code: LanguageCode = None,
    ) -> out.GeminiVoiceInfo:
        """Design a new synthetic voice from a description (stored in the Gemini project)."""
        created = await in_thread(
            guard.workspace().library.design_voice,
            description,
            display_name=display_name,
            language_code=language_code,
        )
        return out.GeminiVoiceInfo.model_validate(created)

    @guard.register(
        server,
        name="voice_list_gemini_voices",
        title="List Gemini voices",
        annotations=_REMOTE_READ,
    )
    async def voice_list_gemini_voices(
        types: Annotated[
            list[Literal["prebuilt", "prompted", "replicated"]] | None,
            Field(description="Filter by voice type"),
        ] = None,
        search: Annotated[str | None, Field(max_length=200)] = None,
        page_size: Annotated[int, Field(ge=1, le=100)] = 25,
        page_token: Annotated[str | None, Field(max_length=2048)] = None,
    ) -> out.GeminiVoiceList:
        """List your stored Gemini voices, then the prebuilt catalog (paginated)."""
        result = await in_thread(
            guard.workspace().library.list_voices,
            types=types,
            search=search,
            page_size=page_size,
            page_token=page_token,
        )
        return out.GeminiVoiceList.model_validate(result)

    @guard.register(
        server,
        name="voice_delete_gemini_voice",
        title="Delete a Gemini voice",
        annotations=_REMOTE_DELETE,
    )
    async def voice_delete_gemini_voice(
        voice_id: Annotated[
            str, Field(description="voice_... or voices/voice_...", max_length=200)
        ],
    ) -> out.DeletedVoice:
        """Permanently delete a stored Gemini voice (designed or cloned)."""
        result = await in_thread(guard.workspace().library.delete_voice, voice_id)
        return out.DeletedVoice.model_validate(result)
