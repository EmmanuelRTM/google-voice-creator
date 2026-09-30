// Browser client for Gemini 3.8 Live in your cloned voice (Google's client-to-server pattern).
//
// 1. The signed-in user asks the Voice Creator server for an ephemeral token
//    (POST /v1/live-tokens). The token is single-use, short-lived, and locked to the
//    profile's cloned voice, model and instructions; the Gemini API key never reaches
//    the browser.
// 2. The browser connects to Gemini Live directly with that token, streams the
//    microphone as 16 kHz PCM and plays the 24 kHz reply, flushing on barge-in.
import { GoogleGenAI, Modality } from "@google/genai";

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
  await context.audioWorklet.addModule(new URL("./pcm-recorder.js", import.meta.url));
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
  const { token, model, api_version: apiVersion } = await mintLiveToken();
  // The ephemeral token takes the place of the API key.
  const ai = new GoogleGenAI({ apiKey: token, httpOptions: { apiVersion } });
  const player = new Player();

  const session = await ai.live.connect({
    model,
    // Voice, instructions and model are locked in the token; this only restates the modality.
    config: { responseModalities: [Modality.AUDIO] },
    callbacks: {
      onopen: () => log(`Connected to ${model} as "${PROFILE}"`),
      onmessage: (message) => {
        const content = message.serverContent;
        if (content?.interrupted) player.flush();
        if (message.data) player.play(message.data);
        if (content?.inputTranscription?.text) log(`You: ${content.inputTranscription.text}`);
        if (content?.outputTranscription?.text) log(`Voice: ${content.outputTranscription.text}`);
      },
      onerror: (error) => log(`Error: ${error.message}`),
      onclose: (event) => log(`Closed ${event.reason ? `(${event.reason})` : ""}`),
    },
  });

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
