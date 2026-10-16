// Browser client for Gemini 3.8 Live in your cloned voice (Google's client-to-server pattern).
//
// 1. The signed-in user asks the Voice Creator server for an ephemeral token
//    (POST /v1/live-tokens). The token is single-use, short-lived, and locked to the
//    profile's cloned voice, model and instructions; the Gemini API key never reaches
//    the browser.
// 2. The browser connects to Gemini Live directly with that token, streams the
//    microphone as 16 kHz PCM and plays the 24 kHz reply, flushing on barge-in.
import { GoogleGenAI, Modality } from "@google/genai";
// `?url&no-inline` keeps the worklet a real file in `vite build` (Vite inlines small assets
// as data: URLs by default, which a strict CSP blocks in `audioWorklet.addModule`).
import pcmRecorderUrl from "./pcm-recorder.js?url&no-inline";

const TOKEN_ENDPOINT = "/v1/live-tokens";
const PROFILE = new URLSearchParams(location.search).get("profile") ?? "me";
const INPUT_MIME_TYPE = "audio/pcm;rate=16000";
const OUTPUT_SAMPLE_RATE = 24000;

const log = (line) => {
  document.querySelector("#log").textContent += `${line}\n`;
};

// Replace with your OAuth/OIDC client (authorization code + PKCE): an access token for
// the Voice Creator server with the `dialogue:generate` scope. A loopback development
// server without OAuth needs none.
async function getAccessToken() {
  return null;
}

async function mintLiveToken() {
  const accessToken = await getAccessToken();
  const response = await fetch(TOKEN_ENDPOINT, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      ...(accessToken ? { Authorization: `Bearer ${accessToken}` } : {}),
    },
    body: JSON.stringify({ profile: PROFILE, uses: 1, ttl_minutes: 30 }),
  });
  if (!response.ok) {
    const problem = await response.json().catch(() => ({}));
    throw new Error(`Token request failed (${response.status}): ${problem.error_description ?? ""}`);
  }
  return response.json(); // { token, model, api_version, expire_time, new_session_expire_time, uses }
}

class Player {
  constructor() {
    this.context = new AudioContext({ sampleRate: OUTPUT_SAMPLE_RATE });
    this.playAt = 0;
    this.sources = new Set();
  }

  play(base64Pcm) {
    const bytes = Uint8Array.from(atob(base64Pcm), (c) => c.charCodeAt(0));
    const pcm = new Int16Array(bytes.buffer, 0, bytes.byteLength >> 1);
    const buffer = this.context.createBuffer(1, pcm.length, OUTPUT_SAMPLE_RATE);
    const channel = buffer.getChannelData(0);
    for (let i = 0; i < pcm.length; i++) channel[i] = pcm[i] / 32768;

    const source = this.context.createBufferSource();
    source.buffer = buffer;
    source.connect(this.context.destination);
    this.playAt = Math.max(this.playAt, this.context.currentTime);
    source.start(this.playAt);
    this.playAt += buffer.duration;
    this.sources.add(source);
    source.onended = () => this.sources.delete(source);
  }

  // The user started talking over the model: drop everything still queued.
  flush() {
    for (const source of this.sources) source.stop();
    this.sources.clear();
    this.playAt = 0;
  }

  close() {
    this.flush();
    return this.context.close();
  }
}

async function startMicrophone(onChunk) {
  const stream = await navigator.mediaDevices.getUserMedia({
    audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true },
  });
  const context = new AudioContext({ sampleRate: 16000 });
  await context.resume(); // autoplay policy: a context may start suspended
  await context.audioWorklet.addModule(pcmRecorderUrl);
  const recorder = new AudioWorkletNode(context, "pcm-recorder");
  recorder.port.onmessage = (event) => onChunk(event.data);
  context.createMediaStreamSource(stream).connect(recorder);
  return () => {
    stream.getTracks().forEach((track) => track.stop());
    return context.close();
  };
}

function toBase64(buffer) {
  let binary = "";
  for (const byte of new Uint8Array(buffer)) binary += String.fromCharCode(byte);
  return btoa(binary);
}

async function start() {
  // Created inside the click gesture, before any await, so the autoplay policy allows it.
  const player = new Player();
  await player.context.resume();
  const { token, model, api_version: apiVersion } = await mintLiveToken();
  // The ephemeral token takes the place of the API key.
  const ai = new GoogleGenAI({ apiKey: token, httpOptions: { apiVersion } });

  // Transcripts arrive in fragments; the Live guide collects them until the turn completes.
  const transcript = { you: "", voice: "" };
  // `connect` only settles once setup completes, so a socket that closes first (expired or
  // reused token, rejected config) would hang the Start button: race it against the close.
  let rejectOnClose;
  const closed = new Promise((_, reject) => (rejectOnClose = reject));
  closed.catch(() => {});

  const session = await Promise.race([
    ai.live.connect({
      model,
      // Voice, instructions and model are locked in the token; this only restates the modality.
      config: { responseModalities: [Modality.AUDIO] },
      callbacks: {
        onopen: () => log(`Connected to ${model} as "${PROFILE}"`),
        onmessage: (message) => {
          const content = message.serverContent;
          if (content?.interrupted) player.flush();
          // Walk every part, as the Live guide does; audio arrives as inline PCM parts.
          for (const part of content?.modelTurn?.parts ?? []) {
            if (part.inlineData?.mimeType?.startsWith("audio/pcm")) player.play(part.inlineData.data);
          }
          transcript.you += content?.inputTranscription?.text ?? "";
          transcript.voice += content?.outputTranscription?.text ?? "";
          if (content?.turnComplete) {
            if (transcript.you) log(`You: ${transcript.you}`);
            if (transcript.voice) log(`Voice: ${transcript.voice}`);
            transcript.you = transcript.voice = "";
          }
        },
        // Browsers raise a plain Event here (no `.message`); the close code carries the detail.
        onerror: (error) => log(`Error: ${error.message ?? "WebSocket error (see close code)"}`),
        onclose: (event) => {
          log(`Closed (${event.code})${event.reason ? ` ${event.reason}` : ""}`);
          rejectOnClose(new Error(`Live session closed (${event.code})`));
          if (!stopButton.disabled) stopButton.click(); // token expiry, GoAway, session limit
        },
      },
    }),
    closed,
  ]);

  const stopMicrophone = await startMicrophone((pcm) =>
    session.sendRealtimeInput({ audio: { data: toBase64(pcm), mimeType: INPUT_MIME_TYPE } }),
  );
  return async () => {
    await stopMicrophone();
    session.close();
    await player.close();
  };
}

const startButton = document.querySelector("#start");
const stopButton = document.querySelector("#stop");
let stop = null;

startButton.addEventListener("click", async () => {
  startButton.disabled = true;
  try {
    stop = await start(); // audio contexts must start from a user gesture
    stopButton.disabled = false;
  } catch (error) {
    log(error.message);
    startButton.disabled = false;
  }
});

stopButton.addEventListener("click", async () => {
  stopButton.disabled = true;
  await stop?.();
  stop = null;
  startButton.disabled = false;
});
