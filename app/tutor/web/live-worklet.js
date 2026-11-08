// Runs on the audio thread: turns microphone audio (in a 16 kHz AudioContext) into 512-sample
// PCM16 frames (32 ms) for the live conversation WebSocket.
class PcmCapture extends AudioWorkletProcessor {
  constructor() {
    super();
    this.frame = new Int16Array(512);
    this.n = 0;
  }

  process(inputs) {
    const channel = inputs[0] && inputs[0][0];
    if (channel) {
      for (let i = 0; i < channel.length; i++) {
        const s = Math.max(-1, Math.min(1, channel[i]));
        this.frame[this.n++] = s < 0 ? s * 0x8000 : s * 0x7fff;
        if (this.n === 512) {
          this.port.postMessage(this.frame.buffer, [this.frame.buffer]);
          this.frame = new Int16Array(512);
          this.n = 0;
        }
      }
    }
    return true;
  }
}

registerProcessor("pcm-capture", PcmCapture);
