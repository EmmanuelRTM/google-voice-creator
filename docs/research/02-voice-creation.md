# 02: Creating ("training") your voice

## There is no training job

Gemini doesn't fine-tune a model on your voice. It **replicates** your voice
zero-shot from a short reference recording at request time [Docs]. For the Live API,
"there's nothing to upload or train in advance" [Docs]. What you control is:

1. **The reference recording.** Its quality sets the ceiling on how close the clone sounds.
2. **The consent recording.** It is mandatory and is checked against the reference speaker.
3. **Where the voice lives.** It is sent inline per Live session, or stored once
   through the Voices API for TTS.

If you truly need a trained custom voice for enterprise TTS, Google Cloud also offers
**Chirp 3: Instant Custom Voice** (Cloud Text-to-Speech), a separate product outside
this repo's scope [Docs].

## The three voice types (Voices API, `VoiceType`) [SDK]

| Type | How it's made | Stored? | Reference you synthesize with |
|---|---|---|---|
| `prebuilt` | Google catalog: 30 studio voices, plus an extended library of 2,000+ voices [3P] | Always | Speaker name, e.g. `Kore`, `Puck` |
| `prompted` (voice design) | From a natural-language description | **Must** use `store=true` | `voice_…` ID |
| `replicated` (voice cloning) | From your `source_audio` plus `consent_audio` | `store=true` or `false` | `voice_…` ID, or `voicekey_…` |

The voice metadata you can set and filter on [SDK]: `display_name`, `description`,
`language_code` (BCP-47), `region_code`, `accent`, `gender`, `persona`, `context`,
`pitch` (`low|medium|high`), `model`. Voices you create "can be synthesized across any
supported TTS synthesis model" [SDK].

## Path A: Voices API, then Gemini 3.8 Flash TTS (scripted dialogue)

`POST /v1beta/voices` (`client.voices.create(voice=…, store=…)`) [SDK]:

```jsonc
{
  "voice": {
    "type": "replicated",
    "display_name": "Mario",
    "language_code": "es-MX",
    "replicated": {
      "source_audio":  { "data": "<base64 wav>", "mime_type": "audio/wav" },
      "consent_audio": { "data": "<base64 wav>", "mime_type": "audio/wav" }
    }
  },
  "store": true
}
```

- `store=true` returns `id` (`voice_abc123…`), managed with get, list and delete
  (`voices/{id}`). It counts against a **project quota of 200 active stored voices**,
  and going over returns `RESOURCE_EXHAUSTED` [SDK + 3P]. Retention is **one year**
  (`expire_time`) [3P].
- `store=false` (the API default) returns `key` (`voicekey_…`) that you store yourself.
  It lives about **7 days** [3P] and suits workloads that want no server-side biometric
  profile [3P]. Metadata isn't persisted.
- You can't get or delete prebuilt voices. `list` returns your stored voices newest-first,
  then the catalog [SDK].
- To synthesize, pass the ID or key as the voice (`SpeechConfig.voice_config.voice`, or
  `speech_config: [{voice: …}]` in the Interactions API) [SDK].

Gemini 3.8 Flash TTS (`gemini-3.8-flash-tts`, or the cheaper `gemini-3.8-flash-lite-tts`)
[3P] treats **the input text as the transcript** [3P]:

- **Sustained direction** ("whispered, urgent", "bored and monotone") goes into
  **speech metadata**, not the text. In the SDK this is a `speech_metadata` annotation
  with a `style` (and optional `speaker`) on the text content [SDK].
- **Momentary effects** go inline as angle-bracket tags [3P]: `<laugh>`, `<chuckle>`,
  `<giggle>`, `<sigh>`, `<breath>`, `<heavy breath>`, `<exhales>`, `<gasp>`, `<cough>`,
  `<sneeze>`, `<throat-clearing>`, `<yawn>`, `<sob>`, `<cry>`, `<whimper>`, `<groan>`,
  `<moan>`, `<growl>`, `<grunt>`, `<grr>`, `<hiss>`, `<pant>`, `<phew>`, `<pff>`,
  `<tsk>`, `<snicker>`, `<snort>`, `<cackle>`, `<cheer>`, `<shout>`, `<scream>`,
  `<shriek>`, `<argh>`, `<whispers>`, `<short pause>`, `<long pause>`.
- **Two-speaker scenes:** single-request multi-speaker TTS takes **exactly two**
  speakers [SDK]. Community reports say designed and replicated voices are generated
  turn by turn and joined [3P]. The MCP server always renders line by line, which
  works for any number of speakers and voice types.
- Over 100 languages and dialects [Docs].

## Path B: Live API inline voice (improvised, real-time dialogue)

Per session, in `LiveConnectConfig.speech_config.voice_config.replicated_voice_config`
[SDK]:

| Field | Meaning |
|---|---|
| `mime_type` | Gemini Developer API: **only `audio/wav`**, 16-bit signed LE, **24 kHz** [SDK] |
| `voice_sample_audio` | The reference clip. Duration **10–20 s** [Docs] |
| `consent_audio` | The consent recording, same format [SDK]. **Developer API only** [SDK] |
| `voice_consent_signature.signature` | Signature from an earlier session's `setup_complete`. Used **instead of** `consent_audio` to cut latency, and fails if invalid or expired [SDK]. **Developer API only** [SDK] |

- In the Enterprise Agent Platform (formerly Vertex AI), the sample is described as
  PCM s16le at a variable rate, e.g. `audio/pcm;rate=24000` [Docs]. The SDK rejects
  `consent_audio` and `voice_consent_signature` in that mode [SDK].
- The **voice sample is re-sent on every session**. A 20 s, 24 kHz mono WAV is about
  960 KB before base64.
- **Voices API IDs aren't accepted by `gemini-3.8-live` yet** [3P: Google AI Developers
  Forum]. Keep the local recordings; the MCP server stores them as a "voice profile".

## Consent requirements

- A **separate consent recording from the same adult speaker** is required. Mismatched
  voices are rejected [Docs + 3P].
- English statement: *"I am the owner of this voice and I consent to Google using this
  voice to create a synthetic voice model."* **25 consent phrases** cover the other
  supported languages [3P]. Read the one for your language verbatim.
- Output carries **SynthID** watermarks, and voice replication adds **C2PA** content
  credentials [3P].

## Recording guide (the actual "training")

| Do | Why |
|---|---|
| 10–20 s of natural, continuous speech (10–30 s is fine for TTS) | Live enforces 10–20 s, and more varied speech gives a better timbre estimate [Docs + 3P] |
| Mono, 16-bit, **24 kHz** WAV | The Developer API format for Live, and the recommended input for TTS [SDK + 3P] |
| Quiet room, little echo, no music or other voices | Noise and reverb get cloned too [3P] |
| **Same mic and room** for the sample and consent | Helps the speaker-verification check pass [3P] |
| Varied sentences, questions, and a neutral-to-warm tone | Emotional extremes bleed into every output [3P] |
| Speak in the language you'll generate most | Cross-language output can carry your accent, or drift (see [04](04-edge-cases.md)) |
| Peaks around -6 dBFS, no clipping; trim long silences | The MCP `voice_validate_recording` tool checks these |

Convert anything else first:

```bash
ffmpeg -i input.m4a -ac 1 -ar 24000 -sample_fmt s16 sample.wav
```
