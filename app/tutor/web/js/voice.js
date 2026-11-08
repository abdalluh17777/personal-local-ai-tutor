// The tutor's voice (read aloud) and push-to-talk. The chat decides what to do with a transcript (bindMic).

import { api } from "./api.js";
import { h, icon, toast } from "./dom.js";

let audioCtx = null;
export function audioContext() {
  // Created on a user gesture (click or key), as browsers require before playing sound
  if (!audioCtx) audioCtx = new AudioContext();
  if (audioCtx.state === "suspended") audioCtx.resume();
  return audioCtx;
}

// What will be spoken: markdown and citation labels removed; Arabic is kept (the Arabic voice speaks it)
export function cleanForSpeech(text) {
  return text.replace(/\[S\d+\]/g, "").replace(/[*_`#>|]+/g, "").replace(/\s+/g, " ").trim();
}
export const SPEAKABLE = /[A-Za-z؀-ۿ]/;

// Cuts streamed text into sentences so speech can start before the reply is complete
export function makeSplitter(emit) {
  let buf = "";
  return {
    push(text) {
      buf += text;
      for (;;) {
        const re = /[.!?…](?:["')\]]*)\s+|\n\s*/g;
        let m;
        let cut = -1;
        while ((m = re.exec(buf))) {
          const end = m.index + m[0].length;
          if (end >= 24 || m[0].includes("\n")) { cut = end; break; }
        }
        if (cut < 0) break;
        const sentence = buf.slice(0, cut);
        buf = buf.slice(cut);
        if (SPEAKABLE.test(cleanForSpeech(sentence))) emit(sentence);
      }
    },
    flush() {
      if (SPEAKABLE.test(cleanForSpeech(buf))) emit(buf);
      buf = "";
    },
  };
}

function splitSentences(text) {
  const out = [];
  const s = makeSplitter(x => out.push(x));
  s.push(text);
  s.flush();
  return out;
}

// Plays sentences back to back with Web Audio: the next sentence is synthesised while the
// current one plays, and buffers are scheduled on one clock so there are no gaps or clicks.
export const speaker = {
  queue: [], sources: [], nextTime: 0, generation: 0, fetching: false,
  enqueue(text, opts = {}) {
    if (!SPEAKABLE.test(cleanForSpeech(text))) return;
    this.queue.push({ text, ...opts });
    this.pump();
  },
  async pump() {
    if (this.fetching) return;
    this.fetching = true;
    const gen = this.generation;
    try {
      while (this.queue.length && gen === this.generation) {
        const item = this.queue.shift();
        const r = await fetch("/api/voice/speak", { method: "POST", headers: { "Content-Type": "application/json" },
                                                     body: JSON.stringify(item) });
        if (!r.ok) {
          const d = await r.json().catch(() => ({}));
          throw new Error(d.detail || `Speech failed (${r.status})`);
        }
        const data = await r.arrayBuffer();
        if (gen !== this.generation) break;
        const ctx = audioContext();
        const buffer = await ctx.decodeAudioData(data);
        if (gen !== this.generation) break;
        const src = ctx.createBufferSource();
        src.buffer = buffer;
        src.connect(ctx.destination);
        const start = Math.max(ctx.currentTime + 0.05, this.nextTime);
        src.start(start);
        this.nextTime = start + buffer.duration + 0.12;   // a short, natural pause between sentences
        this.sources.push(src);
        src.onended = () => { this.sources = this.sources.filter(s => s !== src); };
      }
    } catch (e) {
      toast(e.message, "error");
    } finally {
      this.fetching = false;
      // Whatever is queued now belongs to the current generation (stop() empties the queue), including text
      // queued after a stop() while this older fetch was still running: a fresh pump speaks it
      if (this.queue.length) this.pump();
    }
  },
  stop() {
    this.generation += 1;
    this.queue = [];
    for (const s of this.sources) { try { s.stop(); } catch { /* already ended */ } }
    this.sources = [];
    this.nextTime = 0;
  },
};

export function speakText(text, opts) {
  audioContext();
  speaker.stop();
  for (const s of splitSentences(text)) speaker.enqueue(s, opts);
}

// A small "▶ Listen" button that reads some text aloud
export const listenButton = (getText, label = "Listen") =>
  h("button", { class: "listen", type: "button", title: "Read aloud", onclick: () => speakText(getText()) },
    icon("play"), label);

// Whisper and Kokoro load in the background the first time the chat opens, so the first spoken turn is fast
let warmed = false;
export function warmUpVoice() {
  if (warmed) return;
  warmed = true;
  api("/api/voice/warmup", { method: "POST" }).catch(e => toast("Voice is not ready: " + e.message, "error"));
}

// ---------------------------------------------------------------- push-to-talk
// The recorder itself; the chat binds what the button looks like and what happens with the text.
export const mic = {
  recorder: null, stream: null, chunks: [], source: null, analyser: null, raf: 0, timer: 0,
  startedAt: 0, busy: false, starting: false, cancelled: false,
  hooks: {
    blocked: () => false,          // true while the tutor is answering: no new recording
    sessionId: () => null,         // the transcript is helped by the session's course and topic
    onChange: () => {},            // recording / transcribing started or stopped; also every frame while recording
    onLevel: () => {},             // 0..1 input level, every frame while recording
    onText: () => {},              // the transcript
  },
};
const MAX_RECORDING_MS = 120000;

export function bindMic(hooks) { Object.assign(mic.hooks, hooks); }

export async function startRecording() {
  if (mic.recorder || mic.starting || mic.busy || mic.hooks.blocked()) return;
  if (!window.MediaRecorder || !navigator.mediaDevices?.getUserMedia) {
    toast("This browser cannot record audio. Use Chrome or Edge.", "error");
    return;
  }
  speaker.stop();   // the learner starts talking: the tutor stops
  const ctx = audioContext();
  // Waiting for the microphone (or its permission prompt): a second Ctrl+Space must not start a second recorder
  mic.starting = true;
  try {
    mic.stream = await navigator.mediaDevices.getUserMedia({ audio: {
      channelCount: 1, echoCancellation: true, noiseSuppression: true, autoGainControl: true } });
  } catch (e) {
    toast("Microphone not available: " + e.message, "error");
    return;
  } finally {
    mic.starting = false;
  }
  const type = MediaRecorder.isTypeSupported("audio/webm;codecs=opus") ? "audio/webm;codecs=opus" : "";
  mic.recorder = new MediaRecorder(mic.stream, type ? { mimeType: type, audioBitsPerSecond: 128000 } : undefined);
  mic.chunks = [];
  mic.cancelled = false;
  mic.recorder.ondataavailable = e => { if (e.data.size) mic.chunks.push(e.data); };
  mic.recorder.onstop = finishRecording;
  mic.recorder.start();
  mic.startedAt = performance.now();

  // Live input level on the mic button, so the learner sees they are being heard
  mic.source = ctx.createMediaStreamSource(mic.stream);
  mic.analyser = ctx.createAnalyser();
  mic.analyser.fftSize = 1024;
  mic.source.connect(mic.analyser);
  const samples = new Float32Array(mic.analyser.fftSize);
  const draw = () => {
    mic.analyser.getFloatTimeDomainData(samples);
    let sum = 0;
    for (const v of samples) sum += v * v;
    mic.hooks.onLevel(Math.min(1, Math.sqrt(sum / samples.length) * 6));
    mic.hooks.onChange();
    mic.raf = requestAnimationFrame(draw);
  };
  draw();
  mic.timer = setTimeout(() => stopRecording(), MAX_RECORDING_MS);
}

export function stopRecording(cancel = false) {
  if (!mic.recorder) return;
  mic.cancelled = cancel;
  cancelAnimationFrame(mic.raf);
  clearTimeout(mic.timer);
  mic.source?.disconnect();
  mic.recorder.stop();
  for (const t of mic.stream.getTracks()) t.stop();
  mic.hooks.onLevel(0);
}

async function finishRecording() {
  const blob = new Blob(mic.chunks, { type: mic.recorder.mimeType || "audio/webm" });
  mic.recorder = null;
  mic.stream = null;
  if (mic.cancelled) { mic.hooks.onChange(); return; }
  if (performance.now() - mic.startedAt < 400) {
    toast("Hold on a little longer while you speak.");
    mic.hooks.onChange();
    return;
  }
  mic.busy = true;
  mic.hooks.onChange();
  try {
    const form = new FormData();
    form.append("audio", blob, "speech.webm");
    const sessionId = mic.hooks.sessionId();
    if (sessionId) form.append("session_id", sessionId);
    const r = await api("/api/voice/transcribe", { method: "POST", form });
    if (!r.text) { toast("I didn't catch that. Try again a little closer to the microphone."); return; }
    mic.hooks.onText(r.text);   // not awaited: sending it streams a whole reply, and the mic is free again now
  } catch (e) {
    toast(e.message, "error");
  } finally {
    mic.busy = false;
    mic.hooks.onChange();
  }
}

export function toggleRecording() {
  if (mic.recorder) stopRecording();
  else startRecording();
}
