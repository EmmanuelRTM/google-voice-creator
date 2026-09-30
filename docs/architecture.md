# Architecture

The MCP server uses ports and adapters (clean architecture). The domain and application
layers never import `google-genai`. MCP, OAuth and HTTP concerns live at the edges
(`server/`, `auth/`), so the core rules are unit-tested with in-memory fakes.

```mermaid
flowchart TD
    Local["Local client (stdio)<br/>Claude Code, Claude Desktop"] --> Server
    Remote["Remote client (Streamable HTTP)"] -->|Bearer JWT| Guard["auth/http_guard.py<br/>OAuthResourceGuard<br/>RFC 9728 metadata, 401/403 step-up"]
    Guard -->|validated principal| Server["server/<br/>MCP tools + resources"]
    Server --> ToolGuard["server/guard.py<br/>scopes · rate limit · audit · errors"]
    ToolGuard --> Workspaces["app/workspace.py<br/>per-principal workspace"]
    Workspaces --> Library["app/voice_library.py<br/>VoiceLibrary"]
    Workspaces --> Studio["app/dialogue_studio.py<br/>DialogueStudio"]
    Library --> Profiles["voices/profiles.py<br/>ProfileStore (0600 files)"]
    Library --> Catalog["voices/catalog.py"]
    Studio --> Renderer["dialogue/renderer.py"]
    Studio --> Runner["live/runner.py"]
    Library & Studio --> Backend["app/backend.py<br/>Backend port"]
    Backend -.lazy, server credential.-> Gemini["gemini/*<br/>SDK adapters"]
    Gemini --> Voices[("Voices API")]
    Gemini --> TTS[("Interactions API<br/>gemini-3.8-flash-tts")]
    Gemini --> Live[("Live API<br/>gemini-3.8-live[-extended-thinking]")]
```

## Layers

| Layer | Modules | Depends on |
|---|---|---|
| Domain | `audio/`, `dialogue/`, `voices/`, `live/` | stdlib (plus `Protocol` ports) |
| Application | `app/voice_library.py`, `app/dialogue_studio.py`, `app/workspace.py`, `app/inputs.py`, `app/rate_limit.py`, `app/audit.py`, `app/outputs.py`, `app/backend.py` | domain |
| Adapters | `gemini/*.py`, `app/backend.GeminiBackend` | `google-genai` |
| Auth (HTTP edge) | `auth/scopes.py`, `auth/principal.py`, `auth/jwt_verifier.py`, `auth/http_guard.py`, `auth/config.py` | `mcp`, `pyjwt`, `starlette` |
| Interface | `server/` (`core`, `guard`, `schemas`, `voice_tools`, `dialogue_tools`, `resources`, `composition`, `http`), `__main__.py` | application, auth, `mcp` |

The composition root is `server.composition.create_server`. It chooses the deployment
policy (`ServerOptions`):

| | stdio (local) | HTTP + OAuth (remote) |
|---|---|---|
| Caller | Trusted local user | Validated JWT principal |
| Scope checks | Off | Per tool (and per HTTP request) |
| Workspace | One shared (`VOICE_CREATOR_DATA_DIR`) | One per principal (`principals/<sha256>`) |
| File-path inputs | Allowed (optionally confined) | Disabled; base64 only |
| Server paths in results | Shown | Redacted; use `voice://renders/…` resources |
| Rate limit | Off unless configured | 30 costly calls/min per principal (default) |

## Key flows

### Authorization (HTTP)

```mermaid
sequenceDiagram
    participant C as MCP client
    participant G as OAuthResourceGuard
    participant V as JwtTokenVerifier
    participant S as MCP server / ToolGuard
    C->>G: POST /mcp (no token)
    G-->>C: 401 WWW-Authenticate: resource_metadata, scope="voice:read"
    C->>G: GET /.well-known/oauth-protected-resource/mcp
    G-->>C: RFC 9728 metadata (issuer, scopes_supported)
    Note over C: OAuth 2.1 + PKCE with the IdP, resource=<this server>
    C->>G: tools/call voice_replicate_profile (Bearer, Mcp-Name)
    G->>V: verify signature, iss, aud, exp, sub
    V-->>G: AccessToken(scopes=[voice:read])
    G-->>C: 403 insufficient_scope, scope="voice:clone voice:read"
    Note over C: step-up: re-authorize with the union of scopes
    C->>G: retry with the new token
    G->>S: request + principal (auth context)
    S->>S: re-check scopes, rate limit, bind workspace, audit
```

### Scripted dialogue (`voice_render_dialogue`)

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
        T-->>R: 24 kHz audio
        R-->>C: progress notification
    end
    S-->>C: render_id, voice://renders/{id}/audio, segments
```

### Live dialogue (`voice_live_dialogue`)

```mermaid
sequenceDiagram
    participant S as DialogueStudio
    participant R as LiveDialogueRunner
    participant G as Gemini 3.8 Live
    S->>S: build_live_config() (model rules checked)
    S->>R: options (sample + consent or cached signature)
    R->>G: connect(replicated_voice_config)
    alt cached signature rejected
        G--xR: setup error
        R-->>S: LiveConnectionError
        S->>R: retry with consent_audio
    end
    loop each prompt
        R->>G: send_realtime_input(text)
        G-->>R: audio, transcripts, turn_complete
        Note over R,G: Extended Thinking: keep reading until<br/>interaction_status != IN_PROGRESS
    end
    R-->>S: audio, transcript, warnings, new signature
```

## Design decisions

- **Two layers of authorization.** The HTTP guard issues spec-compliant `401`/`403`
  step-up challenges, using routing headers or, for older clients, the JSON-RPC body.
  Every tool then re-checks scopes from the validated token, so a spoofed header can
  never grant access.
- **Resource server only.** Tokens come from an external IdP. The server validates
  audience-bound JWTs and never forwards them (no token passthrough).
- **Per-principal workspaces.** Biometric data is partitioned by `sha256(issuer, subject)`
  with owner-only permissions.
- **Remote servers are base64-only.** Tool arguments can be prompt-injected, so a remote
  server never reads caller-supplied server paths.
- **Line-by-line TTS through the Interactions API.** It works for any cast and voice type,
  and delivery direction goes in `speech_metadata`, where it isn't read aloud.
- **Local voice profiles for Live.** Live can't use Voices API IDs yet. Recordings are
  kept locally and sent per session, with a cached consent signature.
- **Lazy Gemini client.** Offline tools work without an API key.
- **No native audio dependencies.** WAV handling and signal checks use only the
  standard library.
