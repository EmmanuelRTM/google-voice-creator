# Integrating your voice the way Google recommends

How Google's SDKs and products expect Gemini Live voice to be wired into apps, flows and
agents, and how this repo implements each path. Evidence levels as in the
[README](README.md): **[SDK]** means read in, or run against, the package source:
`google-genai` 2.25.0, `@google/genai` 2.24.0, `google-adk` 2.10.0, `genkit` 0.12.0 and
`mcp` 2.2.0.

## The paths at a glance

| Integration | Google's recommended shape | In this repo | Checked by |
| --- | --- | --- | --- |
| Python library | `google-genai` SDK, no framework | `voice_creator.VoiceCreator` (no MCP dependency) | unit tests |
| Command line | n/a | `voice-creator` CLI | unit tests |
| Web and mobile clients | Client-to-server with **ephemeral tokens** | `POST /v1/live-tokens`, [browser example](../../examples/browser_ephemeral_token/) | tests; JS SDK run with a fake socket |
| Backends, telephony | **Server-to-server** relay holding the Live session | `/v1/live` WebSocket, [client example](../../examples/realtime_ws_client.py) | tests; real uvicorn + websockets |
| Agents | **ADK** `Runner.run_live`, `RunConfig.speech_config`, `McpToolset` | [ADK example](../../examples/adk_voice_agent/agent.py) | real ADK with a fake Live socket |
| Flows | **Genkit** flows and traced steps | [Genkit example](../../examples/genkit_flow/flows.py) | real Genkit with fakes |
| Terminal | **Gemini CLI** extension with `mcpServers` | [extension](../../examples/gemini_cli_extension/) | manifest tests |
| Remote tool use | **Interactions API** `mcp_server` tool | [example](../../examples/interactions_mcp_server.py) | real SDK against a local stub |
| Live + tools | SDK function calling (`NON_BLOCKING` on 3.8 ET) | `LiveMcpBridge`, [example](../../examples/live_mcp_bridge.py) | tests; real stdio MCP server |
| All of the above, for real | n/a | `voice-creator verify`, `scripts/verify_gemini.py` | needs your key |

## 1. Library and CLI, independent of MCP

The voice core (`src/voice_creator/`) depends only on `google-genai`. The MCP server
(`src/voice_creator_mcp/`, the `mcp` extra) is one adapter on top of it. A test fails the
build if the core ever imports `mcp`, `starlette`, `uvicorn` or `jwt`.

```python
from voice_creator import VoiceCreator

creator = VoiceCreator()  # GEMINI_API_KEY from the environment
creator.create_profile("me", display_name="Me", sample="me.wav", consent="consent.wav")
await creator.dialogue.live_dialogue("me", ["Introduce yourself"])
creator.live_speech_config("me")  # types.SpeechConfig for SDK/ADK
```

`live_speech_config` returns the official `types.SpeechConfig` with the
`replicated_voice_config`. It carries the cached consent signature when one exists,
otherwise the consent recording. **[SDK]**

## 2. Client-to-server: ephemeral tokens

Google's recommendation for browsers and phones is to connect to Live directly, with a
short-lived token minted by your backend, never the API key. **[SDK]**
`client.auth_tokens.create` exists only on `v1alpha`. The token is used as the API key
and the JS SDK then connects to `BidiGenerateContentConstrained?access_token=…`
(observed by running `@google/genai` 2.24 against a fake socket).

What `POST /v1/live-tokens` does:

- It authenticates the user (OAuth bearer, `dialogue:generate` scope, per-user workspace
  and rate limit), then builds the same Live config as the server's own sessions.
- It locks the **whole** config into `live_connect_constraints` (model, cloned voice,
  system instructions, thinking level). `lock_additional_fields` is omitted, which locks
  every field, so the client cannot swap the voice. The SDK docstring still mentions
  `live_constrained_parameters`, but that field is stale.
- Defaults: `uses=1`, 30 min `expire_time`, and 1 min `new_session_expire_time` (the
  window for starting the session). Caps: 5 uses, 60 min.
- It responds `{token, model, api_version, uses, expire_time, new_session_expire_time}`
  with `Cache-Control: no-store`. Errors use RFC 6750 style: `401`/`403 insufficient_scope`,
  `404` unknown profile, `429` with `Retry-After`, `503` when the server has no key.

Edge cases and decisions:

- **Tokens are credentials.** They are only returned over this HTTP API, never through an
  MCP tool, because tool results land in a model's context and logs.
- **Consent signature vs recording.** With a cached signature the token is small. Without
  one, the consent WAV travels inside the token config. Both serialize through the SDK.
- **Browsers cannot set `Authorization` on WebSockets**, which is why browsers use tokens
  and not the `/v1/live` relay.
- **Same origin.** The endpoint sends no CORS headers. Serve it on the app's origin
  through your reverse proxy (the browser example proxies it with Vite).

## 3. Server-to-server: the `/v1/live` relay

For telephony, embedded devices and apps that must keep audio on their own
infrastructure, the backend holds the Live session and relays audio.

Protocol:

1. Handshake with `Authorization: Bearer …` (`dialogue:generate`). A failure closes
   with `1008`.
2. Client → `{"type": "start", "profile": "me", ...}`. The options mirror
   `/v1/live-tokens`, plus `turn_detection` (`vad` | `manual`), `input_transcription`
   and `max_minutes` (1–60).
3. Client → binary frames of 16-bit PCM mono at 16 kHz (at most 64 KiB each). JSON
   control frames: `activity_start`/`activity_end` (push-to-talk), `audio_stream_end`,
   `{"type": "text", "text": …}`, `stop`.
4. Server → binary 24 kHz PCM (the cloned voice). JSON events: `session`,
   `input_transcript`, `output_transcript`, `interrupted`, `turn_complete` (with
   `status`), `error`, `session_end`.

Following the Live API's session guidance **[SDK]**:

- **Barge-in.** `interrupted` is forwarded, and clients must flush queued playback.
- **Session resumption.** Handles from `session_resumption_update` are tracked. On
  `GoAway` or a dropped socket the relay reconnects with the latest handle (up to 3
  times), and the client only sees a new `session` event with `resumed: true`.
- **Long calls.** Sliding-window `context_window_compression` lifts the ~15 min
  audio-session limit, and `max_minutes` caps cost.
- **Push-to-talk** (`turn_detection: "manual"`) disables automatic activity detection.
  This avoids 3.8's end-of-turn issues with clients that stream digital silence.
- **Security.** The OAuth guard verifies the handshake. A cross-site `Origin` is rejected
  (`1008`) to prevent cross-site WebSocket hijacking. Malformed audio (odd length,
  oversized) is reported and skipped.

## 4. Tools with Live: SDK function calling and MCP

- **`tools=[mcp_session]` loses shapes.** The google-genai SDK's automatic MCP
  conversion (`_mcp_utils.mcp_to_gemini_tool`) runs the schema through
  `Schema.from_json_schema`, which drops `additionalProperties`: a `dict[str, str]`
  becomes a shapeless object. **[SDK]** The `cast` argument of `voice_render_dialogue` is
  therefore a list of `{speaker, voice}` objects, and `mcp_tool_schemas` in the verifier
  flags any tool that regresses.
- **Declare with `parameters_json_schema`.** `LiveMcpBridge` passes each MCP tool's JSON
  Schema unchanged and sets `behavior=NON_BLOCKING`, which Gemini 3.8 Live Extended
  Thinking requires for every function.
- **Scheduling.** Results go back with `FunctionResponse.scheduling=WHEN_IDLE` (by
  default), so they do not cut the model off. `tool_call_cancellation` cancels running
  calls.
- **IDLE is not "done" while tools run.** Extended Thinking can say "let me check", report
  `IDLE`, and only speak the result after the WHEN_IDLE response. `LiveMcpBridge.busy`
  stays true until the model has spoken after every result. End a conversation on
  `IDLE and not bridge.busy`.

## 5. ADK

ADK 2.10 already handles the 3.x Live specifics **[SDK]**:

- it sends text to `gemini-3.*-live*` models with `send_realtime_input(text=…)`, because
  those models accept `client_content` only as initial history;
- it forwards `generate_content_config.thinking_config` to the Live connect config;
- it exposes `interaction_status` on events.

What the example adds:

- `RunConfig(speech_config=creator.live_speech_config(profile), streaming_mode=BIDI, …)`
  with session resumption and context compression.
- `McpToolset` over **stdio**, with an explicit environment allow-list: the MCP client
  passes only a minimal default environment, so `GEMINI_API_KEY` must be listed. Or over
  **Streamable HTTP** with `headers={"Authorization": …}`.
- ADK has no toolset-level `behavior` option, so `AsyncMcpToolset` marks the MCP tools
  `NON_BLOCKING` and `WHEN_IDLE` for Extended Thinking. The loop waits until every
  function response has been spoken.
- `tool_filter` limits the agent to read-only tools. Creating or deleting voices stays a
  human action.

## 6. Genkit

- `genkit-plugin-google-genai` 0.9.0 fails to import with `genkit` 0.12.0
  (`ImportError: DocumentPart`). The example does not need a model plugin: the flows call
  `VoiceCreator`, and each Gemini call is a traced step (`ai.run(name=…, fn=…)`).
- Genkit traces record flow inputs and outputs, so **no flow returns a Live token**. Use
  `/v1/live-tokens`.
- `ai.run_main(main())` keeps the reflection server up for `genkit start`.

## 7. Gemini CLI extension

- `gemini-extension.json` declares the stdio server with
  `"env": {"GEMINI_API_KEY": "$GEMINI_API_KEY"}`, so the key is expanded from the user's
  environment and never written into the file. It also sets `"trust": false` (confirm
  each call) and `excludeTools` for the delete tools.
- `GEMINI.md` gives the model the setup order, script format and consent rules.
- For a remote server, use `"httpUrl": "https://…/mcp"` with `"oauth": {"enabled": true}`.
  The server's RFC 9728 metadata lets the CLI discover the authorization server.

## 8. Interactions API `mcp_server` tool

- **[SDK]** `{"type": "mcp_server", "name", "url", "headers", "allowed_tools": [{"tools": [...]}]}`.
  Responses list `mcp_server_tool_call` and `mcp_server_tool_result` steps, and the SDK
  derives `output_text` from the trailing `model_output` step.
- **Google's servers call the URL**, so it must be public HTTPS, and the `headers` go to
  Google. Send a short-lived token for *your* server with the minimum scopes (for
  example `voice:read`), never the Gemini key.

## 9. Identity providers: scope claims

JWTs carry scopes in different claims: `scope` (RFC 9068, space-separated) or `scp`
(Okta; Entra ID, often as a list). `VOICE_CREATOR_OAUTH_SCOPE_CLAIMS` (default
`scope,scp`) lists the claims to read, and granted scopes are the union.

## 10. What still needs your key

Offline, every payload validates against the SDK's own models, and every example runs
against the real packages with faked network endpoints. What only the live service can
answer is covered by `voice-creator verify`:

| Question | Check |
| --- | --- |
| Does Live accept the replicated voice (standard and ET), and return a consent signature? | `live_standard`, `live_extended_thinking` |
| Is the cached consent signature accepted later? | `live_signature_reuse` |
| Do realtime audio, VAD and input transcription work with the cloned voice? | `live_realtime` |
| Does a token with the voice locked in open a session? Is the token size OK with the consent WAV inside? | `ephemeral_token` |
| Does TTS accept a stateless voice key and `speech_metadata` style? | `tts_cloned_voice` (`--replicate`) |
| Does ET call MCP tools asynchronously and speak the result? | `mcp_live_tool_call` |
| Can Gemini reach your deployed server via Interactions? | `interactions_mcp_server` (`--mcp-url`) |

Reports never include keys or tokens, so they can be shared in an issue.
