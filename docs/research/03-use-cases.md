# 03: Use cases for your voice

Each use case is mapped to the model family that fits and to the MCP tool in this repo.
**TTS** means Gemini 3.8 Flash / Flash-Lite TTS with a Voices API voice.
**Live** means `gemini-3.8-live`. **Live-ET** means `gemini-3.8-live-extended-thinking`.

## Creating dialogue in your voice (the core goal)

| # | Use case | Best fit | MCP tool | Notes |
|---|---|---|---|---|
| 1 | Scripted dialogue where you play one role and another voice plays the rest | TTS | `render_dialogue` with `{"You": "profile:me", "Ana": "Kore"}` | Exact words, per-line `(style)` and `<tags>` |
| 2 | You talking with yourself (debate, inner monologue, two moods) | TTS | `render_dialogue` with both speakers on `profile:me` and different styles | Style carries the contrast, the voice stays the same |
| 3 | Podcast or co-host episode with a designed partner | TTS | `design_voice`, then `render_dialogue` | A designed voice needs `store=true` |
| 4 | Improvised replies "as you" to prompts | Live | `live_dialogue(mode="improvise")` | Natural phrasing, but not word-exact |
| 5 | Reading your lines through the Live engine for a live feel | Live | `live_dialogue(mode="verbatim")` | May paraphrase; use TTS when wording matters |
| 6 | Dialogue that needs reasoning (plan a trip, explain a proof) narrated in your voice | Live-ET | `live_dialogue(model="gemini-3.8-live-extended-thinking", thinking_level="medium")` | Speaks fillers while it thinks, so wait for IDLE |
| 7 | Multilingual versions of the same scene | TTS or Live | `render_dialogue(language="fr-FR")` or `live_dialogue(language_code=…)` | Expect some accent carry-over |
| 8 | Audiobook or long narration in your voice | TTS | `render_dialogue` with one speaker and many lines | Line-by-line rendering limits voice drift |

## Broader use cases for a replicated voice with 3.8 Live

| Use case | Model | Why this model | Watch out for |
|---|---|---|---|
| **Voice banking / accessibility**: preserve the voice of someone about to lose it (ALS, surgery) | TTS + Live | Replicate while the voice is healthy, then speak through TTS or Live | Record early and store samples offline; a stored voice expires after about 1 year, so re-create it |
| Personal voice assistant or voicemail that answers as you | Live | Low latency, async tools | Disclose it's AI to callers; many jurisdictions require it |
| Customer-support or receptionist agent in a founder's voice | Live-ET | Tool chains (CRM, calendar) run while it talks | All tools must be `NON_BLOCKING`, and plan for 15-minute session limits |
| Tutoring or coaching in a teacher's voice | Live-ET | Multi-step reasoning, 97 languages | Thinking tokens are billed as output |
| Interview or presentation rehearsal partner | Live | Natural turn-taking and barge-in | Echo: use headphones or AEC |
| Language-learning partner in your own voice (hear yourself fluent) | Live or TTS | Language switching mid-conversation | Accent leakage between languages |
| Localization or dubbing of your videos | TTS | Exact scripts, timing control | Lip-sync isn't handled; Live Avatar is an enterprise option |
| Game, animation or character prototyping | TTS (designed voices) | Voice design from text, vocal tags | A 200 stored-voice quota per project |
| Enterprise avatar with your voice | Live + Live Avatar | Lip-synced video, GA 2026-09-24 | Enterprise-only; SynthID on video |
| Search-grounded briefings read in your voice | Live-ET | Search grounding plus reasoning | Cite sources in text, since audio has no links |

## What 3.8 Live is *not* good for

- **Word-exact scripts.** It is a conversational model, so use TTS.
- **Long uninterrupted narration.** Session limits and turn-taking get in the way.
  Render with TTS.
- **Other people's voices** without their recorded consent. The API rejects consent
  from a different speaker, and doing it anyway is a legal and ethical problem
  ([04](04-edge-cases.md)).
