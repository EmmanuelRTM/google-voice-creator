# 05: Sources, evidence and open questions

## Primary: official SDK source (read in full)

- `google-genai` **2.25.0** on PyPI: `google/genai/types.py` (`ReplicatedVoiceConfig`,
  `VoiceConsentSignature`, `VoiceConfig.voice`, `SpeechConfig`, `MultiSpeakerVoiceConfig`,
  `ThinkingLevel`, `InteractionStatus`, `LiveConnectConfig`, `LiveServerSetupComplete`,
  `LiveServerContent`, `LiveServerSessionResumptionUpdate`, `FunctionResponseScheduling`,
  `Behavior`, `ProactivityConfig`, `HistoryConfig`); `google/genai/live.py`
  (`AsyncSession.receive`, `_is_interaction_complete`, `send_realtime_input`,
  `send_client_content`); `google/genai/_live_converters.py` (Developer API vs
  Enterprise restrictions); `google/genai/_gaos/voices.py` and `_gaos/types/voices/*`
  (Voices API); `_gaos/types/interactions/*` (`SpeechAnnotation`, `SpeechConfig`,
  `AudioContent`, `AudioResponseFormat`).

## Official documentation and announcements (from search summaries)

- [Gemini 3.8 Live Extended Thinking, model card](https://ai.google.dev/gemini-api/docs/models/gemini-3.8-live-extended-thinking)
- [Gemini 3.8 Live, model card](https://ai.google.dev/gemini-api/docs/models/gemini-3.8-live)
- [Introducing Gemini 3.8 Live and 3.8 Live Extended Thinking](https://blog.google/innovation-and-ai/models-and-research/gemini-models/gemini-3-8-live-gemini-3-8-live-extended-thinking/)
- [Thinking in the Live API](https://ai.google.dev/gemini-api/docs/live-api/thinking)
- [Live API capabilities guide](https://ai.google.dev/gemini-api/docs/live-api/capabilities)
- [Developer's guide to Gemini 3.8 Live (Enterprise Agent Platform)](https://docs.cloud.google.com/gemini-enterprise-agent-platform/models/guides/gemini-3-8-live)
- [Configure language and voice (Live API)](https://docs.cloud.google.com/gemini-enterprise-agent-platform/models/live-api/configure-language-voice)
- [Best practices with Gemini Live API](https://docs.cloud.google.com/gemini-enterprise-agent-platform/models/live-api/best-practices)
- [Voice replication](https://ai.google.dev/gemini-api/docs/voice-replication)
- [Voice design](https://ai.google.dev/gemini-api/docs/voice-design)
- [Gemini Voices API reference](https://ai.google.dev/api/voices)
- [Gemini 3.8 Flash TTS, model card](https://ai.google.dev/gemini-api/docs/models/gemini-3.8-flash-tts)
- [Gemini 3.8 Flash TTS and Flash-Lite TTS (blog)](https://blog.google/innovation-and-ai/models-and-research/gemini-models/gemini-3-8-text-to-speech/)
- [Gemini 3.8 Flash TTS developer guide (AI Studio)](https://aistudio.google.com/learn/gemini-3-8-flash-tts-developer-guide)
- [Introducing Gemini 3.8 Live with Live Avatar](https://blog.google/innovation-and-ai/models-and-research/gemini-models/gemini-3-8-live-with-live-avatar/)
- [Build real-time voice applications with Gemini 3.8 Live and 3.5 Transcribe](https://blog.google/innovation-and-ai/technology/developers-tools/build-real-time-voice-applications-gemini-audio/)
- [Chirp 3: Instant Custom Voice](https://docs.cloud.google.com/text-to-speech/docs/chirp3-instant-custom-voice)

## Third-party reporting and community

- [9to5Google: 3.8 Live powers Gemini Live, Gmail and Keep](https://9to5google.com/2026/09/15/gemini-3-8-live-announced/)
- [MarkTechPost: production-grade voice agents](https://www.marktechpost.com/2026/09/15/google-releases-gemini-3-8-live-and-3-8-live-extended-thinking-for-production-grade-voice-agents/)
- [TechRepublic: models that reason while they talk](https://www.techrepublic.com/article/news-gemini-3-8-live-models/)
- [DataCamp: features, benchmarks, pricing](https://www.datacamp.com/blog/gemini-3-8-live)
- [pasqualepillitteri.it: about $1.38/hour](https://pasqualepillitteri.it/en/news/16547/gemini-3-8-live-audio-model)
- [getapipulse: pricing, thinking and migration](https://www.getapipulse.com/blog-gemini-3-8-live-api.html)
- [cellcog: 3.8 Live vs GPT-Live-1](https://cellcog.ai/blog/gemini-3-8-live/)
- [benchlm: Extended Thinking specs](https://benchlm.ai/models/gemini-3-8-live-extended-thinking)
- [The New Stack: self-serve voice replication](https://thenewstack.io/gemini-tts-voice-replication-api/)
- [Android Authority: Gemini can clone your voice](https://www.androidauthority.com/gemini-3-8-flash-text-to-speech-rolling-out-3714915/)
- [latenode: where cloning works and what consent covers](https://latenode.com/ai-trends/gemini-3-8-tts-voice-cloning)
- [eesel: 3.8 Flash TTS pricing](https://www.eesel.ai/blog/gemini-3-8-flash-tts-pricing)
- [buildfastwithai: 3.8 Flash TTS review](https://blog.buildfastwithai.com/gemini-3-8-flash-tts-review)
- [Google AI Developers Forum: 3.8 Live support for custom voices](https://discuss.ai.google.dev/t/gemini-3-8-live-support-for-voice-design-custom-voices/184651)
- [Google AI Developers Forum: 3.8 TTS voice consistency](https://discuss.ai.google.dev/t/gemini-3-8-tts-voice-consistency-issue/186103)
- [openclaw #152830: 3.8 Live never ends the turn after audioStreamEnd](https://github.com/openclaw/openclaw/issues/152830)
- [openclaw #152646: Extended Thinking transcripts never finalize over relay](https://github.com/openclaw/openclaw/issues/152646)
- [TechRadar: Gemini Live Avatars](https://techradar.com/pro/googles-creepy-new-gemini-live-avatars-want-to-try-and-make-online-support-bots-feel-more-human)

## Open questions (verify with a real API key)

1. **Voices API IDs in Live.** The forum says they don't work with `gemini-3.8-live` yet.
   Watch the changelog; when they do, the Live path can drop the inline sample.
2. **Billing of the inline voice sample.** It isn't documented whether the per-session
   `voice_sample_audio` counts as billable audio input.
3. **Consent-signature lifetime.** The SDK only says it "may be used in a subsequent
   request" and fails when expired. The TTL isn't documented, so the MCP server retries
   automatically.
4. **`speech_metadata` style with replicated voices.** The annotation is defined in the
   SDK types. Confirm that delivery direction works as well on replicated voices as on
   prebuilt ones.
5. **Consent-phrase list.** Get the exact wording for the 25 languages from the Voice
   replication page before recording.
6. **TTS output format.** The adapter asks the Interactions API for `audio/wav`
   (`response_format`). It also decodes raw PCM, but confirm the field is accepted
   for `gemini-3.8-flash-tts`.
7. **Default `thinking_level`** on Extended Thinking when omitted (the server decides).
8. **Rate limits and quotas** per tier for 3.8 Live and 3.8 TTS.
