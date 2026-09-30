// AudioWorklet: turns microphone samples into 16-bit PCM chunks of about 100 ms.
// The AudioContext runs at 16 kHz, the Live API's input rate, so no resampling is needed here.
class PcmRecorder extends AudioWorkletProcessor {
  constructor() {
    super();
    this.chunk = new Int16Array(sampleRate / 10);
    this.length = 0;
  }

  process(inputs) {
    const samples = inputs[0]?.[0];
    if (samples) {
      for (const sample of samples) {
        const clamped = Math.max(-1, Math.min(1, sample));
        this.chunk[this.length++] = clamped < 0 ? clamped * 0x8000 : clamped * 0x7fff;
        if (this.length === this.chunk.length) {
          this.port.postMessage(this.chunk.buffer.slice(0));
          this.length = 0;
        }
      }
    }
    return true;
  }
}

registerProcessor("pcm-recorder", PcmRecorder);
