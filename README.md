# google-voice-creator

**Create dialogue in your own voice** with Google's **Gemini 3.8 Live**, **Gemini 3.8
Live Extended Thinking** and **Gemini 3.8 Flash TTS**. Use it as a Python library and
CLI, as an MCP server, or through Google's official integration paths: ADK, Genkit,
Gemini CLI, the Interactions API, ephemeral tokens for browsers, and a server-to-server
realtime relay.

- **Scripted dialogue.** Write `Speaker (style): line` scripts and cast yourself
  (`profile:me`), designed voices or prebuilt voices. Get back one WAV and a transcript.
- **Live dialogue.** Have Gemini 3.8 Live improvise or read lines *as you*, in your
  cloned voice. Switch to Extended Thinking for turns that need reasoning.
- **Voice management.** Validate recordings, keep private voice profiles, clone or
  design Gemini voices, and list or delete them.
- **Realtime apps.** Browsers and phones connect to Gemini directly with single-use
  tokens that have your voice locked in. Backends and phone lines stream audio through
  the `/v1/live` relay.
- **Secure by default.** It runs locally over stdio. As a remote Streamable HTTP server
  it is an OAuth 2.1 resource server with per-tool scopes and per-user isolation
  ([details](docs/research/06-mcp-and-auth.md)).

Research: **[docs/research/](docs/research/README.md)** (models, voice cloning, use cases,
edge cases, MCP/auth, [Google integration paths](docs/research/07-google-integration.md)).
Architecture: **[docs/architecture.md](docs/architecture.md)**. Examples:
**[examples/](examples/README.md)**.

## Quick start (local, stdio)

Requirements: Python 3.11+, [uv](https://docs.astral.sh/uv/), and a Gemini **auth key**
(`AQ.…`) from Google AI Studio. Legacy `AIza…` standard keys stopped working in
September 2026.

```bash
git clone https://github.com/EmmanuelRTM/google-voice-creator.git
cd google-voice-creator
uv sync --extra dev
uv sync --all-extras   # core + MCP server + dev tools (the core alone: `uv sync`)
cp .env.example .env   # set GEMINI_API_KEY; never commit .env
```

Keep the key in your environment or a key file, and never paste it into a chat.

**Claude Code**

```bash
claude mcp add voice-creator \
  -e GEMINI_API_KEY="$GEMINI_API_KEY" \
  -- uv --directory "$(pwd)" run --extra mcp voice-creator-mcp
```

**Gemini CLI:** `gemini extensions install ./examples/gemini_cli_extension`
([extension](examples/gemini_cli_extension/)).

**Claude Desktop / other clients** (`mcpServers` JSON):

```json
{
  "mcpServers": {
    "voice-creator": {
      "command": "uv",
      "args": ["--directory", "/absolute/path/to/google-voice-creator", "run", "--extra", "mcp", "voice-creator-mcp"],
      "env": { "GEMINI_API_KEY_FILE": "/absolute/path/to/gemini.key" }
    }
  }
}
```

Prefer `GEMINI_API_KEY_FILE`, pointing at a `chmod 600` file or a mounted secret, over
putting the key in client config.

## Without MCP: library and CLI

The voice core (`voice_creator`) needs only `google-genai`:

```python
from voice_creator import VoiceCreator

creator = VoiceCreator()  # GEMINI_API_KEY / GEMINI_API_KEY_FILE from the environment
creator.create_profile("me", display_name="Mario", sample="sample.wav", consent="consent.wav")
creator.voices.replicate_profile("me")  # a Gemini TTS voice
creator.dialogue.render_dialogue("Mario (warm): ¡Hola!", {"Mario": "profile:me"})
await creator.dialogue.live_dialogue("me", ["Preséntate"])  # Gemini 3.8 Live, your voice
creator.live_speech_config("me")  # types.SpeechConfig for the google-genai SDK or ADK
```

```bash
uv run voice-creator validate sample.wav
uv run voice-creator profile create me --display-name Mario --sample sample.wav --consent consent.wav
uv run voice-creator render scene.txt --cast Mario=profile:me --cast Ana=Kore
uv run voice-creator live me --prompt "Preséntate" --model gemini-3.8-live-extended-thinking --thinking-level low
uv run voice-creator verify --sample sample.wav --consent consent.wav --replicate --report report.md
```

## Verify with your key

`voice-creator verify` exercises every integration path against the real Gemini API and
writes a report you can share (keys and tokens are redacted). It covers recordings, the
Voices API, Live and Live Extended Thinking in your voice, consent-signature reuse,
realtime audio, ephemeral tokens and, with `--replicate`, TTS with a 7-day voice key
(nothing stored). `scripts/verify_gemini.py` adds the MCP checks: tool schemas with your
SDK version, an Extended Thinking session calling an MCP tool, and, with `--mcp-url`,
the Interactions API calling your deployed server. See
[what each check answers](docs/research/07-google-integration.md#10-what-still-needs-your-key).

## Create your voice (once)

1. **Record a 10–20 s sample**: natural speech, quiet room, mono.
2. **Record the consent statement** in the same room on the same mic. In English:
   *"I am the owner of this voice and I consent to Google using this voice to create a
   synthetic voice model."* Other languages have their own official phrase.
3. **Convert both** to 24 kHz mono 16-bit WAV:
   `ffmpeg -i in.m4a -ac 1 -ar 24000 -sample_fmt s16 sample.wav`
4. Ask your MCP client:

> Validate `/path/sample.wav`, then create voice profile `me` (display name "Mario",
> language es-MX) with consent `/path/consent.wav`, and replicate it.

See the [recording guide](docs/research/02-voice-creation.md#recording-guide-the-actual-training).

## Make dialogue

**Scripted (exact words, via TTS):**

> Render this with me as Mario and Kore as Ana, output name `cafe-scene`
> (the tool takes `cast=[{"speaker": "Mario", "voice": "profile:me"}, {"speaker": "Ana", "voice": "Kore"}]`):
> ```
> Mario (warm, amused): ¡Llegaste! <laugh> Pensé que te habías perdido.
> Ana (teasing): Tu mapa estaba al revés.
> Mario (mock offended): <gasp> ¿Mi mapa?
>   Ese mapa es una obra de arte.
> ```

**Live (Gemini speaks as you):**

> Use voice_live_dialogue with profile `me` and prompts ["Introduce yourself to a new
> teammate", "Explain what you do on weekends"].

> Use voice_live_dialogue with profile `me`, model `gemini-3.8-live-extended-thinking`,
> thinking_level `medium`, prompt "Walk through planning a 3-day trip to Oaxaca".

Every render is also available as MCP resources:
`voice://renders/{render_id}/audio` (WAV) and `voice://renders/{render_id}/transcript`.

## Tools

| Tool | What it does | OAuth scope | Calls Gemini |
|---|---|---|---|
| `voice_validate_recording` | Checks duration, format, loudness, clipping and silence (`live`, `voices_api` or `consent` rules) | `voice:read` | No |
| `voice_create_profile` | Saves a sample and consent as a private profile (path or base64) | `voice:write` | No |
| `voice_replicate_profile` | Clones a profile into a Gemini TTS voice (`store=true`: up to 1 year; `false`: about 7 days) | `voice:clone` | Yes |
| `voice_list_profiles` | Lists your profiles (secrets masked) | `voice:read` | No |
| `voice_delete_profile` | Deletes a profile, optionally with its Gemini voice | `voice:delete` | Optional |
| `voice_design_gemini_voice` | Designs a voice from a text description | `voice:write` | Yes |
| `voice_list_gemini_voices` | Lists your stored voices and the prebuilt catalog (paginated) | `voice:read` | Yes |
| `voice_delete_gemini_voice` | Deletes a stored Gemini voice | `voice:delete` | Yes |
| `voice_preview_script` | Parses a script and lists its speakers | `voice:read` | No |
| `voice_render_dialogue` | Multi-speaker script to WAV and transcript (Gemini 3.8 Flash TTS), with progress | `dialogue:generate` | Yes |
| `voice_live_dialogue` | Prompts to spoken replies in your voice (Gemini 3.8 Live / Extended Thinking), with progress | `dialogue:generate` | Yes |

Every tool has a title, annotations (read-only, destructive, idempotent, open-world)
and a typed `outputSchema`. Scopes only apply to the authenticated HTTP transport.

### Script format

```
# comments and blank lines are ignored
Speaker: text
Speaker (style direction): text with <laugh> or <short pause> tags
  indented lines continue the previous line
```

`voice_render_dialogue` takes the cast as a list of `{"speaker", "voice"}` entries (up to
20; speakers are case-insensitive and may not repeat). A list of objects keeps its shape
when Gemini's SDK converts MCP tools into function declarations; a free-form map would
not. Voices: `profile:<name>`, a stored voice ID (`voice_…`), a voice key (`voicekey_…`),
or a prebuilt voice name (e.g. `Kore`, `Puck`).

## Realtime apps: ephemeral tokens and the relay

The HTTP transport also serves two endpoints behind the same OAuth guard. Both need the
`dialogue:generate` scope.

**`POST /v1/live-tokens`** (browsers, mobile). This is Google's client-to-server pattern.
The response is a single-use Live token that expires in minutes, with the model, your
cloned voice and the instructions locked in. The app connects to Gemini directly and
never sees the API key.

```bash
curl -X POST https://voice.example.com/v1/live-tokens \
  -H "Authorization: Bearer $ACCESS_TOKEN" -H "Content-Type: application/json" \
  -d '{"profile": "me", "uses": 1, "ttl_minutes": 30}'
# {"token": "auth_tokens/…", "model": "gemini-3.8-live", "api_version": "v1alpha", "expire_time": "…", …}
```

Then connect with `new GoogleGenAI({apiKey: token, httpOptions: {apiVersion: "v1alpha"}})`
([browser example](examples/browser_ephemeral_token/)). Tokens are never returned by an
MCP tool, because tool results end up in a model's context.

**`WS /v1/live`** (backends, telephony, devices). This is the server-to-server relay.
1. Send `{"type": "start", "profile": "me"}`.
2. Stream binary 16-bit PCM mono at 16 kHz.
3. Receive binary 24 kHz PCM in your voice, plus JSON events (`session`,
   `input_transcript`, `output_transcript`, `interrupted`, `turn_complete`, `error`,
   `session_end`).

Push-to-talk is available with `"turn_detection": "manual"` and `activity_start` /
`activity_end`. The relay resumes Gemini sessions after `GoAway`, compresses context
for long calls and caps each call at `max_minutes`.
[Protocol and example client](examples/realtime_ws_client.py). Browsers can't set
WebSocket headers, so they should use tokens instead.

## Integrations

| Integration | Example |
|---|---|
| Google ADK agent (`run_live`, cloned voice in `RunConfig`, MCP as `McpToolset`) | [`examples/adk_voice_agent/`](examples/adk_voice_agent/agent.py) |
| Genkit flows | [`examples/genkit_flow/`](examples/genkit_flow/flows.py) |
| Gemini CLI extension | [`examples/gemini_cli_extension/`](examples/gemini_cli_extension/) |
| Interactions API `mcp_server` tool | [`examples/interactions_mcp_server.py`](examples/interactions_mcp_server.py) |
| google-genai Live + MCP (`LiveMcpBridge`, async tools for Extended Thinking) | [`examples/live_mcp_bridge.py`](examples/live_mcp_bridge.py) |
| Server-to-server relay client | [`examples/realtime_ws_client.py`](examples/realtime_ws_client.py) |
| Browser with ephemeral tokens | [`examples/browser_ephemeral_token/`](examples/browser_ephemeral_token/) |

Why each one looks the way it does: [docs/research/07-google-integration.md](docs/research/07-google-integration.md).

## Remote deployment (Streamable HTTP + OAuth 2.1)

```bash
VOICE_CREATOR_OAUTH_ISSUER=https://your-tenant.example-idp.com/ \
VOICE_CREATOR_RESOURCE_URL=https://voice.example.com/mcp \
GEMINI_API_KEY_FILE=/run/secrets/gemini_key \
uv run --extra mcp voice-creator-mcp --transport http --host 0.0.0.0 --port 8765
```

- **Use an external IdP** (Auth0, Okta, Entra ID, Keycloak, WorkOS, …). Register an
  API/resource whose identifier (audience) is `VOICE_CREATOR_RESOURCE_URL`. Define the
  scopes `voice:read`, `voice:write`, `voice:clone`, `voice:delete` and
  `dialogue:generate`. Issue **JWT access tokens** (RS/PS/ES/EdDSA) that include `sub`.
  Scopes are read from `scope` and `scp` (Okta, Entra ID); set
  `VOICE_CREATOR_OAUTH_SCOPE_CLAIMS` if your IdP uses another claim.
- **Discovery.** The server publishes RFC 9728 metadata at
  `/.well-known/oauth-protected-resource/mcp`. It answers `401` with `resource_metadata`,
  and `403 insufficient_scope` for step-up.
- **Per-user isolation.** Each user gets a private workspace. Remote callers send audio
  as base64, and server paths are never read or returned.
- **Refuses insecure setups.** No OAuth outside loopback, and no plain HTTP OAuth URLs
  off loopback. Terminate TLS at your proxy, and add its public host to
  `VOICE_CREATOR_ALLOWED_HOSTS` if it differs from the resource URL.
- **Local testing without an IdP:** `VOICE_CREATOR_AUTH=none` works **only** on
  `127.0.0.1`.

## Configuration

All configuration comes from environment variables (see `.env.example`). No secrets
live in code.

| Variable | Default | Purpose |
|---|---|---|
| `GEMINI_API_KEY` / `GEMINI_API_KEY_FILE` | none | Gemini auth key, or a file containing it (mode `600`) |
| `VOICE_CREATOR_DATA_DIR` | `~/.voice-creator` | Voice profiles (biometric data, `0700`/`0600`) |
| `VOICE_CREATOR_OUTPUT_DIR` | `$DATA_DIR/output` | Renders for local use |
| `VOICE_CREATOR_TTS_MODEL` | `gemini-3.8-flash-tts` | Or `gemini-3.8-flash-lite-tts` |
| `VOICE_CREATOR_LIVE_MODEL` | `gemini-3.8-live` | Default for `voice_live_dialogue` |
| `VOICE_CREATOR_ALLOWED_DIRS` | unrestricted (stdio) | Confine file-path inputs (`os.pathsep`-separated) |
| `VOICE_CREATOR_RATE_LIMIT_PER_MINUTE` | off / 30 over HTTP | Costly Gemini calls per user per minute |
| `VOICE_CREATOR_TRANSPORT` | `stdio` | `stdio` or `http` (or `--transport`) |
| `VOICE_CREATOR_HTTP_HOST` / `_PORT` / `_PATH` | `127.0.0.1` / `8765` / `/mcp` | HTTP binding |
| `VOICE_CREATOR_AUTH` | `oauth` | `none` is allowed only on loopback |
| `VOICE_CREATOR_OAUTH_ISSUER` | none | Authorization server issuer (exact string) |
| `VOICE_CREATOR_RESOURCE_URL` | none | Public URL of the MCP endpoint (the resource) |
| `VOICE_CREATOR_OAUTH_AUDIENCE` | resource URL | Expected `aud`, if your IdP uses another identifier |
| `VOICE_CREATOR_OAUTH_JWKS_URL` | discovered | JWKS endpoint (else RFC 8414 / OIDC discovery) |
| `VOICE_CREATOR_OAUTH_ALGORITHMS` | asymmetric set | Comma-separated JWS algorithms (HS* and `none` rejected) |
| `VOICE_CREATOR_OAUTH_SCOPE_CLAIMS` | `scope,scp` | Claims that carry granted scopes (their union is used) |
| `VOICE_CREATOR_ALLOWED_HOSTS` / `_ORIGINS` | resource host and loopback | DNS-rebinding allow-lists; `_ORIGINS` also gates `/v1/live` WebSocket origins |
| `VOICE_CREATOR_MCP_URL` / `_BEARER` | none | Examples and `scripts/verify_gemini.py`: a deployed server and a token for it |
| `VOICE_CREATOR_DOCS_URL` | none | `resource_documentation` in the metadata |

## Development

```bash
uv sync --all-extras
uv run pytest            # unit tests (no network, no API key)
uv run ruff check .      # lint
uv run ruff format .     # format
```

Tests are written first and use in-memory fakes for every external port
(`tests/fakes.py`). The Gemini adapters are tested against duck-typed fakes that mirror
`google-genai` 2.25. The auth layer is tested with real RS256 JWTs and Starlette's test
client.

```
src/voice_creator/            the voice core (depends only on google-genai)
  audio/        PCM/WAV primitives, quality checks, 16 kHz conversion
  dialogue/     script parser, cast rules, line-by-line renderer
  voices/       local profile store and Voices API catalog
  live/         Live config rules, instructions, dialogue runner, tokens, realtime bridge
  gemini/       google-genai adapters (Voices, Interactions TTS, Live, auth tokens)
  studio/       use cases: VoiceLibrary, DialogueStudio, outputs, Backend port
  diagnostics/  end-to-end verification against the real API
  client.py     VoiceCreator facade      cli.py   voice-creator CLI
src/voice_creator_mcp/        the MCP server and HTTP API (the `mcp` extra)
  app/          workspaces, input safety, rate limit, audit
  auth/         OAuth 2.1 resource server: scopes, JWT verification, guard, config
  server/       MCP tools, resources, schemas, /v1/live-tokens, /v1/live, HTTP app
  integrations/ LiveMcpBridge (Gemini Live + MCP tools), MCP verification checks
examples/                     official integration paths (see examples/README.md)
scripts/verify_gemini.py      core + MCP verification with your key
```

## Status and limitations

- Built against the `google-genai` **2.25.0** SDK types and MCP Python SDK **2.2**
  (protocol 2026-07-28, with 2025-11-25 clients still supported). The examples were run
  against `google-adk` 2.10, `genkit` 0.12 and `@google/genai` 2.24. Every payload
  validates against the SDK's own models, but nothing **has been run against the live
  Gemini service yet** (no key was available while building). Run `voice-creator verify`
  with your key; see the
  [open questions](docs/research/05-sources.md#open-questions-verify-with-a-real-api-key).
- `gemini-3.8-live` can't use Voices API IDs yet, so Live sessions send the profile's
  sample (and consent, or a cached consent signature) every time.
- `voice_live_dialogue(mode="verbatim")` may paraphrase. Use `voice_render_dialogue`
  when the exact wording matters.
- The rate limiter is per process. Behind several replicas, also rate-limit at the
  gateway (on the `Mcp-Method`/`Mcp-Name` headers).

## Responsible use

Only clone **your own voice**, or a voice whose owner gave recorded consent. Google
rejects consent recorded by a different speaker. Don't use a cloned voice to deceive
people, and disclose AI-generated audio where required. Generated audio carries SynthID
watermarks; keep them. Voice profiles are biometric data: keep the data directory out of
version control (it is in `.gitignore`) and delete profiles you no longer need. Every
tool call is written to an audit log (stderr) without its content.
