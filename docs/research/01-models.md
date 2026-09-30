# 01: Gemini 3.8 Live and 3.8 Live Extended Thinking

## Identity and availability

| | Gemini 3.8 Live | Gemini 3.8 Live Extended Thinking |
|---|---|---|
| Model ID | `gemini-3.8-live` [Docs] | `gemini-3.8-live-extended-thinking` [Docs] |
| Released | 2026-09-15, Gemini API + Google AI Studio [Docs] | same [Docs] |
| Modality | Native audio-to-audio over the Live API (WebSocket) [Docs] | same, plus background reasoning [Docs] |
| Context | 131,072 input / 65,536 output tokens [Docs] | same [Docs] |
| Thinking | **Not supported**: `thinking_level` must be omitted [3P, docs-derived] | `low`, `medium`, `high`; **`minimal` errors** [3P, docs-derived] |
| Function calling | Async (`NON_BLOCKING`) is the default [3P] | **Async only**; a blocking declaration is a hard error [3P] |
| Grounding | Search grounding supported [Docs] | Search grounding supported [Docs] |
| Languages | 97, switching mid-conversation [3P] | same [3P] |

Consumer surfaces: 3.8 Live powers Gemini Live, Gmail and Keep voice features [3P].
Enterprise: **Gemini 3.8 Live with Live Avatar** went GA on 2026-09-24 with lip-synced
video and SynthID on both audio and video [3P]. The SDK has an `avatar_config` for it [SDK].
Google shipped **Gemini 3.5 Transcribe** alongside it for speech-to-text pipelines [3P].

## What "Extended Thinking" changes

Extended Thinking **reasons and speaks at the same time**. It can say "let me check that"
and then narrate its progress while a multi-step task or tool call runs in the
background [Docs]. That changes the protocol a client has to follow:

- **`turn_complete` no longer means idle.** `LiveServerContent.interaction_status` is
  `IN_PROGRESS` while it is still reasoning or running tools, and `IDLE` when everything
  is done. It is "always sent alongside `turn_complete`" [SDK]. `REQUIRES_ACTION` is
  deprecated in favor of `IDLE` [SDK].
- The SDK's `AsyncSession.receive()` already ends a turn on `IDLE` when a status is
  present, and on `turn_complete` otherwise (`_is_interaction_complete`) [SDK].
- The model can emit **conversational fillers** while it reasons, so keep your UI in a
  "thinking" state until `IDLE` [3P].
- Tools: set `behavior: NON_BLOCKING` on every function declaration [3P]. Tool responses
  take `scheduling` (`SILENT`, `WHEN_IDLE` (default), `INTERRUPT`) and `will_continue`,
  which lets a function stream several results [SDK].
- A client message with `turn_complete=true` **interrupts** ongoing generation [3P].

When to pick which:

| Need | Pick |
|---|---|
| Lowest latency, chit-chat, reading lines, persona improvisation | `gemini-3.8-live` |
| Multi-step reasoning, planning, tool chains, tutoring through hard problems | `gemini-3.8-live-extended-thinking` |
| Exact words, studio quality, long-form narration | Neither: use Gemini 3.8 Flash TTS ([02](02-voice-creation.md)) |

## Pricing (verify before budgeting)

| Item | Price | Evidence |
|---|---|---|
| 3.8 Live audio input | $3.00 / 1M tokens (about $0.005/min) | [3P] |
| 3.8 Live audio output | $12.00 / 1M tokens (about $0.018/min); **output includes thinking tokens** | [3P] |
| Rough conversation cost | about $1.38 per hour | [3P] |
| Extended Thinking | about $3.50 per hour of input audio, measured on a benchmark subset | [3P] |
| 3.8 Flash TTS | $0.50 / 1M text in, $9 / 1M audio out, **doubling on 2027-01-01** | [3P] |
| 3.8 Flash-Lite TTS | $0.50 / 1M text in, $6 / 1M audio out, doubling on 2027-01-01 | [3P] |

Quality: Extended Thinking ranks #1 on Artificial Analysis' Speech-to-Speech Quality
Index at 82.6 [3P].

## Session contract (Live API, google-genai 2.25)

Config fields on `LiveConnectConfig` [SDK]: `response_modalities`, `speech_config`,
`thinking_config`, `enable_affective_dialog`, `system_instruction`, `tools`,
`session_resumption`, `input_audio_transcription`, `output_audio_transcription`,
`realtime_input_config` (VAD), `context_window_compression`,
`proactivity.proactive_audio`, `explicit_vad_signal`,
`history_config.initial_history_in_client_content`, `avatar_config`, `safety_settings`,
`translation_config`.

Server message fields [SDK]: `setup_complete` (with `session_id` and
**`voice_consent_signature`**), `server_content` (`model_turn`, `turn_complete`,
`interrupted`, `generation_complete`, `input_transcription`,
`interim_input_transcription`, `output_transcription`, `waiting_for_input`,
`turn_complete_reason`, `interaction_status`), `tool_call`, `tool_call_cancellation`,
`usage_metadata`, `go_away`, `session_resumption_update` (`new_handle`, `resumable`,
`last_consumed_client_message_index`), `voice_activity`.

Practical limits and formats:

- **Session length:** about 15 min for audio-only and 2 min for audio+video without
  session management [3P]. Extend them with `context_window_compression` plus
  `session_resumption`, and reconnect on `go_away` [SDK].
- **Resumption gaps:** `resumable=false` while the model is generating or running tools,
  and resuming then loses data [SDK].
- **Audio in:** 16 kHz 16-bit PCM. Sending 44.1 kHz without resampling produces garbled
  or unrecognized speech [3P].
- **Audio out:** 24 kHz 16-bit PCM (`audio/pcm;rate=24000`) [SDK/3P].
- **Text turns:** on 3.x Live models, send text with `send_realtime_input(text=…)`.
  `send_client_content` is for seeding initial history (with
  `history_config.initial_history_in_client_content`), and mixing the two is "not
  recommended" [SDK].

## Known issues at launch

- **Zero-silence clients get no reply.** The 3.8 server VAD needs trailing audio to
  detect end of speech, and unlike 3.1 it **doesn't treat `audioStreamEnd` as end of
  turn**. A client that stops streaming, or streams digital zeros, can wait forever. The
  user's transcript then arrives late, merged with the next utterance [3P: openclaw #152830].
- **Missing user transcripts over relays.** With Extended Thinking over some gateway
  relays, final user transcripts are never emitted [3P: openclaw #152646].
- **Barge-in.** Clients must flush buffered playback on `interrupted`, or the assistant
  talks over the user [3P].
- **Latency budget.** Above about 700 ms P50 from end of utterance to first audio, users
  start talking over the agent [3P].
- **Instability in preview paths.** Earlier native-audio previews showed intermittent
  non-responses, mid-turn disconnects and tool-call instability [3P]. Build in reconnects.
