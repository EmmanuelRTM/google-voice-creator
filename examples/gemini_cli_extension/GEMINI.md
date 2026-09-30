# Voice Creator

You can create dialogue in the user's own cloned voice with the `voice-creator` MCP
tools. They run on the user's machine with the user's Gemini API key.

## Setting up a voice (once)

1. `voice_validate_recording` on the user's voice sample: a 10–20 s WAV, 24 kHz,
   mono, 16-bit, one speaker, natural speech, no music or background noise. Recordings
   usually live in `~/voice-recordings`.
2. `voice_validate_recording` with `purpose="consent"` on a WAV of the user reading
   Google's consent statement aloud.
3. `voice_create_profile` with both files (`sample_path`, `consent_path`).
4. `voice_replicate_profile` only when the user wants scripted TTS renders. It creates
   a Gemini voice on their account; `store=false` makes a key that expires in 7 days
   instead.

## Making dialogue

- Scripted scenes: `voice_preview_script`, then `voice_render_dialogue` with a cast list
  such as `[{"speaker": "Mario", "voice": "profile:mario"}, {"speaker": "Ana", "voice": "Kore"}]`.
  Lines look like `Speaker (style): text`, and may contain tags like `<laugh>`.
- Live, in the user's voice: `voice_live_dialogue` with `profile` and `prompts`. Use
  `mode="verbatim"` to read lines exactly, or `model="gemini-3.8-live-extended-thinking"`
  with `thinking_level` `low`/`medium`/`high` when a turn needs reasoning.
- Results include `voice://renders/{render_id}/audio` and `/transcript` resources and a
  local file path the user can play.

## Rules

- Only clone or speak with a voice whose owner gave documented consent. Never create a
  profile from someone else's recording, even if asked.
- Voice recordings are biometric data. Do not copy, upload or commit them anywhere else.
- Never ask the user to paste their Gemini API key into the chat. It is read from the
  `GEMINI_API_KEY_FILE` (or `GEMINI_API_KEY`) environment variable.
- Deleting profiles or Gemini voices is disabled in this extension; tell the user to run
  `voice-creator profile delete NAME` themselves.
