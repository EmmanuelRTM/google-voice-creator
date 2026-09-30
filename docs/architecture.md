# Architecture

Two packages, one direction of dependency:

- **`voice_creator`** is the voice core: a library, CLI and verifier. It depends only on
  `google-genai`, and a test enforces that it never imports MCP, Starlette, Uvicorn or
  PyJWT.
- **`voice_creator_mcp`** holds the MCP server, the OAuth resource server, the HTTP API
  (`/v1/live-tokens`, `/v1/live`) and the Gemini integrations (`LiveMcpBridge`). It
  depends on the core (the `mcp` extra).

Inside the core, ports and adapters keep `google-genai` at the edge (`gemini/`), so the
domain rules are unit-tested with in-memory fakes.

```mermaid
flowchart TD
    subgraph clients[Clients]
        Local["stdio MCP clients<br/>Claude Code, Gemini CLI"]
        Remote["Remote MCP clients"]
        Browser["Browsers, mobile apps"]
        Backend2["Backends, telephony"]
        Py["Python apps, ADK, Genkit"]
    end
    subgraph mcp[voice_creator_mcp]
        Guard["auth/http_guard.py<br/>OAuthResourceGuard (HTTP + WebSocket)"]
        Server["server/<br/>MCP tools + resources"]
        Api["server/api.py<br/>POST /v1/live-tokens"]
        Ws["server/realtime_ws.py<br/>WS /v1/live"]
        ToolGuard["server/guard.py<br/>scopes, rate limit, audit, errors"]
        Workspaces["app/workspace.py<br/>per-principal workspace"]
        Bridge["integrations/gemini_live.py<br/>LiveMcpBridge"]
    end
    subgraph core[voice_creator]
        Facade["client.py<br/>VoiceCreator, cli.py"]
        Library["studio/voice_library.py"]
        Studio["studio/dialogue_studio.py"]
        Tokens["live/tokens.py<br/>LiveTokenIssuer"]
        Relay["live/realtime.py<br/>RealtimeBridge"]
        Runner["live/runner.py"]
        Port["studio/backend.py<br/>Backend port"]
        Adapters["gemini/*<br/>SDK adapters"]
    end
    Local --> Server
    Remote -->|Bearer JWT| Guard --> Server
    Browser -->|Bearer JWT| Guard --> Api
    Backend2 -->|Bearer JWT| Guard --> Ws
    Py --> Facade
    Server & Api & Ws --> ToolGuard --> Workspaces --> Library & Studio
    Facade --> Library & Studio
    Studio --> Tokens & Relay & Runner
    Library & Studio --> Port -.lazy, server credential.-> Adapters
    Adapters --> Voices[("Voices API")]
    Adapters --> TTS[("Interactions API<br/>gemini-3.8-flash-tts")]
    Adapters --> Live[("Live API<br/>gemini-3.8-live[-extended-thinking]")]
    Adapters --> AuthTokens[("auth_tokens (v1alpha)")]
    Browser -.ephemeral token.-> Live
```

## Layers

| Layer | Modules | Depends on |
|---|---|---|
| Domain | `voice_creator/{audio,dialogue,voices,live}/` | stdlib (plus `Protocol` ports) |
| Application | `voice_creator/studio/` (`voice_library`, `dialogue_studio`, `outputs`, `backend`), `voice_creator/diagnostics/` | domain |
| Adapters | `voice_creator/gemini/*.py`, `studio/backend.GeminiBackend` | `google-genai` |
| Core interface | `voice_creator/client.py` (`VoiceCreator`), `voice_creator/cli.py` | application |
| MCP application | `voice_creator_mcp/app/` (workspaces, input safety, rate limit, audit) | core |
| Auth (HTTP edge) | `voice_creator_mcp/auth/` (scopes, principal, JWT verification, guard, config) | `mcp`, `pyjwt`, `starlette` |
| MCP interface | `voice_creator_mcp/server/` (`core`, `guard`, `schemas`, `voice_tools`, `dialogue_tools`, `resources`, `api`, `realtime_ws`, `composition`, `http`), `__main__.py` | MCP application, auth, `mcp` |
| Integrations | `voice_creator_mcp/integrations/` (`gemini_live.LiveMcpBridge`, `verification`) | core, `mcp`, `google-genai` |

The composition root is `server.composition.create_components`, which returns the MCP
server and the `ToolGuard` shared by the MCP tools and the HTTP API. It chooses the
deployment policy (`ServerOptions`):

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
    C->>S: script + cast [{speaker: "Mario", voice: "profile:mario"}]
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

### Client-to-server: ephemeral token (`POST /v1/live-tokens`)

```mermaid
sequenceDiagram
    participant B as Browser / app
    participant G as OAuthResourceGuard
    participant S as DialogueStudio
    participant A as Gemini auth_tokens (v1alpha)
    participant L as Gemini Live
    B->>G: POST /v1/live-tokens {profile} (Bearer, dialogue:generate)
    G->>S: issue_live_token (rate limit, workspace, audit)
    S->>A: create(uses=1, expire_time, new_session_expire_time,<br/>live_connect_constraints={model, cloned voice, instructions})
    A-->>S: auth_tokens/…
    S-->>B: {token, model, api_version, expire_time} (no-store)
    B->>L: BidiGenerateContentConstrained?access_token=… (voice locked)
    B->>L: 16 kHz microphone PCM
    L-->>B: 24 kHz audio in the cloned voice
```

### Server-to-server: realtime relay (`WS /v1/live`)

```mermaid
sequenceDiagram
    participant C as Backend / phone bridge
    participant W as realtime_ws.py
    participant R as RealtimeBridge
    participant L as Gemini Live
    C->>W: WebSocket handshake (Bearer), Origin checked
    C->>W: {"type": "start", "profile": "me"}
    W->>R: realtime_conversation (resumable, compressed context)
    R->>L: connect(replicated voice, session_resumption)
    par upstream
        C->>R: binary 16 kHz PCM, activity / stream-end controls
        R->>L: send_realtime_input(audio)
    and downstream
        L-->>R: audio, transcripts, interrupted, resumption handles
        R-->>C: binary 24 kHz PCM + JSON events
    end
    L-->>R: GoAway
    R->>L: reconnect with the latest handle
    R-->>C: {"type": "session", "resumed": true}
    C->>R: {"type": "stop"}
    W-->>C: {"type": "session_end"} and close 1000
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
- **No native audio dependencies.** WAV handling, signal checks and resampling use only
  the standard library.
- **The core stands alone.** Apps, ADK agents and Genkit flows use `VoiceCreator`
  directly. MCP is one adapter, not the center.
- **Live tokens never go through MCP.** They are credentials, and tool results end up in
  model context and logs, so tokens are only returned by the HTTP API, with `no-store`.
- **The relay is dispatched before Starlette routing.** The MCP app owns the lifespan
  (session manager), so `with_realtime_route` hands `/v1/live` WebSocket scopes to the
  endpoint and everything else to the MCP app. Both sit behind the OAuth guard.
- **Tool declarations keep their JSON Schema.** `parameters_json_schema` plus
  `NON_BLOCKING` for Extended Thinking. The SDK's automatic MCP conversion drops
  `additionalProperties`, so tool inputs avoid free-form maps.
