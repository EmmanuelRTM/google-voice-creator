# 04: Edge cases

The **MCP** column says how this repo's server deals with each case:
**Enforced** (rejected with a clear error), **Handled** (worked around automatically),
**Warned** (returned as a warning), **Doc** (documented only, so you act on it).

## A. Recordings

| # | Edge case | Consequence | MCP |
|---|---|---|---|
| A1 | Sample shorter than 10 s or longer than 20 s (Live) | Request rejected | Enforced (`validate_voice_sample`, `register_voice_profile`) |
| A2 | Sample isn't 24 kHz, or is stereo, 8-bit, 24-bit or float | Live Developer API accepts only 16-bit 24 kHz WAV | Enforced, with an `ffmpeg` hint |
| A3 | MP3, M4A or Opus file | Not WAV | Enforced; the error explains how to convert |
| A4 | Clipping (peaks at 0 dBFS) | Distortion gets cloned | Warned |
| A5 | Very quiet recording (below -40 dBFS RMS) | Noisy, thin clone | Warned |
| A6 | Mostly silence (over 40%) | Too little speech to model the timbre | Warned |
| A7 | Background music, TV, other people or room echo | Artifacts are cloned; consent verification may fail | Doc (recording guide) |
| A8 | Sample and consent recorded on different mics or rooms | Speaker verification may fail | Doc |
| A9 | Emotional extremes in the sample (shouting, crying) | Every output inherits the mood | Doc |
| A10 | WAV with extra metadata chunks | Some servers reject non-canonical files | Handled (re-encoded to canonical PCM WAV on registration) |
| A11 | Sample in one language, output in another | Accent carry-over, occasional drift | Doc; set `language_code` |

## B. Consent, policy and privacy

| # | Edge case | Consequence | MCP |
|---|---|---|---|
| B1 | Consent read by someone other than the sample speaker | Rejected by Google | Doc (the server can't detect it) |
| B2 | Minor's voice | Not allowed; consent must come from an adult speaker | Doc |
| B3 | Public figure, deceased person, or a voice you don't own | Policy and legal violation (right of publicity, fraud) | Doc; the server instructions require documented consent |
| B4 | Consent statement not in a supported language | Rejected; 25 phrases are supported | Doc |
| B5 | Undisclosed AI voice in calls | Deceptive, and illegal in some places | The Live persona prompt never tells the model to deny being AI |
| B6 | Biometric data at rest (GDPR, BIPA and similar) | Liability | Handled: profiles are `0700`/`0600`, `.gitignore` excludes `*.wav` and data dirs |
| B7 | Voice keys and consent signatures leaking through tool output | Credential-like values exposed | Handled: masked in every tool response |
| B8 | Right to be forgotten | Must delete both remote and local copies | `delete_voice_profile(delete_remote_voice=true)` |
| B9 | Provenance | Output carries SynthID, replication adds C2PA | Doc (don't strip metadata when you publish) |

## C. Voices API

| # | Edge case | Consequence | MCP |
|---|---|---|---|
| C1 | More than 200 active stored voices | `RESOURCE_EXHAUSTED` | Doc; delete unused voices with `delete_voice` |
| C2 | `prompted` voice with `store=false` | `INVALID_ARGUMENT` | Handled (design always stores) |
| C3 | Stateless key older than about 7 days | Synthesis fails | Enforced: the expiry is checked before use, with instructions to re-replicate |
| C4 | Stored voice older than 1 year | Voice disappears | Enforced (same `expire_time` check) |
| C5 | Using a Voices API ID with `gemini-3.8-live` | Not supported yet | Handled: Live always uses the profile's inline sample |
| C6 | Get or delete on a prebuilt voice | Not allowed | Doc |
| C7 | `voices/voice_x` vs `voice_x`, path traversal in IDs | Wrong URL | Handled (`normalize_voice_id`) |
| C8 | Response missing `id` or `key` | Silent breakage | Enforced (`VoiceApiError`) |
| C9 | Replicating the same profile twice | A second stored voice counts against the quota | Doc (the profile keeps only the newest reference) |

## D. Text-to-speech generation

| # | Edge case | Consequence | MCP |
|---|---|---|---|
| D1 | Style written into the text ("say warmly: …") | 3.8 reads it aloud | Handled: `(style)` is sent as a `speech_metadata` annotation |
| D2 | A mistyped tag (`<laugh`) or unknown tag | Read aloud literally | Doc (`preview_dialogue_script` shows the parsed text) |
| D3 | Tags in transcripts, captions or other LLMs | Noise | Handled: transcripts strip `<tags>` |
| D4 | Tone or voice switches in long-form output (reported after phrase changes) | Inconsistent voice | Handled: line-by-line synthesis; keep lines short |
| D5 | More than two speakers, or custom voices in one request | Multi-speaker needs exactly two speakers | Handled: line-by-line rendering allows any cast |
| D6 | Speaker missing from the cast | Nothing to synthesize | Enforced before any API call (no cost) |
| D7 | Cast key casing ("mario" vs "Mario") | Lookup miss | Handled (case-insensitive) |
| D8 | Mixed sample rates between lines | Can't be stitched | Enforced (`AudioFormatError`) |
| D9 | Names, acronyms, numbers and dates | Mispronounced | Doc: spell phonetically in the text |
| D10 | A colon in text ("10:30") | Parser confusion | Handled: only the first `Speaker:` prefix is parsed |
| D11 | Price doubling on 2027-01-01 | Budget | Doc |

## E. Live sessions (3.8 Live and Extended Thinking)

| # | Edge case | Consequence | MCP |
|---|---|---|---|
| E1 | `thinking_level` on `gemini-3.8-live` | Error | Enforced before connecting |
| E2 | `minimal` on Extended Thinking | Error | Enforced |
| E3 | Blocking function declarations on Extended Thinking | Hard error | Doc (this MCP server declares no Live tools) |
| E4 | Treating `turn_complete` as done on Extended Thinking | Audio cut off mid-task | Handled: waits until `interaction_status != IN_PROGRESS` |
| E5 | A stream that never reaches IDLE | Hang | Handled: bounded passes plus a per-turn timeout |
| E6 | Consent signature expired or invalid | Setup fails | Handled: retries once with the consent recording and refreshes the cached signature |
| E7 | Consent audio or signature in Enterprise (Vertex) mode | SDK `ValueError` | Doc (Developer API only) |
| E8 | Client streams digital-zero silence and then `audioStreamEnd` | 3.8 never ends the turn | Doc; this server sends text turns, not microphone audio |
| E9 | Microphone at 44.1 or 48 kHz sent as 16 kHz | Garbled input | Doc (resample input to 16 kHz) |
| E10 | Barge-in while audio is buffered | The agent talks over the user | Warned (`interrupted` flag); real-time clients must flush playback |
| E11 | Echo (speaker to mic) | The model hears itself and interrupts | Doc: headphones or AEC |
| E12 | Session over 15 min (audio) or 2 min (audio+video) | Disconnect | Doc: use `context_window_compression` and `session_resumption`; GoAway is reported as a warning |
| E13 | Resuming while `resumable=false` (mid-generation or mid-tool) | Data loss | Doc |
| E14 | Using `send_client_content` mid-conversation on 3.x | Unexpected results | Handled: text is sent with `send_realtime_input` |
| E15 | Proactive audio or safety filtering leaves a turn silent | Empty turn | Warned; errors only if the whole session has no audio |
| E16 | Transcripts arrive out of order or merged with the next turn | Wrong captions | Doc (transcription is independent of audio ordering) |
| E17 | Verbatim mode paraphrases or answers instead of reading | Wrong words | Doc: use `render_dialogue` for exact wording |
| E18 | Mid-sentence language switching | Unexpected language | Doc: set `language_code` and state the language in the prompt |
| E19 | The voice sample is re-sent every session (about 1 MB) | Bandwidth and latency | Doc; the cached signature at least avoids re-sending consent |
| E20 | Shipping the API key to a browser or mobile client | Key theft | Doc: use ephemeral tokens (`v1alpha`) for client-side apps |
| E21 | Latency above about 700 ms P50 | Users talk over the agent | Doc |

## F. Output, operations and security

| # | Edge case | Consequence | MCP |
|---|---|---|---|
| F1 | Output name with `../` or odd characters | Path traversal | Handled (sanitized) |
| F2 | Rendering twice with the same name | Overwrites a take | Handled (`take-2.wav`, …) |
| F3 | Profile name with `../`, uppercase or spaces | Path traversal or collisions | Enforced (`[a-z0-9][a-z0-9_-]{0,63}`) |
| F4 | Missing `GEMINI_API_KEY` | Every Gemini call fails | Handled: offline tools still work; Gemini tools give a clear error |
| F5 | API key in `repr` or logs | Leak | Handled (`repr=False`) |
| F6 | Blocking HTTP calls inside the MCP event loop | A frozen server | Handled: worker threads |
| F7 | Tool exceptions hidden by the MCP SDK | Opaque failures | Handled: domain errors become `ToolError` with the message |
