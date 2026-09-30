# Architecture

The MCP server uses ports and adapters (clean architecture). The domain and application
layers never import `google-genai` or `mcp`, so every rule is unit-tested with
in-memory fakes. Only thin adapters touch the SDKs.

```mermaid
flowchart TD
    Client["MCP client<br/>(Claude Code, Claude Desktop, …)"] -->|stdio| Server["server.py<br/>MCP tools"]
    Server --> Library["app/voice_library.py<br/>VoiceLibrary"]
    Server --> Studio["app/dialogue_studio.py<br/>DialogueStudio"]
    Studio --> Library
    Studio --> Outputs["app/outputs.py<br/>OutputWriter"]
    Library --> Profiles["voices/profiles.py<br/>ProfileStore (0600 files)"]
    Library --> Quality["audio/quality.py<br/>assess()"]
    Library --> Catalog["voices/catalog.py<br/>VoiceCatalog"]
    Studio --> Renderer["dialogue/renderer.py<br/>DialogueRenderer"]
    Studio --> Runner["live/runner.py<br/>LiveDialogueRunner"]
    Runner --> LiveCfg["live/config.py<br/>build_live_config()"]
    Library & Studio --> Backend["app/backend.py<br/>Backend port"]
    Backend -.lazy.-> Gemini["gemini/*<br/>SDK adapters"]
    Gemini --> Voices[("Voices API")]
    Gemini --> TTS[("Interactions API<br/>gemini-3.8-flash-tts")]
    Gemini --> Live[("Live API<br/>gemini-3.8-live[-extended-thinking]")]
```

## Layers

| Layer | Modules | Depends on |
|---|---|---|
| Domain | `audio/`, `dialogue/`, `voices/`, `live/config.py`, `live/instructions.py`, `live/runner.py` | stdlib only (plus `Protocol` ports) |
| Application | `app/voice_library.py`, `app/dialogue_studio.py`, `app/outputs.py`, `app/backend.py` (port) | domain |
| Adapters | `gemini/voices_gateway.py`, `gemini/tts_gateway.py`, `gemini/live_gateway.py`, `app/backend.GeminiBackend` | `google-genai` |
| Interface | `server.py`, `__main__.py` | application, `mcp` |

Ports are `typing.Protocol`s: `VoicesGateway`, `SpeechSynthesizer`, `LiveConnector` /
`LiveSession`, and `Backend`. The composition root, `server.create_server`, wires the
Gemini adapters. Tests wire `tests/fakes.py`.

## Key flows

### Scripted dialogue (`render_dialogue`)

```mermaid
sequenceDiagram
    participant C as MCP client
    participant S as DialogueStudio
    participant L as VoiceLibrary
    participant R as DialogueRenderer
    participant T as Gemini TTS
    C->>S: script + cast {"Mario": "profile:mario"}
    S->>S: parse_script() (fails fast on bad lines)
    S->>L: resolve_voice("profile:mario")
    L-->>S: voice_… or voicekey_… (expiry checked)
    loop each line
        R->>T: text + speech_metadata(style) + voice
        T-->>R: 24 kHz PCM/WAV
    end
    R-->>S: stitched PcmAudio + timed segments
    S-->>C: wav path, transcript path, segments
```

### Live dialogue (`live_dialogue`)

```mermaid
sequenceDiagram
    participant S as DialogueStudio
    participant R as LiveDialogueRunner
    participant G as Gemini 3.8 Live
    S->>S: build_live_config() (model rules checked)
    S->>R: options (sample + consent or signature)
    R->>G: connect(replicated_voice_config)
    alt cached signature rejected
        G--xR: setup error
        R-->>S: LiveConnectionError
        S->>R: retry with consent_audio
    end
    G-->>R: setup_complete(voice_consent_signature)
    loop each prompt
        R->>G: send_realtime_input(text)
        G-->>R: audio chunks, transcripts, turn_complete
        Note over R,G: Extended Thinking: keep reading until<br/>interaction_status != IN_PROGRESS
    end
    R-->>S: audio, transcript, warnings, new signature
    S->>S: cache signature in profile
```

## Design decisions

- **Line-by-line TTS** instead of single-request multi-speaker: it works for any number
  of speakers and any voice type, gives exact segment timings, and limits long-form drift.
- **Interactions API for TTS**: 3.8 TTS takes delivery direction as a `speech_metadata`
  annotation. Putting direction in `generate_content` text would get it read aloud.
- **Local voice profiles**: Live can't use Voices API IDs yet, so the sample and consent
  recordings are kept locally with owner-only permissions and sent per session.
- **Consent-signature cache with fallback**: lower latency on repeat sessions, with
  automatic recovery when the signature expires.
- **Lazy Gemini client**: offline tools work without an API key, and Gemini tools report
  a clear configuration error.
- **No native audio dependencies**: WAV handling and signal checks use only the standard
  library.
