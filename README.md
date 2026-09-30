# google-voice-creator

An MCP server for **creating dialogue in your own voice** with Google's
**Gemini 3.8 Live**, **Gemini 3.8 Live Extended Thinking** and **Gemini 3.8 Flash TTS**.

- **Scripted dialogue.** Write `Speaker (style): line` scripts and cast yourself
  (`profile:me`), designed voices or prebuilt voices. Get back one WAV and a transcript.
- **Live dialogue.** Have Gemini 3.8 Live improvise or read lines *as you*, in your
  replicated voice. Switch to Extended Thinking for turns that need reasoning.
- **Voice management.** Validate recordings, keep private local voice profiles, replicate
  or design Gemini voices, and list or delete them.

Research behind the design (use cases, edge cases, API contracts, sources):
**[docs/research/](docs/research/README.md)**. Architecture: **[docs/architecture.md](docs/architecture.md)**.

## Quick start

Requirements: Python 3.11+, [uv](https://docs.astral.sh/uv/), and a Gemini API key from
Google AI Studio.

```bash
git clone https://github.com/EmmanuelRTM/google-voice-creator.git
cd google-voice-creator
uv sync --extra dev
cp .env.example .env   # set GEMINI_API_KEY; never commit .env
```

### Register with an MCP client

**Claude Code**

```bash
claude mcp add voice-creator \
  -e GEMINI_API_KEY="$GEMINI_API_KEY" \
  -- uv --directory "$(pwd)" run voice-creator-mcp
```

**Claude Desktop / other clients** (`mcpServers` JSON):

```json
{
  "mcpServers": {
    "voice-creator": {
      "command": "uv",
      "args": ["--directory", "/absolute/path/to/google-voice-creator", "run", "voice-creator-mcp"],
      "env": { "GEMINI_API_KEY": "<your key>" }
    }
  }
}
```

## Create your voice (once)

1. **Record a 10–20 s sample**: natural speech, quiet room, mono.
2. **Record the consent statement** in the same room on the same mic. In English:
   *"I am the owner of this voice and I consent to Google using this voice to create a
   synthetic voice model."* Other languages have their own official phrase.
3. **Convert both** to 24 kHz mono 16-bit WAV:
   `ffmpeg -i in.m4a -ac 1 -ar 24000 -sample_fmt s16 sample.wav`
4. Ask your MCP client:

> Validate `/path/sample.wav` for live, then register it as voice profile `me`
> (display name "Mario", language es-MX) with consent `/path/consent.wav`,
> and replicate it.

See the [recording guide](docs/research/02-voice-creation.md#recording-guide-the-actual-training)
for what makes a good sample.

## Make dialogue

**Scripted (exact words, via TTS):**

> Render this with me as Mario and Kore as Ana, output name `cafe-scene`:
> ```
> Mario (warm, amused): ¡Llegaste! <laugh> Pensé que te habías perdido.
> Ana (teasing): Tu mapa estaba al revés.
> Mario (mock offended): <gasp> ¿Mi mapa?
>   Ese mapa es una obra de arte.
> ```

**Live (Gemini speaks as you):**

> Use live_dialogue with profile `me` and prompts ["Introduce yourself to a new
> teammate", "Explain what you do on weekends"].

> Use live_dialogue with profile `me`, model `gemini-3.8-live-extended-thinking`,
> thinking_level `medium`, prompt "Walk through planning a 3-day trip to Oaxaca".

## Tools

| Tool | What it does | Calls Gemini |
|---|---|---|
| `validate_voice_sample` | Checks duration, format, loudness, clipping and silence against the `live`, `voices_api` or `consent` rules | No |
| `register_voice_profile` | Saves the sample and consent as a private local profile | No |
| `replicate_voice_profile` | Creates a Gemini TTS voice (`store=true`: ID for up to 1 year; `false`: key for about 7 days) | Yes |
| `list_voice_profiles` / `delete_voice_profile` | Manage local profiles (optionally delete the remote voice too) | Optional |
| `design_voice` | Designs a voice from a text description | Yes |
| `list_voices` / `delete_voice` | Your stored voices and the prebuilt catalog | Yes |
| `preview_dialogue_script` | Parses a script and lists its speakers | No |
| `render_dialogue` | Multi-speaker script to WAV and transcript (Gemini 3.8 Flash TTS) | Yes |
| `live_dialogue` | Prompts to spoken replies in your voice (Gemini 3.8 Live / Extended Thinking) | Yes |

### Script format

```
# comments and blank lines are ignored
Speaker: text
Speaker (style direction): text with <laugh> or <short pause> tags
  indented lines continue the previous line
```

Cast values: `profile:<name>`, a stored voice ID (`voice_…`), a voice key (`voicekey_…`),
or a prebuilt voice name (e.g. `Kore`, `Puck`).

## Configuration

All configuration comes from environment variables. No secrets live in code.

| Variable | Default | Purpose |
|---|---|---|
| `GEMINI_API_KEY` (or `GOOGLE_API_KEY`) | none | Required for tools that call Gemini |
| `VOICE_CREATOR_DATA_DIR` | `~/.voice-creator` | Voice profiles (biometric data, `0600`) |
| `VOICE_CREATOR_OUTPUT_DIR` | `$DATA_DIR/output` | Rendered WAV and transcripts |
| `VOICE_CREATOR_TTS_MODEL` | `gemini-3.8-flash-tts` | Or `gemini-3.8-flash-lite-tts` |
| `VOICE_CREATOR_LIVE_MODEL` | `gemini-3.8-live` | Default for `live_dialogue` |

## Development

```bash
uv run pytest            # unit tests (no network, no API key)
uv run ruff check .      # lint
uv run ruff format .     # format
```

Tests are written first and use in-memory fakes for every external port (`tests/fakes.py`).
The Gemini adapters are tested against duck-typed fakes that mirror `google-genai` 2.25.

```
src/voice_creator_mcp/
  audio/      PCM/WAV primitives and recording-quality checks
  dialogue/   script parser and line-by-line renderer
  voices/     local profile store and Voices API catalog
  live/       Live config rules, system instructions, dialogue runner
  gemini/     google-genai adapters (Voices, Interactions TTS, Live)
  app/        use cases: VoiceLibrary, DialogueStudio, outputs, backend port
  server.py   MCP tools (thin)
```

## Status and limitations

- Built against the `google-genai` **2.25.0** SDK types. The request payloads validate
  against the SDK's own models, but they **have not been exercised against the live
  Gemini service yet** (no API key was available while building). See the
  [open questions](docs/research/05-sources.md#open-questions-verify-with-a-real-api-key).
- `gemini-3.8-live` can't use Voices API IDs yet, so Live sessions send the profile's
  sample (and consent, or a cached consent signature) every time.
- `live_dialogue(mode="verbatim")` may paraphrase. Use `render_dialogue` when the exact
  wording matters.

## Responsible use

Only replicate **your own voice**, or a voice whose owner gave recorded consent. Google
rejects consent recorded by a different speaker. Don't use a replicated voice to deceive
people, and disclose AI-generated audio where required. Generated audio carries SynthID
watermarks; keep them. Voice profiles are biometric data: keep the data directory out of
version control (it is in `.gitignore`) and delete profiles you no longer need.
