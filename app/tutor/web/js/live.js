// Voice mode (DESIGN.md section 3): a live conversation inside the chat view, which stays readable and usable. The
// microphone streams continuously to the server, which decides when the learner has finished (voice activity +
// whether the sentence sounds complete), answers, and streams the tutor's speech back. One soft sphere shows the state,
// by its motion only: in the middle of an empty chat, else floating just above the composer. The turns are the chat's
// own messages (the chat draws them, bindLive), and a message typed meanwhile goes over the same connection
// (sendLiveText).

import { $, h, popoverOpen, run, toast, typing } from "./dom.js";
import { audioContext, mic, speaker, stopRecording } from "./voice.js";

// The only words under the orb; the other states are said to screen readers only (the caption is a live region)
const CAPTIONS = { connecting: "Connecting…", muted: "Muted" };
const SPOKEN = { listening: "Listening", user_speaking: "You are speaking", thinking: "Thinking", speaking: "Speaking" };
const THINKING_DRIFT = 3.5;   // how much faster the clouds drift while the tutor thinks

export const live = {
  active: false, starting: false, ws: null, stream: null, ctx: null, node: null, sink: null, outAnalyser: null,
  muted: false, sources: [], nextTime: 0, playing: false, staleTurn: 0, turns: {}, micLevel: 0, level: 0, state: "",
  error: "", raf: 0, runId: 0,
};

// What the chat provides
const chat = {
  busy: () => false,                          // a typed reply is streaming
  ensureSession: async () => null,            // the open session's id, starting one if needed (null: could not)
  hasMessages: () => false,                   // the chat shows messages (else the orb sits in its middle)
  addTurn: () => ({ user: null, tutor: null }),   // the learner's words and an empty, pending reply
  setReply: () => {},                         // (tutorEl, text, done)
  replyDone: () => {},                        // (turn, event): the complete reply, sources, "checking your English"
  replyCut: () => {},                         // (turn): a reply cut short, kept as it stands
  dropTurn: () => {},                         // (turn): words the learner went on talking over, and no reply
  feedback: () => {},                         // (userEl, feedback)
  scrollDown: () => {},
  onActive: () => {},                         // voice mode opened (true) or closed (false)
  onMuted: () => {},                          // the microphone was muted (true) or unmuted (false)
};
export function bindLive(hooks) { Object.assign(chat, hooks); }

function liveSend(message) {
  if (live.ws?.readyState === WebSocket.OPEN) live.ws.send(JSON.stringify(message));
}

// A message typed in voice mode: the server answers it (aloud) like a finished utterance, and its user_final draws
// it in the chat. False when there is no connection (yet) to send it on.
export function sendLiveText(text) {
  if (!live.active || live.ws?.readyState !== WebSocket.OPEN) return false;
  liveSend({ type: "text", text });
  return true;
}

// The state is on the voice mode's root (data-state): app.css moves the orb by it
function setLiveState(state) {
  live.state = state;
  $("#voice-mode").dataset.state = state;
  renderCaption();
  // While the tutor thinks the clouds drift faster. Their speed changes, not their place (as a new CSS duration
  // would do): no jump when the state changes.
  for (const a of $("#live-orb").getAnimations({ subtree: true })) {
    if (a.animationName?.startsWith("vm-cloud")) a.updatePlaybackRate(state === "thinking" ? THINKING_DRIFT : 1);
  }
}

// The caption under the orb: an error (red) until the next turn, else Connecting… or Muted, else nothing to see
function renderCaption() {
  const caption = $("#vm-caption");
  const shown = live.error || CAPTIONS[live.state] || "";
  caption.classList.toggle("is-error", !!live.error);
  caption.replaceChildren(shown || h("span", { class: "sr-only" }, SPOKEN[live.state] || ""));
}

function liveTurn(turn) {
  return live.turns[turn] || (live.turns[turn] = { user: null, tutor: null, reply: "", done: false });
}

// ---------------------------------------------------------------- where the orb is
// In the middle of the empty message area while the chat has no messages (its welcome is hidden meanwhile), else
// floating just above the composer. The chat view's data-voice attribute lays both out (app.css, voice mode).
function placeOrb() {
  if (!live.active) return;
  $("#view-chat").dataset.voice = chat.hasMessages() ? "docked" : "centred";
  chat.scrollDown();   // the messages make room for the orb below them: the latest stays in view
}

// A server error: a toast, and the red caption under the orb until the next turn
function showError(message) {
  live.error = message;
  renderCaption();
}

function clearError() {
  if (!live.error) return;
  live.error = "";
  renderCaption();
}

// ---------------------------------------------------------------- the server's messages
function playLiveAudio(buffer) {
  const view = new DataView(buffer);
  const turn = view.getUint32(0, true);
  const rate = view.getUint32(4, true);
  if (turn <= live.staleTurn) return;   // speech from a reply the learner already interrupted
  const pcm = new Int16Array(buffer, 8);
  const ctx = audioContext();
  const audio = ctx.createBuffer(1, pcm.length, rate);
  const channel = audio.getChannelData(0);
  for (let i = 0; i < pcm.length; i++) channel[i] = pcm[i] / 32768;
  const src = ctx.createBufferSource();
  src.buffer = audio;
  src.connect(live.outAnalyser);
  const start = Math.max(ctx.currentTime + 0.04, live.nextTime);
  src.start(start);
  live.nextTime = start + audio.duration + 0.1;   // sentences back to back, with a natural breath between
  live.sources.push(src);
  if (!live.playing) { live.playing = true; liveSend({ type: "playback", playing: true }); }
  src.onended = () => {
    live.sources = live.sources.filter(s => s !== src);
    if (!live.sources.length && live.playing) { live.playing = false; liveSend({ type: "playback", playing: false }); }
  };
}

function stopLiveAudio() {
  for (const s of live.sources) { s.onended = null; try { s.stop(); } catch { /* already ended */ } }
  live.sources = [];
  live.nextTime = 0;
  if (live.playing) { live.playing = false; liveSend({ type: "playback", playing: false }); }
}

// A reply still coming in when it is cut (the learner talked or typed over it, or voice mode ended): what was said
// stays, marked " …" as the server saves it; a turn with no reply yet goes, as the server drops it
function cutTurn(t) {
  if (!t.tutor || t.done) return;
  t.done = true;
  if (t.reply) {
    t.reply += " …";
    chat.setReply(t.tutor, t.reply, true);
    chat.replyCut(t);
  } else {
    chat.dropTurn(t);
  }
}

function onLiveMessage(e) {
  if (typeof e.data !== "string") { playLiveAudio(e.data); return; }
  const ev = JSON.parse(e.data);
  const t = ev.turn !== undefined ? liveTurn(ev.turn) : null;
  switch (ev.type) {
    case "state":
      setLiveState(ev.state);
      break;
    case "user_final":   // spoken or typed: the learner's message and the reply to come, as in a typed turn
      clearError();
      Object.assign(t, chat.addTurn(ev.text));
      placeOrb();
      chat.scrollDown(true);
      break;
    case "token":
      if (!t.tutor || t.done) break;
      t.reply += ev.text;
      chat.setReply(t.tutor, t.reply, false);
      chat.scrollDown();
      break;
    case "reply_done":
      if (!t.tutor || t.done) break;
      t.done = true;
      chat.replyDone(t, ev);
      chat.scrollDown();
      break;
    case "feedback":
      if (t.user?.isConnected) chat.feedback(t.user, ev.feedback);
      chat.scrollDown();
      break;
    case "interrupt":
      stopLiveAudio();
      live.staleTurn = ev.turn;
      // With no reply yet, the learner was still talking: these words come back as part of their next turn
      if (t) cutTurn(t);
      placeOrb();
      break;
    case "metrics":   // timings go to the server's metrics log (logs\live-metrics.jsonl), never to the screen
      break;
    case "error":
      toast(ev.message, "error");
      showError(ev.message);
      break;
  }
}

// The orb's size follows the learner's voice while they speak, and the tutor's while it speaks (--level, 0..1: app.css
// scales the orb by 1 + 0.25 × level); in the other states it settles back to its own size
function animateOrb() {
  const data = new Float32Array(512);
  const orb = $("#live-orb");
  const tick = () => {
    if (!live.active) return;
    let level = 0;
    if (live.state === "speaking" && live.outAnalyser) {
      live.outAnalyser.getFloatTimeDomainData(data);
      let sum = 0;
      for (const v of data) sum += v * v;
      level = Math.min(1, Math.sqrt(sum / data.length) * 5);
    } else if (live.state === "user_speaking") {
      level = live.micLevel;
    }
    live.level += (level - live.level) * 0.35;   // eased, so the orb swells and settles instead of flickering
    orb.style.setProperty("--level", live.level.toFixed(3));
    live.raf = requestAnimationFrame(tick);
  };
  tick();
}

// ---------------------------------------------------------------- opening and closing
export async function startLive() {
  if (live.active || live.starting) return;
  if (chat.busy()) { toast("Wait for the reply to finish (or stop it), then start voice mode."); return; }
  if (mic.recorder) stopRecording(true);
  speaker.stop();
  const playCtx = audioContext();   // inside the click or key press, so the page may play sound
  // Starting a session ends any live run first (the chat's startSession), so the session comes before voice mode
  // opens; meanwhile a second click or shortcut must not start a second run
  live.starting = true;
  let sessionId;
  try {
    sessionId = await chat.ensureSession();
  } finally {
    live.starting = false;
  }
  if (!sessionId || live.active || $("#view-chat").hidden) return;
  const runId = ++live.runId;
  Object.assign(live, { active: true, turns: {}, staleTurn: 0, muted: false, error: "", micLevel: 0, level: 0 });
  $("#voice-mode").hidden = false;
  setLiveState("connecting");
  placeOrb();
  chat.onActive(true);
  // End or Esc can stop this start while it waits (microphone permission, the worklet, the connection):
  // stopLive releases what is already on `live`, and after each wait a stopped start releases what it holds
  // and goes no further, so no hidden session keeps the microphone streaming.
  const stopped = () => !live.active || live.runId !== runId;
  try {
    live.outAnalyser = playCtx.createAnalyser();
    live.outAnalyser.fftSize = 512;
    live.outAnalyser.connect(playCtx.destination);

    const stream = await navigator.mediaDevices.getUserMedia({ audio: {
      channelCount: 1, echoCancellation: true, noiseSuppression: true, autoGainControl: true } });
    if (stopped()) { for (const t of stream.getTracks()) t.stop(); return; }
    live.stream = stream;
    const ctx = live.ctx = new AudioContext({ sampleRate: 16000 });   // the browser resamples the microphone to 16 kHz
    await ctx.audioWorklet.addModule("/static/live-worklet.js");
    if (stopped()) { ctx.close().catch(() => { /* already closed by stopLive */ }); return; }
    live.node = new AudioWorkletNode(ctx, "pcm-capture");
    live.sink = ctx.createGain();
    live.sink.gain.value = 0;   // silent output that keeps the capture node running
    ctx.createMediaStreamSource(stream).connect(live.node).connect(live.sink).connect(ctx.destination);

    const ws = live.ws = new WebSocket(`ws://${location.host}/api/live?session_id=${sessionId}`);
    ws.binaryType = "arraybuffer";
    await new Promise((resolve, reject) => {
      ws.onopen = resolve;
      ws.onerror = () => reject(new Error("Could not connect to the tutor."));
    });
    if (stopped()) { ws.close(); return; }
    ws.onmessage = onLiveMessage;
    ws.onclose = () => { if (live.active && live.runId === runId) { toast("The live conversation was disconnected.", "error"); stopLive(); } };
    if (live.muted) liveSend({ type: "mute", muted: true });   // muted while it was connecting
    live.node.port.onmessage = e => {
      const frame = new Int16Array(e.data);
      let sum = 0;
      for (const v of frame) sum += v * v;
      live.micLevel = live.muted ? 0 : Math.min(1, (Math.sqrt(sum / frame.length) / 32768) * 6);
      if (!live.muted && live.ws?.readyState === WebSocket.OPEN) live.ws.send(e.data);
    };
    animateOrb();
  } catch (e) {
    if (stopped()) return;   // ended while starting (closing it can fail the wait): nothing to report
    toast(/permission|denied/i.test(e.message) ? "Voice mode needs microphone permission." : e.message, "error");
    stopLive();
  }
}

export function stopLive() {
  if (!live.active) return;
  live.active = false;
  cancelAnimationFrame(live.raf);
  stopLiveAudio();
  const ws = live.ws;
  live.ws = null;
  try { ws?.close(); } catch { /* already closed */ }
  if (live.node) live.node.port.onmessage = null;
  live.outAnalyser?.disconnect();
  for (const t of live.stream?.getTracks() || []) t.stop();
  live.ctx?.close();
  Object.assign(live, { ctx: null, stream: null, node: null, sink: null, muted: false });
  for (const t of Object.values(live.turns)) cutTurn(t);   // the server cuts them too when the connection closes
  $("#voice-mode").hidden = true;
  delete $("#view-chat").dataset.voice;
  $("#live-orb").style.setProperty("--level", "0");
  chat.onActive(false);
}

export function toggleLiveMute() {
  if (!live.active) return;
  live.muted = !live.muted;
  liveSend({ type: "mute", muted: live.muted });
  chat.onMuted(live.muted);
}

// ---------------------------------------------------------------- keys
// Voice mode's keys come first (capture phase): Ctrl+Shift+Space opens or ends it from the chat; while it is on, Ctrl+Space
// mutes and Esc ends it (from the message box too). Every other key reaches the chat as usual: it stays usable.
document.addEventListener("keydown", e => {
  const space = e.ctrlKey && !e.altKey && !e.metaKey && e.code === "Space";
  if (!space && e.key !== "Escape") return;
  if ($("#view-chat").hidden || document.querySelector("dialog[open]") || popoverOpen()) return;   // they handle their own keys
  if (space && e.shiftKey) {
    e.preventDefault();
    e.stopPropagation();
    if (live.active) stopLive();
    else run(startLive)();
  } else if (!live.active) {
    return;
  } else if (space) {
    e.preventDefault();
    e.stopPropagation();
    toggleLiveMute();
  } else if (!$("#app").classList.contains("sb-open") && !(typing(e) && !$("#view-chat").contains(e.target))) {
    // Not while the drawer is open (Esc closes it) or a field outside the chat (the sidebar's) has the focus
    e.preventDefault();
    e.stopPropagation();
    stopLive();
  }
}, true);

// Voice mode lives in the chat view: going to a page ends it
new MutationObserver(() => { if (live.active && $("#view-chat").hidden) stopLive(); })
  .observe($("#view-chat"), { attributes: true, attributeFilter: ["hidden"] });
