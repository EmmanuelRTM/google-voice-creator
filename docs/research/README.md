# Research: your voice with Gemini 3.8 Live and 3.8 Live Extended Thinking

Research date: **2026-09-30** (15 days after the 2026-09-15 launch).

## Executive summary

1. **You don't "train" a voice.** Gemini replicates a voice zero-shot from a short
   reference clip plus a spoken consent statement. No fine-tuning job runs and you
   can't upload a dataset. "Training" in practice means **curating a clean
   10–20 s reference recording** ([02-voice-creation.md](02-voice-creation.md)).
2. **There are two separate paths to "my voice":**
   - **Gemini 3.8 Live / Live Extended Thinking**, a real-time audio-to-audio
     conversation. Your voice is sent **inline on every session**
     (`speech_config.voice_config.replicated_voice_config`). Voice IDs from the
     Voices API **can't be used** with `gemini-3.8-live` yet.
   - **Gemini 3.8 Flash / Flash-Lite TTS**, which turns scripted text into speech. You
     create a voice once with the **Voices API** (`POST /v1beta/voices`) and reuse
     its ID (`voice_…`, stored up to 1 year) or key (`voicekey_…`, about 7 days).
3. **For "dialogue in my voice", use both.** Use TTS to get your exact words with
   line-by-line direction (scripts, podcasts, two-person scenes). Use Live when the
   model should *improvise* as you, and Extended Thinking when it needs to reason or
   call tools while it keeps talking. This repo wraps both in a Python library and CLI,
   an MCP server, and Google's official integration paths (ADK, Genkit, Gemini CLI,
   ephemeral tokens, server-to-server realtime)
   ([../../README.md](../../README.md), [07-google-integration.md](07-google-integration.md)).
4. **Extended Thinking changes the session contract.** `turn_complete` no longer means
   the model is idle. Wait for `interaction_status == IDLE`. `thinking_level` accepts only
   `low|medium|high` (`minimal` errors), all tools must be `NON_BLOCKING`, and the
   standard `gemini-3.8-live` model rejects `thinking_level` entirely.
5. **Consent is enforced technically.** The consent recording must be the same adult
   speaker, or the request is rejected. The server returns a consent signature that
   can replace the recording in later Live sessions. Output carries SynthID
   watermarks, and replication adds C2PA credentials.

## Contents

| File | What it covers |
|---|---|
| [01-models.md](01-models.md) | Model IDs, specs, differences, pricing, session contract, known issues |
| [02-voice-creation.md](02-voice-creation.md) | Replication vs design vs prebuilt, the Voices API, Live inline voices, consent, recording guide |
| [03-use-cases.md](03-use-cases.md) | Use cases for your voice, mapped to model and MCP tool |
| [04-edge-cases.md](04-edge-cases.md) | Edge cases by category, with how the MCP server handles each |
| [05-sources.md](05-sources.md) | Sources, evidence levels and open questions |
| [06-mcp-and-auth.md](06-mcp-and-auth.md) | MCP 2026-07-28 and OAuth best practices, threat model, scopes, Gemini credentials |
| [07-google-integration.md](07-google-integration.md) | Google's integration paths (SDK, ephemeral tokens, server-to-server, ADK, Genkit, Gemini CLI, Interactions `mcp_server`) and how each is implemented and verified |

## How this research was done (read before relying on it)

The network policy of the environment this was written in **blocked direct access** to
`ai.google.dev`, `blog.google`, `docs.cloud.google.com` and most news sites. Evidence
therefore comes from three places. Each claim is tagged with its evidence level:

- **[SDK]**: verified by reading the source of the official `google-genai` Python
  SDK **v2.25.0** (PyPI), which includes the generated types for the Live API,
  the Voices API and the Interactions API. This is the strongest evidence here.
- **[Docs]**: official Google documentation or blog text, as quoted in web-search
  result summaries (not read in full).
- **[3P]**: third-party articles, community forums and GitHub issues.

Claims that couldn't be confirmed are listed as open questions in
[05-sources.md](05-sources.md). Verify pricing and quotas in AI Studio before you
budget.
