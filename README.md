# google-voice-creator

**Create dialogue in your own voice** with Google's **Gemini 3.8 Live**, **Gemini 3.8
Live Extended Thinking** and **Gemini 3.8 Flash TTS**.

You set up your voice once. After that you can make dialogue in it in any of these ways:

| I want to… | Use | Section |
|---|---|---|
| Ask Claude (or Gemini CLI) to write and voice dialogue for me | the MCP server | [2. Dialogue from Claude](#2-make-dialogue-from-claude-mcp) |
| Script it from Python or the terminal, no MCP | the `voice_creator` library and `voice-creator` CLI | [3. Your own code](#3-use-your-voice-in-your-own-code-no-mcp) |
| Put my voice in agents and flows (ADK, Genkit, the Gemini SDK) | the library plus the [examples](examples/README.md) | [3. Your own code](#agents-flows-and-the-gemini-sdk) |
| Talk in real time from a web page, phone or backend | ephemeral tokens or the `/v1/live` relay | [4. Realtime apps](#4-realtime-apps) |
| Share the server with other people or devices | remote HTTP with OAuth 2.1 | [5. Remote server](#5-run-it-as-a-remote-server) |

Background: [research](docs/research/README.md) (models, voice cloning, use cases, edge
cases, MCP/auth, [Google integration paths](docs/research/07-google-integration.md)) and
[architecture](docs/architecture.md).

## 1. Set up your voice (once)

### Install

You need Python 3.11+, [uv](https://docs.astral.sh/uv/) and [ffmpeg](https://ffmpeg.org/).

```bash
git clone https://github.com/EmmanuelRTM/google-voice-creator.git
cd google-voice-creator
uv tool install --editable ".[mcp]"   # puts voice-creator and voice-creator-mcp on your PATH
```

This is an editable install, so a later `git pull` updates the commands. After a
dependency change, re-run the install with `--force`. To work on the code itself, use
`uv sync --all-extras` and `uv run …` instead (see [Development](#development)).

### Your Gemini key

Create an **auth key** (`AQ.…`) in Google AI Studio. Legacy `AIza…` standard keys
stopped working in September 2026. Keep the key in a file only you can read, and
**never paste it into a chat or a prompt**:

```bash
mkdir -p ~/.config/voice-creator && chmod 700 ~/.config/voice-creator
${EDITOR:-nano} ~/.config/voice-creator/gemini.key    # paste the key here, save
chmod 600 ~/.config/voice-creator/gemini.key
echo 'export GEMINI_API_KEY_FILE="$HOME/.config/voice-creator/gemini.key"' >> ~/.zshrc  # or ~/.bashrc
```

`GEMINI_API_KEY` also works, but a file keeps the key out of shell history and client
configs.

### Record your voice

Record two clips in the same quiet room, with the same microphone:

1. **Voice sample, 10–20 s.** Speak naturally, in the tone you want the clone to have,
   in the language you'll use most (for example Spanish, `es-MX`). Use one speaker, with
   no music or background noise.
2. **Consent statement, 3–30 s.** Read Google's official consent statement for your
   language, word for word. The English one is: *"I am the owner of this voice and I
   consent to Google using this voice to create a synthetic voice model."* For Spanish,
   copy the exact phrase from Google's voice replication page. Gemini rejects consent
   recorded by a different speaker.

Convert both clips to 24 kHz mono 16-bit WAV, and keep them outside this repo (`*.wav`
is git-ignored, but voice recordings are biometric data):

```bash
mkdir -p ~/voice-recordings
ffmpeg -i sample.m4a  -ac 1 -ar 24000 -sample_fmt s16 ~/voice-recordings/sample.wav
ffmpeg -i consent.m4a -ac 1 -ar 24000 -sample_fmt s16 ~/voice-recordings/consent.wav
```

More tips: [recording guide](docs/research/02-voice-creation.md#recording-guide-the-actual-training).

### Create your profile

```bash
voice-creator validate ~/voice-recordings/sample.wav
voice-creator validate ~/voice-recordings/consent.wav --purpose consent
voice-creator profile create me --display-name Mario --language es-MX \
  --sample ~/voice-recordings/sample.wav --consent ~/voice-recordings/consent.wav
voice-creator profile replicate me     # a Gemini TTS voice, stored up to 1 year
```

- **Live** (Gemini speaking as you, improvising) works right after `profile create`.
- **Scripted TTS** (your exact words, line-by-line direction) needs `profile replicate`.
  `--stateless` makes a 7-day key instead, and nothing is stored.

The profile lives in `~/.voice-creator` (owner-only permissions). Renders go to
`~/.voice-creator/output`.

### Verify everything with your key

Nothing in this repo has been run against the live Gemini service yet. The first run
with your key tells you what works on your account:

```bash
voice-creator verify --sample ~/voice-recordings/sample.wav \
  --consent ~/voice-recordings/consent.wav --replicate --report ~/voice-verify.md
```

- **Scratch profile.** This builds a separate `verify` profile from your recordings.
  Your real profile is never replaced.
- **Checking your real profile.** Once `me` exists, check it with
  `voice-creator verify --profile me`.
- **What it covers:**
  - the Voices API;
  - Live and Live Extended Thinking speaking in your voice;
  - reuse of the consent signature;
  - realtime audio;
  - an ephemeral token;
  - TTS, using the profile's existing voice, or a 7-day key with `--replicate`.
- **Output.** The spoken results land in `~/.voice-creator/output`, so you can listen to
  the clone.
- **Cost.** It makes about five short Live sessions and one TTS call.
- **MCP checks.** `uv run python scripts/verify_gemini.py --profile me` (from the repo)
  also checks the MCP integration.
- **Sharing.** Reports never contain keys or tokens, so you can share them.

## 2. Make dialogue from Claude (MCP)

### Connect

**Claude Code**, available in all your projects:

```bash
claude mcp add --scope user voice-creator \
  -e GEMINI_API_KEY_FILE="$HOME/.config/voice-creator/gemini.key" \
  -e VOICE_CREATOR_ALLOWED_DIRS="$HOME/voice-recordings" \
  -- voice-creator-mcp
```

**Claude Desktop** (`claude_desktop_config.json`) or any client that takes `mcpServers`
JSON:

```json
{
  "mcpServers": {
    "voice-creator": {
      "command": "/Users/you/.local/bin/voice-creator-mcp",
      "env": {
        "GEMINI_API_KEY_FILE": "/Users/you/.config/voice-creator/gemini.key",
        "VOICE_CREATOR_ALLOWED_DIRS": "/Users/you/voice-recordings"
      }
    }
  }
}
```

Desktop apps don't see your shell's PATH, so use the absolute path from
`which voice-creator-mcp`.

**Gemini CLI:** `gemini extensions install ./examples/gemini_cli_extension`. It passes
`GEMINI_API_KEY_FILE` (or `GEMINI_API_KEY`) from your environment to the server, asks
before every tool call, and leaves deleting voices to you
([extension](examples/gemini_cli_extension/)).

`VOICE_CREATOR_ALLOWED_DIRS` limits which files the assistant can read to your
recordings folder.

### Ask for dialogue

Talk to it normally, in Spanish or English.

**Scripted, exact words (TTS):**

> Renderiza esta escena con mi perfil `me` como Mario y la voz `Kore` como Ana, nombre de
> salida `cafe`:
> ```
> Mario (cálido, divertido): ¡Llegaste! <laugh> Pensé que te habías perdido.
> Ana (burlona): Tu mapa estaba al revés.
> Mario (fingiendo ofensa): <gasp> ¿Mi mapa? Ese mapa es una obra de arte.
> ```

**Live, Gemini speaking as you:**

> Usa voice_live_dialogue con el perfil `me` y los prompts ["Preséntate con un nuevo
> compañero de equipo", "Cuenta qué haces los fines de semana"].

> Usa voice_live_dialogue con el perfil `me`, modelo `gemini-3.8-live-extended-thinking`,
> thinking_level `medium`: "Planea un viaje de 3 días a Oaxaca".

**Setting up from Claude** (instead of the CLI):

> Valida `~/voice-recordings/sample.wav`, crea el perfil `me` (nombre "Mario", idioma
> es-MX) con el consentimiento `~/voice-recordings/consent.wav`, y replícalo.

Each result includes the file path and the MCP resources
`voice://renders/{render_id}/audio` (WAV) and `voice://renders/{render_id}/transcript`.
[All tools](#tools).

## 3. Use your voice in your own code (no MCP)

The voice core (`voice_creator`) needs only `google-genai`. MCP is optional.

### Python

```python
import asyncio

from voice_creator import VoiceCreator

creator = VoiceCreator()  # reads GEMINI_API_KEY_FILE / GEMINI_API_KEY

# Scripted: your exact words with per-line direction (needs `profile replicate`)
creator.dialogue.render_dialogue(
    "Mario (cálido): ¡Hola! <short pause> ¿Cómo estás?\nAna: Muy bien, ¿y tú?",
    {"Mario": "profile:me", "Ana": "Kore"},
    output_name="saludo",
)

# Live: Gemini improvises as you, in your voice
result = asyncio.run(creator.dialogue.live_dialogue("me", ["Preséntate en una frase"]))
print(result["audio_path"], result["transcript"])
```

### Command line

```bash
voice-creator render scene.txt --cast Mario=profile:me --cast Ana=Kore --output-name cafe
voice-creator live me --prompt "Preséntate" --prompt "¿Qué te gusta cocinar?"
voice-creator live me --mode verbatim --prompt "Hola, soy Mario y esta es mi voz."
voice-creator live me --model gemini-3.8-live-extended-thinking --thinking-level low \
  --prompt "Explica en 30 segundos cómo funciona una hipoteca"
voice-creator profile list
```

Every command prints JSON. Errors go to stderr with a non-zero exit code, so it fits in
shell scripts and pipelines.

### Agents, flows and the Gemini SDK

`creator.live_speech_config("me")` returns the official `types.SpeechConfig` for your
cloned voice, so any Gemini Live code can speak as you:

```python
from google import genai


async def talk(creator: VoiceCreator) -> None:
    config = {"response_modalities": ["AUDIO"], "speech_config": creator.live_speech_config("me")}
    client = genai.Client(api_key=creator.settings.require_api_key())
    async with client.aio.live.connect(model="gemini-3.8-live", config=config) as session:
        await session.send_realtime_input(text="Hola, preséntate")
        async for message in session.receive():
            ...  # 24 kHz PCM audio in your voice
```

Examples you can copy, each run against the real packages:

| Integration | Example |
|---|---|
| Google ADK agent (`run_live`, your voice in `RunConfig`, this server as an `McpToolset`) | [`examples/adk_voice_agent/`](examples/adk_voice_agent/agent.py) |
| Genkit flows (traced, Dev UI, deployable) | [`examples/genkit_flow/`](examples/genkit_flow/flows.py) |
| Gemini Live + MCP tools (`LiveMcpBridge`, async tools for Extended Thinking) | [`examples/live_mcp_bridge.py`](examples/live_mcp_bridge.py) |
| Interactions API `mcp_server` tool (Gemini calls your deployed server) | [`examples/interactions_mcp_server.py`](examples/interactions_mcp_server.py) |

Why each one looks the way it does: [07-google-integration.md](docs/research/07-google-integration.md).

## 4. Realtime apps

These are for talking live, microphone in and your voice out. Run the HTTP server
(locally: `VOICE_CREATOR_AUTH=none voice-creator-mcp --transport http`, on `127.0.0.1`
only) to get two endpoints. Remotely they require an OAuth token with the
`dialogue:generate` scope.

**Web and mobile: `POST /v1/live-tokens`.** This is Google's client-to-server pattern.
The app gets a single-use token that expires in minutes, with your cloned voice, the
model and the instructions locked in. It then talks to Gemini directly and never sees
your API key.

```bash
curl -X POST http://127.0.0.1:8765/v1/live-tokens -H "Content-Type: application/json" \
  -d '{"profile": "me", "uses": 1, "ttl_minutes": 30}'
# {"token": "auth_tokens/…", "model": "gemini-3.8-live", "api_version": "v1alpha", "expire_time": "…", …}
```

The browser connects with `new GoogleGenAI({apiKey: token, httpOptions: {apiVersion:
"v1alpha"}})`; see the [browser example](examples/browser_ephemeral_token/), which uses
microphone streaming and barge-in. For a quick local test, `voice-creator token me`
prints a token. Treat it like a password.

**Backends, phone lines, devices: `WS /v1/live`.** This is the server-to-server relay.
1. Send `{"type": "start", "profile": "me"}`.
2. Stream binary 16-bit PCM mono at 16 kHz.
3. Receive binary 24 kHz PCM in your voice, plus JSON events (`session`,
   `input_transcript`, `output_transcript`, `interrupted`, `turn_complete`, `error`,
   `session_end`).

Push-to-talk is available with `"turn_detection": "manual"` and `activity_start` /
`activity_end`. The relay resumes Gemini sessions after `GoAway`, compresses context
for long calls and caps each call at `max_minutes`. See the
[example client](examples/realtime_ws_client.py). Browsers can't set WebSocket headers,
so they should use tokens.

## 5. Run it as a remote server

```bash
VOICE_CREATOR_OAUTH_ISSUER=https://your-tenant.example-idp.com/ \
VOICE_CREATOR_RESOURCE_URL=https://voice.example.com/mcp \
GEMINI_API_KEY_FILE=/run/secrets/gemini_key \
voice-creator-mcp --transport http --host 0.0.0.0 --port 8765
```

- **Use an external IdP** (Auth0, Okta, Entra ID, Keycloak, WorkOS, …). Register an
  API/resource whose identifier (audience) is `VOICE_CREATOR_RESOURCE_URL`. Define the
  scopes `voice:read`, `voice:write`, `voice:clone`, `voice:delete` and
  `dialogue:generate`. Issue **JWT access tokens** (RS/PS/ES/EdDSA) that include `sub`.
  Scopes are read from `scope` and `scp` (Okta, Entra ID); set
  `VOICE_CREATOR_OAUTH_SCOPE_CLAIMS` if your IdP uses another claim.
- **Discovery.** The server publishes RFC 9728 metadata at
  `/.well-known/oauth-protected-resource/mcp`. It answers `401` with `resource_metadata`,
  and `403 insufficient_scope` for step-up. The same guard protects `/v1/live-tokens` and
  `/v1/live`.
- **Per-user isolation.** Each user gets a private workspace. Remote callers send audio
  as base64, and server paths are never read or returned.
- **Refuses insecure setups.** No OAuth outside loopback, and no plain HTTP OAuth URLs
  off loopback. Terminate TLS at your proxy, and add its public host to
  `VOICE_CREATOR_ALLOWED_HOSTS` if it differs from the resource URL.
- **Local testing without an IdP:** `VOICE_CREATOR_AUTH=none` works **only** on
  `127.0.0.1`.

Details and threat model: [06-mcp-and-auth.md](docs/research/06-mcp-and-auth.md).

## Reference

### Tools

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

Voices can be `profile:<name>`, a stored voice ID (`voice_…`), a voice key
(`voicekey_…`), or a prebuilt voice name (e.g. `Kore`, `Puck`).

The cast format depends on the interface:
- The Python API takes the cast as a map, and the CLI takes `--cast Speaker=voice`.
- The `voice_render_dialogue` MCP tool takes a list:
  `[{"speaker": "Mario", "voice": "profile:me"}, …]`. It allows up to 20 entries, and
  speakers are case-insensitive and may not repeat. A list keeps its shape when
  Gemini's SDK turns MCP tools into function declarations; a free-form map would not.

### Configuration

All configuration comes from environment variables (see `.env.example`). No secrets
live in code.

| Variable | Default | Purpose |
|---|---|---|
| `GEMINI_API_KEY_FILE` / `GEMINI_API_KEY` | none | A file holding your Gemini auth key (mode `600`), or the key itself |
| `VOICE_CREATOR_DATA_DIR` | `~/.voice-creator` | Voice profiles (biometric data, `0700`/`0600`) |
| `VOICE_CREATOR_OUTPUT_DIR` | `$DATA_DIR/output` | Renders for local use |
| `VOICE_CREATOR_TTS_MODEL` | `gemini-3.8-flash-tts` | Or `gemini-3.8-flash-lite-tts` |
| `VOICE_CREATOR_LIVE_MODEL` | `gemini-3.8-live` | Default Live model |
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

## Troubleshooting

| You see | Do this |
|---|---|
| `configuration error: No Gemini API key configured` | Set `GEMINI_API_KEY_FILE` in the environment of the process that runs it. For Claude Code, that's the `-e` flag of `claude mcp add`. |
| A warning about a legacy standard key (`AIza…`) | Create an auth key (`AQ.…`) in Google AI Studio and replace the file's contents. |
| A recording is rejected (rate, length, channels) | Convert it with the `ffmpeg` command above, and trim the sample to 10–20 s. |
| `Profile 'me' has no Gemini voice yet` | Run `voice-creator profile replicate me`, or ask Claude to replicate the profile. |
| `The Gemini voice for profile 'me' expired` | Replicate it again. `--stateless` keys last about 7 days. |
| `cached consent signature was rejected; re-verified…` | Nothing to do. The recording was used instead and a fresh signature was cached. |
| Gemini rejects the consent | Re-record it with the same speaker, room and mic, reading the official phrase exactly. |
| Live `verbatim` mode paraphrases | Use scripted TTS (`render`) when the exact words matter. |
| The assistant can't read a file | Put it under `VOICE_CREATOR_ALLOWED_DIRS`. Remote servers accept base64 only. |
| `403 insufficient_scope` / `429 rate_limited` | The token needs the named scope, or wait `Retry-After` seconds. |

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
client. The same checks run in CI on every push and pull request.

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
  first; see the
  [open questions](docs/research/05-sources.md#open-questions-verify-with-a-real-api-key).
- `gemini-3.8-live` can't use Voices API IDs yet, so Live sessions send the profile's
  sample (and consent, or a cached consent signature) every time.
- Live `verbatim` mode may paraphrase. Use scripted TTS when the exact wording matters.
- The rate limiter is per process. Behind several replicas, also rate-limit at the
  gateway (on the `Mcp-Method`/`Mcp-Name` headers).

## Responsible use

Only clone **your own voice**, or a voice whose owner gave recorded consent. Google
rejects consent recorded by a different speaker. Don't use a cloned voice to deceive
people, and disclose AI-generated audio where required. Generated audio carries SynthID
watermarks; keep them.

Voice profiles are biometric data. Keep recordings and `~/.voice-creator` out of version
control, and delete profiles you no longer need (`voice-creator profile delete me
--delete-remote`). Every tool call is written to an audit log (stderr) without its
content.
