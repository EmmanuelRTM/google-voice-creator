# Integration examples

Each example follows one of Google's official integration paths for Gemini Live, the
Interactions API and MCP. All of them read the Gemini key from `GEMINI_API_KEY` and
take no secrets as command-line flags.

| You are building… | Path | Example |
| --- | --- | --- |
| An agent with Google's Agent Development Kit | ADK `Runner.run_live` + `RunConfig.speech_config` + `McpToolset` | [`adk_voice_agent/agent.py`](adk_voice_agent/agent.py) |
| Traced, deployable AI workflows | Genkit flows around `VoiceCreator` | [`genkit_flow/flows.py`](genkit_flow/flows.py) |
| A terminal assistant | Gemini CLI extension (MCP over stdio) | [`gemini_cli_extension/`](gemini_cli_extension/) |
| A backend that lets Gemini call your deployed server | Interactions API `mcp_server` tool | [`interactions_mcp_server.py`](interactions_mcp_server.py) |
| Your own Live loop with the google-genai SDK | Live API + `LiveMcpBridge` (async function calling) | [`live_mcp_bridge.py`](live_mcp_bridge.py) |
| Telephony, mobile backends, server-side audio | Server-to-server: `/v1/live` WebSocket relay | [`realtime_ws_client.py`](realtime_ws_client.py) |
| A web app with a microphone | Client-to-server: ephemeral token from `/v1/live-tokens` | [`browser_ephemeral_token/`](browser_ephemeral_token/) |

Before trying any of them, run the verifier with your key and recordings. It checks
every path end to end and tells you what Gemini accepted:

```sh
uv run voice-creator verify --sample me.wav --consent consent.wav --replicate --report report.md
uv run python scripts/verify_gemini.py --profile verify   # adds the MCP checks
```

## Choosing between the realtime paths

- **Ephemeral tokens (client-to-server)** have the lowest latency: the browser or app
  talks to Gemini directly. The token is single-use, expires in minutes, and has the
  cloned voice, model and instructions locked in, so a user cannot change the voice.
  The Gemini API key never leaves your server.
- **The WebSocket relay (server-to-server)** keeps every byte on your infrastructure. It
  suits phone lines, devices that cannot run the Gemini SDK, and deployments that must
  log or moderate the audio. The relay resumes Gemini sessions transparently on
  `GoAway` and compresses context for long calls.

## Running them

```sh
uv sync --all-extras                        # this repo, with the MCP server
uv pip install "google-adk[mcp]>=2.10"      # adk_voice_agent
uv pip install "genkit>=0.12"               # genkit_flow
uv pip install websockets                   # realtime_ws_client
gemini extensions install ./examples/gemini_cli_extension
cd examples/browser_ephemeral_token && npm install && npm run dev
```

The remote examples read `VOICE_CREATOR_MCP_URL` and `VOICE_CREATOR_MCP_BEARER`: an
access token for *your* Voice Creator server, never the Gemini key. With the
Interactions API that token travels to Google, so give it only the scopes the task
needs and a short lifetime.

## Consent

Only clone and speak with a voice whose owner gave documented consent. The consent
recording is required to create a profile, and it is sent to Gemini (or replaced by the
consent signature Gemini returns) on every Live session.
