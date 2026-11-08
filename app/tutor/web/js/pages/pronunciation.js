// Pronunciation: the learner says a word or sentence; the server checks every sound against the dictionary
// pronunciation with a phone recogniser on the GPU, and the page shows which sounds to work on. Drills practise the
// sounds that are hardest for Arabic speakers. The recording is kept only in the page, to play back.
//
// A drill can be opened directly: openDrill(id), the app event emit("drill", id), the route
// navigate("pronunciation/<drill id>") (Home), or the address #pronunciation/<drill id> (a link or a bookmark).

import { api } from "../api.js";
import { $, button, card, empty, fmtDate, h, icon, navigate, on, pageHeader, run, toast } from "../dom.js";
import { audioContext, speaker, speakText } from "../voice.js";

const pron = { drills: [], drill: null, text: "", ipa: null, result: null, recorder: null, stream: null, source: null,
               chunks: [], startedAt: 0, timer: 0, raf: 0, busy: false, starting: false, cancelled: false,
               audioUrl: null, selectedWord: null, root: null, pendingDrill: null };
// The server refuses recordings over 20 s; the timer and the encoder can run a little past the cap, so stop short
const PRON_MAX_MS = 19000;
const PRON_STATUS = { good: "Good", unsure: "Check", wrong: "Try again" };
const STATUS_ICON = { unsure: "info", wrong: "x" };   // words and sounds are not told apart by colour alone

// ---------------------------------------------------------------- opening a drill from elsewhere
export function openDrill(id) {
  pron.pendingDrill = id;
  navigate("pronunciation");
}
on("drill", openDrill);

// The router knows only "pronunciation", so a drill in the route is taken here first. This module runs before the
// router starts, so its listeners come first: the address at start-up and on every change ("#pronunciation/<id>"
// becomes "#pronunciation", which the router then finds already showing), and the navigate event (the plain route
// is sent on in its place).
const DRILL_ROUTE = /^#?(?:pronunciation|pron)\/([\w-]+)$/;
function drillFromAddress() {
  const m = DRILL_ROUTE.exec(location.hash);
  if (!m) return false;
  pron.pendingDrill = m[1];
  history.replaceState(null, "", "#pronunciation");
  return true;
}
drillFromAddress();
window.addEventListener("hashchange", () => { if (drillFromAddress()) navigate("pronunciation"); });
document.addEventListener("tutor:navigate", e => {
  const m = DRILL_ROUTE.exec(typeof e.detail === "string" ? e.detail : "");
  if (!m) return;
  e.stopImmediatePropagation();
  openDrill(m[1]);
});

// ---------------------------------------------------------------- the page
export async function render(el) {
  pron.root = el;
  const [drills, progress] = await Promise.all([api("/api/pronunciation/drills"), api("/api/pronunciation/progress")]);
  if (pron.root !== el) return;
  pron.drills = drills;
  let opened = null;
  if (pron.pendingDrill) {
    const id = pron.pendingDrill;
    pron.pendingDrill = null;
    opened = drills.find(d => d.id === id) || null;
    if (opened) {
      pronStop(true);
      pron.drill = opened;
      pron.result = null;
    } else {
      toast(`There is no drill called "${id}".`, "error");
    }
  }
  el.replaceChildren(
    pageHeader("Pronunciation", "Listen, then say it. Every sound is checked against the dictionary pronunciation in " +
      "American English; British vowels and a dropped r are accepted too."),
    h("div", { class: "stack" },
      h("section", { class: "card pron-choose", "aria-labelledby": "pron-choose-title" },
        h("h2", { class: "card-title", id: "pron-choose-title" }, "Choose what to practise"),
        h("div", { class: "chips pron-drills", role: "group", "aria-labelledby": "pron-choose-title" })),
      h("section", { class: "card pron-practice", id: "pron-practice", "aria-label": "Practice" }),
      renderPronProgress(progress)));
  drawChips();
  drawPractice();
  if (opened) setPronText(opened.pairs[0][0]);
}

function drawChips() {
  const box = pron.root?.querySelector(".pron-drills");
  if (!box) return;
  const current = pron.drill?.id ?? null;
  const chip = (label, d) => h("button", {
    type: "button", class: "chip" + (current === (d?.id ?? null) ? " is-active" : ""),
    "aria-pressed": String(current === (d?.id ?? null)), onclick: () => chooseDrill(d) }, label);
  box.replaceChildren(chip("Your own sentence", null), ...pron.drills.map(d => chip(d.title, d)));
}

function chooseDrill(d) {
  pronStop(true);   // a recording made for the previous drill is not checked against this one
  pron.drill = d;
  pron.result = null;
  drawChips();
  if (d) setPronText(d.pairs[0][0]);
  else drawPractice();
}

// From the sounds list, far down the page: the practice panel comes into view
function practise(d) {
  chooseDrill(d);
  $("#pron-practice")?.scrollIntoView({ behavior: "smooth", block: "start" });
}

// What a weak sound most often became. An empty "heard" means the sound was left out (the server labels it
// "nothing", which must not be shown as if it were a sound between slashes).
export function insteadNote(s) {
  if (s.instead?.[0] === "") return "often left out";
  return s.instead_ipa?.[0] ? `often said as /${s.instead_ipa[0]}/` : "";
}

// "right 40% of 5 times · often left out": each phrase stays whole (its "·" with it), so a narrow card wraps
// between them
const soundNote = (...phrases) => phrases.filter(Boolean).flatMap((p, i) => [i ? " " : null, h("span", { class: "nowrap" }, i ? `· ${p}` : p)]);

const band = score => (score >= 85 ? "is-good" : score >= 70 ? "is-ok" : "is-low");

function renderPronProgress(p) {
  const sounds = p.sounds.length ? h("ul", { class: "pron-sounds" }, p.sounds.slice(0, 12).map(s => {
    const percent = Math.round(100 * s.accuracy);
    const drill = s.drill && s.accuracy < 0.9 ? pron.drills.find(d => d.id === s.drill) : null;
    return h("li", { class: "pron-sound" },
      h("span", { class: "pron-sound-ipa ipa" }, `/${s.ipa}/`),
      h("div", { class: "grow" },
        h("div", { class: "progress" + (s.accuracy < 0.7 ? " is-low" : ""), role: "progressbar", "aria-valuenow": percent,
                   "aria-valuemin": 0, "aria-valuemax": 100, "aria-label": `/${s.ipa}/ said right` },
          h("div", { class: "progress-bar", style: `--value:${percent}%` })),
        h("div", { class: "card-meta" }, soundNote(`right ${percent}% of ${s.total} times`, insteadNote(s)))),
      drill ? button("Practise", { size: "sm", "aria-label": `Practise ${drill.title}`, onclick: () => practise(drill) }) : null);
  })) : empty("After a few checks, your sounds appear here, weakest first (last 30 days).");
  const clusters = pron.drills.find(d => d.id === "clusters");
  const added = p.added_vowels ? h("p", { class: "pron-added small" },
    `Vowels added between consonants: ${p.added_vowels} times in 30 days. `,
    clusters ? h("button", { type: "button", class: "link-btn", onclick: () => practise(clusters) }, "Practise consonant groups") : null) : null;
  const recent = p.recent.length ? h("ul", { class: "list pron-recent" }, p.recent.map(a => h("li", {},
    h("button", { type: "button", class: "link-btn grow", onclick: run(() => openAttempt(a.id)) }, a.text),
    h("span", { class: `badge pron-score-badge ${band(a.score)}` }, h("span", { class: "sr-only" }, "score "), `${a.score}`),
    h("span", { class: "card-meta nowrap" }, fmtDate(a.created_at))))) : null;
  return h("div", { class: "grid pron-progress" }, card("Your sounds", sounds, added), recent ? card("Recent checks", recent) : null);
}

async function openAttempt(id) {
  const full = await api(`/api/pronunciation/attempts/${id}`);
  await setPronText(full.text);
  if (pron.text !== full.text.trim()) return;   // something else was chosen meanwhile
  pron.result = full.result;
  drawPractice();
  $("#pron-practice")?.scrollIntoView({ behavior: "smooth" });
}

// ---------------------------------------------------------------- the practice panel
function drawPractice() {
  const box = $("#pron-practice");
  if (!box) return;
  const parts = pron.drill ? drillParts(pron.drill) : ownParts();
  if (pron.text) parts.push(sayPanel());
  box.replaceChildren(...parts.filter(Boolean));
}

function drillParts(d) {
  const pick = (text, cls) => h("button", {
    type: "button", class: `chip ${cls}` + (pron.text === text ? " is-active" : ""), "aria-pressed": String(pron.text === text),
    onclick: () => setPronText(text) }, text);
  return [
    h("h2", { class: "card-title" }, d.title),
    h("p", { class: "pron-why" }, d.why),
    d.sounds.length ? h("ul", { class: "pron-how" }, d.sounds.map(s => h("li", {},
      h("span", { class: "pron-how-ipa ipa" }, `/${s.ipa}/`), h("span", {}, s.how)))) : null,
    h("h3", { class: "pron-label" }, "Word pairs"),
    h("div", { class: "pron-pairs" }, d.pairs.map(pair => h("div", { class: "pron-pair" },
      pair.map((w, i) => [i ? h("span", { class: "pron-pair-sep", "aria-hidden": "true" }, "·") : null, pick(w, "pron-pick")])))),
    d.sentences.length ? h("h3", { class: "pron-label" }, "Sentences") : null,
    h("div", { class: "pron-sentences" }, d.sentences.map(s => pick(s, "pron-pick pron-pick-sentence"))),
  ];
}

function ownParts() {
  const box = h("textarea", { class: "textarea", rows: 2, placeholder: "Type a word or a sentence to practise (up to 40 words)",
                              dir: "ltr", "aria-label": "Word or sentence to practise",
                              onkeydown: e => {   // Enter uses it; Shift+Enter is a new line
                                if (e.key === "Enter" && !e.shiftKey && !e.isComposing) { e.preventDefault(); setPronText(box.value); }
                              } }, pron.text);
  return [
    h("h2", { class: "card-title" }, "Your own sentence"),
    h("p", { class: "pron-why" }, "A word, a phrase or a sentence you want to say well. Enter to use it."),
    h("div", { class: "pron-own" }, box,
      h("div", { class: "pron-own-actions" },
        button("Use", { variant: "primary", onclick: () => setPronText(box.value) }),
        button("From my words", { icon: "cards", title: "A sentence from your vocabulary", onclick: run(async () => {
          const words = (await api("/api/vocab")).filter(w => w.example && w.example.split(" ").length <= 25);
          if (!words.length) { toast("No example sentences in your vocabulary yet."); return; }
          setPronText(words[Math.floor(Math.random() * words.length)].example);
        }) }))),
  ];
}

async function setPronText(text) {
  text = text.trim();
  if (!text) return;
  pronStop(true);   // a recording of the old text must not be checked against the new one
  pron.text = text;
  pron.result = null;
  if (pron.audioUrl) URL.revokeObjectURL(pron.audioUrl);
  pron.audioUrl = null;
  pron.selectedWord = null;
  pron.ipa = null;
  drawPractice();
  try {
    const ipa = await api(`/api/pronunciation/ipa?text=${encodeURIComponent(text)}`);
    if (pron.text !== text) return;   // another text was chosen while this one was being looked up
    pron.ipa = ipa;
    drawPractice();
  } catch { /* the IPA line is only a help */ }
}

function sayPanel() {
  const recording = !!pron.recorder;
  const ipa = pron.ipa?.map(w => w.ipa || "?").join(" ");
  const status = pron.busy ? [h("span", { class: "spinner", "aria-hidden": "true" }), "Checking…"]
    : recording ? "Listening… stop when you finish, or pause." : "";
  return h("div", { class: "pron-say" },
    h("p", { class: "pron-say-text", dir: "ltr" }, pron.text),
    // Always there (a blank line while it loads), so the buttons do not jump when it arrives
    h("p", { class: "pron-say-ipa ipa" }, ipa ? h("span", { class: "sr-only" }, "Dictionary pronunciation: ") : null, ipa || " "),
    h("div", { class: "pron-say-actions" },
      button("Listen", { icon: "play", title: "Hear it said slowly", onclick: () => speakText(pron.text, { speed: 0.85 }) }),
      h("button", { type: "button", id: "pron-rec", class: "btn btn-primary pron-rec" + (recording ? " is-recording" : ""),
                    disabled: pron.busy, "aria-pressed": String(recording),
                    onclick: () => (pron.recorder ? pronStop() : pronStart()) },
        icon(recording ? "stop" : "mic"), recording ? "Stop" : "Record"),
      pron.audioUrl ? button("My recording", { icon: "play", onclick: run(() => new Audio(pron.audioUrl).play()) }) : null),
    h("p", { id: "pron-status", class: "pron-status", "aria-live": "polite" }, status),
    pron.result ? pronResult(pron.result) : null);
}

// ---------------------------------------------------------------- the result
const legend = () => h("ul", { class: "pron-legend", "aria-hidden": "true" }, ["good", "unsure", "wrong"].map(s =>
  h("li", { class: `s-${s}` }, h("span", { class: "pron-swatch" }, STATUS_ICON[s] ? icon(STATUS_ICON[s]) : null),
    { good: "clear", unsure: "worth checking", wrong: "clearly different" }[s])));

function pronResult(r) {
  const label = r.score >= 85 ? "Very good" : r.score >= 70 ? "Good: a few sounds to work on" : "Keep practising";
  const sel = pron.selectedWord ?? Math.max(0, r.words.findIndex(w => w.status !== "good"));
  const word = r.words[sel];
  const partner = r.sounded_like;   // the server works out which pair word the swap turned this one into
  return h("div", { class: "pron-result" },
    h("div", { class: "pron-score-row" },
      h("div", { class: `pron-score ${band(r.score)}`, style: `--score:${r.score}`, role: "img", "aria-label": `Score: ${r.score} of 100` },
        h("span", { "aria-hidden": "true" }, `${r.score}`)),
      h("div", { class: "grow" }, h("strong", { class: "pron-score-label" }, label),
        h("p", { class: "card-meta" }, "Click a word to see its sounds."))),
    partner ? h("p", { class: "pron-notice" }, icon("info"), h("span", {}, `It sounded closer to "${partner}".`)) : null,
    h("div", { class: "pron-words", role: "group", "aria-label": "Your words" }, r.words.map((w, i) => h("button", {
      type: "button", class: `pron-word s-${w.status}`, title: PRON_STATUS[w.status], "aria-pressed": String(i === sel),
      onclick: () => { pron.selectedWord = i; drawPractice(); } },
      w.text, STATUS_ICON[w.status] ? icon(STATUS_ICON[w.status]) : null, h("span", { class: "sr-only" }, `: ${PRON_STATUS[w.status]}`)))),
    legend(),
    word ? h("div", { class: "pron-phones" },
      h("span", { class: "pron-phones-word" }, `"${word.text}"`),
      [...word.phones, null].map((p, k) => [
        // an added sound goes where it was said: "street" as /s + ə + t r i t/
        word.added.filter(a => a.before === k).map(a => h("span", { class: "pron-ph s-wrong", title: a.tip },
          `+/${a.heard_ipa}/`, h("small", {}, " added"))),
        p ? h("span", { class: `pron-ph s-${p.status}`, title: p.tip || PRON_STATUS[p.status] },
          `/${p.ipa}/`, p.status !== "good" ? h("small", {}, p.heard ? ` heard /${p.heard_ipa}/` : " missing") : null) : null]),
      button("Hear the word", { icon: "play", size: "sm", variant: "ghost", onclick: () => speakText(word.text) })) : null,
    r.issues.length ? h("ul", { class: "pron-issues" }, r.issues.slice(0, 3).map(i => h("li", {},
      h("span", { class: `badge pron-badge s-${i.status}` }, PRON_STATUS[i.status]),
      h("span", {}, h("strong", {}, i.word), i.phone ? ` /${i.ipa}/` : "", ": ", i.tip))))
      : h("p", { class: "pron-clear" }, icon("check"), "Every sound was clear. Try it a little faster, or try a longer sentence."));
}

// ---------------------------------------------------------------- recording
function updatePronStatus() {
  const el = $("#pron-status");
  if (!el || !pron.recorder) return;
  const secs = Math.floor((performance.now() - pron.startedAt) / 1000);
  el.textContent = `● Listening… ${secs} s · stop when you finish, or just pause`;
}

async function pronStart() {
  if (pron.recorder || pron.starting || pron.busy) return;
  if (!window.MediaRecorder || !navigator.mediaDevices?.getUserMedia) { toast("This browser cannot record audio. Use Chrome or Edge.", "error"); return; }
  speaker.stop();
  const ctx = audioContext();
  // Waiting for the microphone (or its permission prompt): a second click must not start a second recorder
  pron.starting = true;
  try {
    // No noise suppression or echo cancellation: they change the very sounds being checked
    pron.stream = await navigator.mediaDevices.getUserMedia({ audio: {
      channelCount: 1, echoCancellation: false, noiseSuppression: false, autoGainControl: true } });
  } catch (e) {
    toast("Microphone not available: " + e.message, "error");
    return;
  } finally {
    pron.starting = false;
  }
  const type = MediaRecorder.isTypeSupported("audio/webm;codecs=opus") ? "audio/webm;codecs=opus" : "";
  pron.recorder = new MediaRecorder(pron.stream, type ? { mimeType: type, audioBitsPerSecond: 128000 } : undefined);
  pron.chunks = [];
  pron.cancelled = false;
  pron.recorder.ondataavailable = e => { if (e.data.size) pron.chunks.push(e.data); };
  pron.recorder.onstop = pronFinish;
  pron.recorder.start();
  pron.startedAt = performance.now();
  // Stop by itself after a pause, once the learner has spoken
  pron.source = ctx.createMediaStreamSource(pron.stream);
  const analyser = ctx.createAnalyser();
  analyser.fftSize = 1024;
  pron.source.connect(analyser);
  const buf = new Float32Array(analyser.fftSize);
  let spoke = false;
  let quietSince = 0;
  const tick = () => {
    analyser.getFloatTimeDomainData(buf);
    let sum = 0;
    for (const v of buf) sum += v * v;
    const rms = Math.sqrt(sum / buf.length);
    const now = performance.now();
    if (rms > 0.02) { spoke = true; quietSince = 0; } else if (spoke && !quietSince) quietSince = now;
    $("#pron-rec")?.style.setProperty("--level", Math.min(1, rms * 6).toFixed(2));   // the ring around Record
    updatePronStatus();
    if (spoke && quietSince && now - quietSince > 1500) { pronStop(); return; }
    pron.raf = requestAnimationFrame(tick);
  };
  tick();
  pron.timer = setTimeout(() => pronStop(), PRON_MAX_MS);
  drawPractice();
}

// cancel: the recording is dropped, not checked (another text was chosen while it was running).
// The recorder is cleared only when it has finished (pronFinish), so a second call must see it is already stopping.
function pronStop(cancel = false) {
  if (!pron.recorder || pron.recorder.state === "inactive") return;
  pron.cancelled = cancel;
  cancelAnimationFrame(pron.raf);
  clearTimeout(pron.timer);
  pron.source?.disconnect();
  pron.recorder.stop();
  for (const t of pron.stream.getTracks()) t.stop();
}

async function pronFinish() {
  const blob = new Blob(pron.chunks, { type: pron.recorder.mimeType || "audio/webm" });
  pron.recorder = null;
  pron.stream = null;
  if (pron.cancelled) { drawPractice(); return; }
  if (performance.now() - pron.startedAt < 500) { toast("Say the whole word or sentence, then stop."); drawPractice(); return; }
  if (pron.audioUrl) URL.revokeObjectURL(pron.audioUrl);
  pron.audioUrl = URL.createObjectURL(blob);
  pron.busy = true;
  pron.result = null;
  pron.selectedWord = null;
  drawPractice();
  const text = pron.text;
  try {
    const form = new FormData();
    form.append("audio", blob, "speech.webm");
    form.append("text", text);
    if (pron.drill) form.append("drill", pron.drill.id);
    const result = await api("/api/pronunciation/check", { method: "POST", form });
    if (pron.text === text) pron.result = result;   // not shown under a text chosen while it was being checked
  } catch (e) {
    toast(e.message, "error");
  } finally {
    pron.busy = false;
    drawPractice();
  }
  try {
    const progress = await api("/api/pronunciation/progress");
    const old = pron.root?.querySelector(".pron-progress");
    if (old) old.replaceWith(renderPronProgress(progress));
  } catch { /* the statistics refresh next time */ }
}
