// The chat view (DESIGN.md section 2): the context picker, the welcome with its suggestions, the messages (markdown,
// citations and the Sources sheet, the action row, the English check), streaming with Stop and Retry, the composer
// with push-to-talk and the voice mode buttons, the top bar's menu, and the sidebar's chat list. Voice mode's turns
// arrive through bindLive and are drawn here like typed ones; what is typed meanwhile goes over its connection.

import { api, streamEvents } from "./api.js";
import { $, button, confirmDialog, dialog, emit, fmtDate, h, icon, iconButton, input, menu, navigate, on, plural,
         popover, popoverOpen, remembered, run, setIcon, skeleton, spinner, toast, typing } from "./dom.js";
import { bindLive, live, sendLiveText, startLive, stopLive, toggleLiveMute } from "./live.js";
import { renderMarkdown } from "./markdown.js";
import { openSettings, prefs } from "./settings.js";
import { audioContext, bindMic, makeSplitter, mic, speaker, speakText, stopRecording, toggleRecording,
         warmUpVoice } from "./voice.js";

export const chat = {
  session: null,        // the open session shown in the chat: messages go to it
  viewing: null,        // an ended session shown read-only
  messageCount: 0,
  streaming: false,
  abort: null,          // AbortController of the reply being streamed (Stop)
  sessions: [],         // the sidebar's list, GET /api/sessions
  filter: "",           // the sidebar's search field
  started: false,       // the chat view has been shown once (the open session is resumed only then)
  // The course and topic of the next new chat ("" = none); chosen: the learner picked it (a free conversation too)
  ctx: { courseId: remembered.get("course") || "", topicId: remembered.get("topic") || "",
         chosen: remembered.get("context-chosen") === "1" },
  courses: [],          // GET /api/courses: the picker and its label
  topics: new Map(),    // course id -> its topics
  profile: null,        // the greeting's name
  stick: true,          // new text scrolls into view while the learner is at the bottom
  reading: null,        // the reply being read aloud (its button shows ■)
};

const HINT = "Enter to send · Shift+Enter for a new line · Ctrl+Space to talk";
const VOICE_HINT = "Esc ends voice mode · Ctrl+Space mutes";
const WAIT = "Wait for the reply to finish, or stop it first.";
const CONNECTING = "Voice mode is still connecting. Send it again in a moment.";
const shown = () => chat.session || chat.viewing;
const sameContext = (s, courseId, topicId) =>
  String(s.course_id ?? "") === String(courseId ?? "") && String(s.topic_id ?? "") === String(topicId ?? "");
const sourcesOf = new WeakMap();   // a reply's element -> the passages it was given
// replaceChildren() would turn a null into the text "null": optional parts go through this
const nodes = (...parts) => parts.filter(Boolean);

// ---------------------------------------------------------------- small helpers
let busyTimer = 0;
function setBusy(text) {
  clearTimeout(busyTimer);
  // A quick step never flashes the overlay; a slow one (summarising the last chat) says what is happening
  if (text) busyTimer = setTimeout(() => { $("#busy-text").textContent = text; $("#busy").hidden = false; }, 200);
  else $("#busy").hidden = true;
}

// Chrome sizes the textarea to its content by itself (field-sizing); elsewhere it is measured here (8 lines at most)
const GROWS_BY_ITSELF = CSS.supports("field-sizing", "content");
function autoGrow() {
  if (GROWS_BY_ITSELF) return;
  const t = $("#input");
  t.style.setProperty("--h", "auto");
  const line = parseFloat(getComputedStyle(t).lineHeight) || 22;
  t.style.setProperty("--h", Math.min(t.scrollHeight, line * 8 + 14) + "px");
}

function focusComposer() {
  if (!$("#composer").hidden) $("#input").focus({ preventScroll: true });
}

// Puts text in the message box ("Use it", Continue). A read-only chat has no box: the text starts a new chat.
function fillComposer(text) {
  if (chat.viewing) newChat();
  const t = $("#input");
  t.value = text;
  autoGrow();
  updateChatUi();
  t.focus({ preventScroll: true });
  t.setSelectionRange(text.length, text.length);
}

// The view follows new text while the learner is at the bottom; scrolled up, a "Jump to latest" pill appears
const gapBelow = () => { const box = $("#messages"); return box.scrollHeight - box.scrollTop - box.clientHeight; };
const updateJump = () => { $("#chat-jump").hidden = gapBelow() < 160; };

function onMessagesScroll() {
  chat.stick = gapBelow() < 80;
  updateJump();
}

function scrollDown(force = false) {
  const box = $("#messages");
  if (force) chat.stick = true;
  if (chat.stick) box.scrollTop = box.scrollHeight;
  onMessagesScroll();
}

// Screen readers hear a reply once it is complete, not token by token
function announce(text) {
  $("#chat-announcer").textContent = text ? "Tutor: " + text : "";
}

// ---------------------------------------------------------------- course and topic
const courseOf = id => chat.courses.find(c => String(c.id) === String(id));
const topicOf = (courseId, topicId) => (chat.topics.get(String(courseId)) || []).find(t => String(t.id) === String(topicId));

async function loadTopics(courseId) {
  const list = await api(`/api/courses/${courseId}/topics`);
  chat.topics.set(String(courseId), list);
  return list;
}

function setContext(courseId, topicId, chosen = true) {
  chat.ctx = { courseId: String(courseId ?? ""), topicId: String(topicId ?? ""), chosen };
  remembered.set("course", chat.ctx.courseId);
  remembered.set("topic", chat.ctx.topicId);
  remembered.set("context-chosen", chosen ? "1" : "0");
}

// The courses, and the topics of the next chat's course, for the picker's label. A course or topic deleted on the
// Courses page meanwhile is forgotten.
async function loadContext() {
  chat.courses = await api("/api/courses");
  const { courseId, topicId, chosen } = chat.ctx;
  if (courseId && !courseOf(courseId)) setContext("", "", chosen);
  else if (courseId) {
    const topics = await loadTopics(courseId);
    if (topicId && !topics.some(t => String(t.id) === topicId)) setContext(courseId, "", chosen);
  }
  renderContext();
}

function contextLabel() {
  const s = shown();
  if (s) return s.course_name ? [s.course_name, s.topic_title] : ["Free conversation"];
  const c = courseOf(chat.ctx.courseId);
  if (c) return [c.name, topicOf(c.id, chat.ctx.topicId)?.title];
  return chat.ctx.chosen ? ["Free conversation"] : [];
}

const contextName = (courseId, topicId) =>
  topicOf(courseId, topicId)?.title || courseOf(courseId)?.name || "a free conversation";

function renderContext() {
  const b = $("#chat-context");
  const [course, topic] = contextLabel();
  b.classList.toggle("is-placeholder", !course);
  b.classList.toggle("has-topic", !!topic);
  b.replaceChildren(...nodes(
    h("span", { class: "sr-only" }, "Course and topic: "),
    h("span", { class: "chat-context-course" }, course || "Choose a course and topic"),
    topic ? h("span", { class: "chat-context-sep", "aria-hidden": "true" }, "›") : null,
    topic ? h("span", { class: "chat-context-topic" }, topic) : null,
    icon("chevron-down", "chat-context-caret")));
  b.disabled = chat.streaming;
}

// The picker's choice. With no chat open it is the context of the next one (the session starts with the first
// message); a chat with messages in it is ended first, after asking.
async function chooseContext(courseId, topicId) {
  courseId = String(courseId ?? "");
  topicId = String(topicId ?? "");
  if (chat.streaming) { toast(WAIT); return; }
  const s = chat.session;
  if (s && sameContext(s, courseId, topicId)) return;
  if (s && chat.messageCount && !await confirmDialog({
    title: `End this chat and start one on ${contextName(courseId, topicId)}?`,
    body: "This chat is summarised and saved first, as when you end it.",
    confirmLabel: "End and start" })) return;
  if (s) {
    await startSession(courseId, topicId);
  } else {
    setContext(courseId, topicId);
    if (chat.viewing) newChat();   // an ended chat was on screen: the choice is for a new one
    else updateChatUi();
  }
  focusComposer();
}

// Choices made from the welcome (a suggestion card): the chat on screen is empty, so nothing is asked
async function useContext(courseId, topicId) {
  if (chat.session) {
    if (!sameContext(chat.session, courseId, topicId)) await startSession(courseId, topicId);
    return;
  }
  setContext(courseId, topicId);
  await loadContext();
}

// Course › Topic: a popover with the courses on the left and the chosen course's topics on the right (stacked on
// narrow screens). Clicking a course shows its topics; a topic, "Any topic" or "Free conversation" is the choice.
async function openPicker() {
  if (chat.streaming) { toast(WAIT); return; }
  const anchor = $("#chat-context");
  const s = shown();
  const current = s ? { courseId: String(s.course_id ?? ""), topicId: String(s.topic_id ?? ""), chosen: true } : chat.ctx;
  // Fresh lists: a course or topic may have been added on the Courses page
  chat.topics.clear();
  [chat.courses] = await Promise.all([api("/api/courses"),
    current.courseId ? loadTopics(current.courseId).catch(() => []) : null]);
  let browse = courseOf(current.courseId) ? current.courseId : "";
  let pop = null;

  const coursesList = h("div", { class: "chat-picker-list", role: "group", "aria-labelledby": "chat-picker-courses" });
  const topicsList = h("div", { class: "chat-picker-list", role: "group", "aria-labelledby": "chat-picker-topics" });
  const choose = (courseId, topicId) => run(async () => {
    pop?.close(true);
    await chooseContext(courseId, topicId);
  });
  const item = ({ name, meta, num, badge, isCurrent, browsed, data, onclick }) =>
    h("button", { type: "button", class: "chat-picker-item" + (browsed ? " is-browsed" : ""),
                  "aria-current": isCurrent ? "true" : null, onclick, ...data },
      num !== undefined ? h("span", { class: "chat-picker-num", "aria-hidden": "true" }, num) : null,
      h("span", { class: "chat-picker-text" },
        h("span", { class: "chat-picker-name", dir: "auto" }, name),
        meta ? h("span", { class: "chat-picker-meta" }, meta) : null),
      badge || null,
      isCurrent ? icon("check", "chat-picker-check") : null);

  const drawCourses = () => coursesList.replaceChildren(
    item({ name: "Free conversation", meta: "No course: talk about anything", data: { "data-course": "" },
           isCurrent: !current.courseId && current.chosen, browsed: !browse, onclick: choose("", "") }),
    ...chat.courses.map(c => item({ name: c.name, meta: plural(c.topics, "topic"), data: { "data-course": c.id },
      isCurrent: current.courseId === String(c.id), browsed: browse === String(c.id),
      onclick: run(() => browseCourse(String(c.id), true)) })));

  async function drawTopics() {
    const id = browse;
    if (!id) {
      topicsList.replaceChildren(h("p", { class: "chat-picker-empty" },
        "A free conversation has no course or topic: ask anything, or practise speaking."));
      return;
    }
    let topics = chat.topics.get(id);
    if (!topics) {
      const slow = setTimeout(() => topicsList.replaceChildren(skeleton(3)), 150);
      try { topics = await loadTopics(id); } finally { clearTimeout(slow); }
      if (browse !== id) return;
    }
    topicsList.replaceChildren(...nodes(
      item({ name: "Any topic", meta: `The whole of ${courseOf(id)?.name || "the course"}`, data: { "data-topic": "" },
             isCurrent: current.courseId === id && !current.topicId, onclick: choose(id, "") }),
      ...topics.map((t, i) => item({ num: i + 1, name: t.title, data: { "data-topic": t.id },
        badge: h("span", { class: `badge badge-${t.status}` }, t.status),
        isCurrent: current.courseId === id && current.topicId === String(t.id), onclick: choose(id, String(t.id)) })),
      topics.length ? null : h("p", { class: "chat-picker-empty" }, "No topics yet. ",
        h("button", { type: "button", class: "link-btn", onclick: () => { pop?.close(false); navigate("courses"); } },
          "Add topics in Courses"))));
  }

  async function browseCourse(id, focusTopics) {
    browse = id;
    drawCourses();
    await drawTopics();
    if (focusTopics) topicsList.querySelector(".chat-picker-item")?.focus();
  }

  // Up and down move within a column, right opens the focused course's topics, left goes back to the courses
  const onKey = e => {
    const inCourses = coursesList.contains(document.activeElement);
    const items = [...(inCourses ? coursesList : topicsList).querySelectorAll(".chat-picker-item")];
    const i = items.indexOf(document.activeElement);
    if (i < 0) return;
    let next = null;
    if (e.key === "ArrowDown") next = items[(i + 1) % items.length];
    else if (e.key === "ArrowUp") next = items[(i - 1 + items.length) % items.length];
    else if (e.key === "Home") next = items[0];
    else if (e.key === "End") next = items.at(-1);
    else if (e.key === "ArrowRight" && inCourses && document.activeElement.dataset.course) {
      e.preventDefault();
      run(() => browseCourse(document.activeElement.dataset.course, true))();
      return;
    } else if (e.key === "ArrowLeft" && !inCourses) next = coursesList.querySelector(".is-browsed");
    if (next) { e.preventDefault(); next.focus(); }
  };

  drawCourses();
  await drawTopics();
  pop = popover(anchor, h("div", { class: "chat-picker-body", onkeydown: onKey },
    h("div", { class: "chat-picker-col" }, h("h2", { class: "chat-picker-head", id: "chat-picker-courses" }, "Course"), coursesList),
    h("div", { class: "chat-picker-col" }, h("h2", { class: "chat-picker-head", id: "chat-picker-topics" }, "Topic"), topicsList)),
    { className: "chat-picker", label: "Choose a course and topic" });
  (topicsList.querySelector("[aria-current]") || coursesList.querySelector(".is-browsed"))?.focus({ preventScroll: true });
}

// ---------------------------------------------------------------- the state of the controls
// Send, or Stop while a reply streams; disabled while the box is empty (also on every key typed). In voice mode it is
// there only while the box has text.
function updateSend() {
  const send = $("#btn-send");
  const empty = !$("#input").value.trim();
  send.classList.toggle("is-stop", chat.streaming);
  send.setAttribute("aria-label", chat.streaming ? "Stop the reply" : "Send");
  send.title = chat.streaming ? "Stop the reply" : "Send (Enter)";
  setIcon(send, chat.streaming ? "stop" : "send");
  send.disabled = !chat.streaming && empty;
  send.hidden = live.active && !chat.streaming && empty;
}

function updateChatUi() {
  updateSend();
  const hide = !!chat.viewing;
  const voice = live.active;
  $("#composer").hidden = hide;
  // Voice mode keeps the composer, pared down: "Type", mute and × (End voice mode) at the right, no push-to-talk, no
  // voice mode button and no hint line (it stays as the message box's description for screen readers)
  $("#composer-hint").hidden = hide || voice;
  $("#btn-mic").hidden = voice;
  $("#btn-voice").hidden = voice;
  $("#btn-mute").hidden = !voice;
  $("#btn-voice-end").hidden = !voice;
  $("#input").placeholder = voice ? "Type" : "Message the tutor…";
  // The welcome and the composer sit together in the middle of an empty chat (in voice mode, the orb does)
  $("#view-chat").classList.toggle("is-welcome", !!$("#messages > .chat-empty") && !voice);
  updateMic();
  updateMute();
  renderContext();
  updateJump();   // the content may have been replaced: no "Jump to latest" left over from the last chat
}

function updateReadAloud() {
  const b = $("#btn-read-aloud");
  const on = prefs.readAloud;
  b.setAttribute("aria-pressed", String(on));
  b.title = on ? "Read replies aloud: on (click to turn off)" : "Read replies aloud: off (click to turn on)";
  setIcon(b, on ? "speaker" : "speaker-off");
}

// Push-to-talk (hidden in voice mode: the live microphone is already open, and the mute button stops it)
function updateMic() {
  const b = $("#btn-mic");
  const hint = $("#composer-hint");
  if (live.active) {
    b.classList.remove("recording", "busy");
    hint.classList.remove("is-active", "is-recording");
    hint.textContent = VOICE_HINT;
    return;
  }
  b.classList.toggle("recording", !!mic.recorder);
  b.classList.toggle("busy", mic.busy);
  b.setAttribute("aria-pressed", String(!!mic.recorder));
  b.setAttribute("aria-label", mic.recorder ? "Finish talking" : mic.busy ? "Transcribing" : "Talk");
  b.title = mic.recorder ? "Finish talking (Ctrl+Space)" : mic.busy ? "Writing out what you said…" : "Talk (Ctrl+Space)";
  hint.classList.toggle("is-active", !!mic.recorder || mic.busy);
  hint.classList.toggle("is-recording", !!mic.recorder);
  if (mic.recorder) {
    const secs = Math.floor((performance.now() - mic.startedAt) / 1000);
    hint.textContent = `● ${Math.floor(secs / 60)}:${String(secs % 60).padStart(2, "0")} · Listening… click the mic or Ctrl+Space to finish · Esc to cancel`;
  } else if (mic.busy) {
    hint.textContent = "Writing out what you said…";
  } else {
    hint.textContent = HINT;
  }
}

// Voice mode's mute toggle (Ctrl+Space): pressed while the tutor cannot hear the learner
function updateMute() {
  const b = $("#btn-mute");
  const muted = live.active && live.muted;
  b.setAttribute("aria-pressed", String(muted));
  b.title = muted ? "Unmute (Ctrl+Space)" : "Mute (Ctrl+Space)";
}

// ---------------------------------------------------------------- the welcome
function greeting(name) {
  const hour = new Date().getHours();
  const part = hour >= 5 && hour < 12 ? "morning" : hour >= 12 && hour < 18 ? "afternoon" : "evening";
  return `Good ${part}${name ? `, ${name}` : ""}`;
}

const suggestion = ({ icon: name, step, done, title, text, onclick }) =>
  h("button", { type: "button", class: "chat-suggest" + (done ? " is-done" : ""), onclick: run(onclick) },
    h("span", { class: "chat-suggest-icon", "aria-hidden": "true" }, done ? icon("check") : name ? icon(name) : String(step)),
    h("span", { class: "chat-suggest-text" },
      h("span", { class: "chat-suggest-title" }, done ? h("span", { class: "sr-only" }, "Done: ") : null, title),
      h("span", { class: "chat-suggest-desc", dir: "auto" }, text)));

// The Pronunciation page takes a drill in its route
const openDrill = id => navigate(`pronunciation/${id}`);

// Up to four things to do now, from the plan; on a first run, the setup steps instead
function suggestions({ plan, profile, courses, pron }) {
  if (!profile.name || !courses.length) {
    return [
      suggestion({ step: 1, done: !!profile.name, title: "Tell the tutor about you",
                   text: "Your name, English level and goals, so the lessons fit you.", onclick: () => openSettings() }),
      suggestion({ step: 2, done: courses.length > 0, title: "Add a course",
                   text: "Upload your PDFs or notes: the tutor teaches from them.", onclick: () => navigate("courses") }),
      suggestion({ step: 3, title: "Start talking", text: "No course needed: ask anything, or practise speaking.",
                   onclick: () => { setContext("", ""); updateChatUi(); focusComposer(); } }),
    ];
  }
  const cards = [];
  const last = plan.last_session;
  if (last?.next_step) {
    cards.push(suggestion({ icon: "refresh", title: "Continue", text: last.next_step, onclick: () => continueLast(last) }));
  }
  const next = plan.next_topics[0];
  if (next) {
    cards.push(suggestion({ icon: "book", title: "Next lesson", text: `${next.title} · ${next.course_name}`,
                            onclick: () => startLesson(next) }));
  }
  const due = [plan.due_topics.length ? plural(plan.due_topics.length, "topic") : "",
               plan.due_vocab ? plural(plan.due_vocab, "word") : ""].filter(Boolean);
  if (due.length) cards.push(suggestion({ icon: "cards", title: "Reviews due", text: due.join(" · "), onclick: () => navigate("home") }));
  const weak = pron.sounds.find(s => s.drill && s.accuracy < 0.9);   // weakest first
  cards.push(suggestion({ icon: "waveform", title: "Practise pronunciation",
    text: weak ? `/${weak.ipa}/ is right ${Math.round(weak.accuracy * 100)}% of the time` : "Drills for the sounds Arabic speakers find hardest",
    onclick: () => (weak ? openDrill(weak.drill) : navigate("pronunciation")) }));
  return cards.slice(0, 4);
}

// Continue: the last chat's course and topic, and its next step in the message box, ready to send or change
async function continueLast(last) {
  const row = chat.sessions.find(s => s.ended_at === last.ended_at);
  await useContext(row?.course_id, row?.topic_id);
  fillComposer(`Let's continue from last time. ${last.next_step}`);
}

// Next lesson: a chat on the topic, opened with the standard first message
async function startLesson(t) {
  await useContext(t.course_id, t.id);
  await sendText(`Let's start "${t.title}". Explain the main idea, then check my understanding.`);
}

let welcomeRun = 0;
// The greeting, a line about the tutor and the suggestion cards. Shown again, it refreshes the cards in place.
function renderWelcome() {
  const box = $("#messages");
  let el = box.querySelector(":scope > .chat-empty");
  if (!el) {
    el = h("div", { class: "chat-empty" },
      h("span", { class: "chat-empty-mark", "aria-hidden": "true" }, icon("spark")),
      h("h1", { class: "chat-empty-title" }, greeting(chat.profile?.name)),
      h("p", { class: "chat-empty-lead" },
        "Ask a question, explain an idea in your own words, or ask to be quizzed. Your English is checked as you go."),
      h("div", { class: "chat-suggestions", role: "group", "aria-label": "Suggestions" }));
    box.append(el);
  }
  updateChatUi();
  const cards = el.querySelector(".chat-suggestions");
  const runId = ++welcomeRun;
  const slow = setTimeout(() => {
    if (!cards.children.length) cards.replaceChildren(...Array.from({ length: 4 }, () => h("div", { class: "chat-suggest is-loading", "aria-hidden": "true" })));
  }, 150);
  Promise.all([api("/api/plan"), api("/api/profile"), api("/api/courses"),
               api("/api/pronunciation/progress").catch(() => ({ sounds: [] }))])
    .then(([plan, profile, courses, pron]) => {
      if (runId !== welcomeRun || !el.isConnected) return;
      chat.profile = profile;
      el.querySelector(".chat-empty-title").textContent = greeting(profile.name);
      cards.replaceChildren(...suggestions({ plan, profile, courses, pron }));
    })
    .catch(e => {
      if (runId === welcomeRun && el.isConnected) {
        cards.replaceChildren(h("p", { class: "chat-suggest-error" }, "Suggestions could not be loaded: " + e.message));
      }
    })
    .finally(() => clearTimeout(slow));
}

// ---------------------------------------------------------------- messages
function addMessage(role, text = "") {
  const box = $("#messages");
  if (box.querySelector(":scope > .chat-empty")) {
    box.querySelector(":scope > .chat-empty").remove();
    $("#view-chat").classList.remove("is-welcome");
  }
  const body = h("div", { class: "body", dir: "auto" });
  let el;
  if (role === "user") {
    body.textContent = text;
    el = h("div", { class: "msg user" }, h("span", { class: "sr-only" }, "You said:"), body, h("div", { class: "feedback" }));
  } else {
    body.innerHTML = renderMarkdown(text);
    el = h("div", { class: "msg assistant", "data-text": text },
      h("span", { class: "msg-mark", "aria-hidden": "true" }, icon("spark")),
      h("div", { class: "msg-main" }, h("span", { class: "sr-only" }, "The tutor said:"), body,
        h("div", { class: "msg-actions" })));
  }
  box.append(el);
  return el;
}

// The streaming caret goes at the end of the last paragraph or list item, not on a line of its own
const BLOCKS = /^(P|UL|OL|LI|H3|H4|H5|PRE|CODE)$/;
function appendCaret(body) {
  let at = body;
  while (at.lastElementChild && BLOCKS.test(at.lastElementChild.tagName)) at = at.lastElementChild;
  at.append(h("span", { class: "msg-caret", "aria-hidden": "true" }));
}

function setReply(el, text, done) {
  const body = el.querySelector(".body");
  body.innerHTML = renderMarkdown(text);
  el.dataset.text = text;
  if (done) finishReply(el, sourcesOf.get(el) || []);
  else appendCaret(body);
}

// A complete reply: its citations get the passages' names (a label the tutor made up is marked), and the action row
function finishReply(el, sources = []) {
  el.classList.remove("pending");
  sourcesOf.set(el, sources);
  const unknown = [];
  for (const c of el.querySelectorAll(".body .cite")) {
    const s = sources.find(x => x.label === c.dataset.label);
    c.title = s ? `${s.filename}${s.page ? `, p. ${s.page}` : ""}` : "Not in the material given to the tutor";
    c.classList.toggle("is-unknown", !s);
    if (!s && !unknown.includes(c.dataset.label)) unknown.push(c.dataset.label);
  }
  el.querySelector(".msg-note")?.remove();
  if (unknown.length) {
    el.querySelector(".msg-actions").before(h("p", { class: "msg-note" }, icon("alert"),
      `${unknown.join(", ")} ${unknown.length > 1 ? "are" : "is"} not in your material: check that part yourself.`));
  }
  renderActions(el);
}

// Copy, Read aloud and Sources (n) under a reply; Stop while it streams
function renderActions(el, { streaming = false } = {}) {
  const row = el.querySelector(".msg-actions");
  if (streaming) {
    row.replaceChildren(button("Stop", { variant: "ghost", size: "sm", icon: "stop", class: "msg-stop",
                                         title: "Stop the reply", onclick: stopReply }));
    return;
  }
  const sources = sourcesOf.get(el) || [];
  const reading = chat.reading === el;
  const focused = [...row.children].indexOf(document.activeElement);   // kept when the row is drawn again
  row.replaceChildren(...nodes(
    copyButton(el),
    iconButton(reading ? "stop" : "play", reading ? "Stop reading" : "Read aloud",
               { class: `icon-btn-sm msg-read${reading ? " is-on" : ""}`, onclick: () => toggleRead(el) }),
    sources.length ? h("button", { type: "button", class: "msg-sources", "aria-label": `Sources (${sources.length})`,
                                   title: "The passages from your material the tutor was given",
                                   onclick: () => openSources(el) },
      icon("sources"), h("span", {}, "Sources"), h("span", { class: "msg-count" }, sources.length)) : null,
    el.classList.contains("stopped") ? h("span", { class: "msg-flag" }, "Stopped") : null));
  if (focused >= 0) row.children[Math.min(focused, row.children.length - 1)]?.focus({ preventScroll: true });
}

async function copyText(text) {
  try {
    await navigator.clipboard.writeText(text);
  } catch {
    // The page is not focused or may not write the clipboard: the older way, from a hidden text box
    const opener = document.activeElement;
    const t = h("textarea", { class: "sr-only", readonly: true });
    t.value = text;
    document.body.append(t);
    t.select();
    const ok = document.execCommand("copy");
    t.remove();
    opener?.focus?.({ preventScroll: true });
    if (!ok) throw new Error("Could not copy. Select the text and press Ctrl+C.");
  }
}

function copyButton(el) {
  const b = iconButton("copy", "Copy", { class: "icon-btn-sm msg-copy" });
  let timer = 0;
  b.addEventListener("click", run(async () => {
    // The citation chips' labels mean nothing outside the app
    await copyText((el.dataset.text || "").replace(/\s*\[S\d+\]/g, "").trim());
    clearTimeout(timer);
    setIcon(b, "check");
    b.setAttribute("aria-label", "Copied");
    b.title = "Copied";
    b.classList.add("is-done");
    if (!b.querySelector(".msg-copied")) b.append(h("span", { class: "msg-copied", "aria-hidden": "true" }, "Copied"));
    timer = setTimeout(() => {
      setIcon(b, "copy");
      b.setAttribute("aria-label", "Copy");
      b.title = "Copy";
      b.classList.remove("is-done");
      b.querySelector(".msg-copied")?.remove();
    }, 1600);
  }));
  return b;
}

// Read aloud ▶ / ■. The speaker has no "ended" event, so its queue and playing sounds are watched instead.
function markRead(el, reading) {
  const b = el?.querySelector(".msg-read");
  if (!b) return;
  const label = reading ? "Stop reading" : "Read aloud";
  setIcon(b, reading ? "stop" : "play");
  b.setAttribute("aria-label", label);
  b.title = label;
  b.classList.toggle("is-on", reading);
}

let readTimer = 0;
function setReading(el) {
  const before = chat.reading;
  chat.reading = el;
  clearInterval(readTimer);
  if (before && before !== el) markRead(before, false);
  if (!el) return;
  markRead(el, true);
  let idle = 0;
  readTimer = setInterval(() => {
    idle = speaker.fetching || speaker.queue.length || speaker.sources.length ? 0 : idle + 1;
    if (idle >= 2 || !el.isConnected) setReading(null);
  }, 250);
}

function toggleRead(el) {
  if (chat.reading === el) {
    speaker.stop();
    setReading(null);
    return;
  }
  speakText(el.dataset.text || "");
  setReading(el);
}

// The passages the reply was given, cited ones first and marked; opened from a citation, it scrolls to that one
function openSources(el, label) {
  const sources = sourcesOf.get(el) || [];
  const ordered = [...sources.filter(s => s.cited), ...sources.filter(s => !s.cited)];
  const cited = sources.filter(s => s.cited).length;
  const d = dialog({
    title: "Sources",
    className: "dlg-sheet chat-sources",
    body: [
      h("p", { class: "chat-sources-lead" }, cited
        ? `Passages from your material given to the tutor for this reply. ${plural(cited, "passage")} cited first.`
        : "Passages from your material given to the tutor for this reply. It cited none of them."),
      h("div", { class: "chat-source-list" }, ordered.map(s => h("article", {
        class: "chat-source" + (s.cited ? " is-cited" : ""), "data-label": s.label, tabindex: "-1",
        "aria-label": `Source ${s.label.slice(1)}: ${s.filename}${s.page ? `, page ${s.page}` : ""}` },
        h("header", { class: "chat-source-head" },
          h("span", { class: "chat-source-num", "aria-hidden": "true" }, s.label.slice(1)),
          h("span", { class: "chat-source-file" }, s.filename),
          s.page ? h("span", { class: "chat-source-page" }, `p. ${s.page}`) : null,
          s.cited ? h("span", { class: "badge badge-accent" }, icon("check"), "Cited") : null),
        h("p", { class: "chat-source-excerpt", dir: "auto" }, s.excerpt.trim() + (s.excerpt.length >= 400 ? "…" : ""))))),
    ],
  });
  const target = label && [...d.body.querySelectorAll(".chat-source")].find(x => x.dataset.label === label);
  if (target) {
    target.classList.add("is-target");
    target.scrollIntoView({ block: "start" });
    target.focus({ preventScroll: true });
  }
}

// The English check under a learner's message: a spinner while pending, then "Good English", or the "N corrections"
// card, open (the list and the better phrasing; its header folds it, for that message only); and chips for the words
// and memories it saved
let feedbackSeq = 0;
function renderFeedback(el, fb) {
  const box = el.querySelector(".feedback");
  if (!box) return;
  box.replaceChildren();
  if (!fb) return;
  const chips = h("div", { class: "fb-chips" });
  box.append(chips);
  if (fb === "pending") {
    chips.append(h("span", { class: "fb fb-checking" }, spinner(), "Checking English…"));
    return;
  }
  if (fb.error) {
    chips.append(h("span", { class: "fb fb-muted", title: fb.error }, icon("alert"), "English check unavailable"));
    return;
  }
  const corrections = fb.corrections || [];
  if (corrections.length) {
    const panel = correctionPanel(fb);
    panel.id = `fb-panel-${++feedbackSeq}`;
    const head = h("button", { type: "button", class: "fb-fix", "aria-expanded": "true", "aria-controls": panel.id,
                               title: "Hide the corrections" },
      icon("edit"), h("span", { class: "fb-fix-label" }, plural(corrections.length, "correction")),
      icon("chevron-down", "fb-caret"));
    const card = h("div", { class: "fb-card" }, head, panel);
    head.addEventListener("click", () => {
      panel.hidden = !panel.hidden;
      head.setAttribute("aria-expanded", String(!panel.hidden));
      head.title = panel.hidden ? "Show the corrections" : "Hide the corrections";
      card.classList.toggle("is-folded", panel.hidden);
      if (!panel.hidden) panel.scrollIntoView({ block: "nearest" });
    });
    chips.before(card);
  } else if (/[A-Za-z]{3,}/.test(el.querySelector(".body").textContent)) {
    chips.append(h("span", { class: "fb fb-ok" }, icon("check"), "Good English"));
  }
  for (const w of fb.saved_words || []) {
    chips.append(h("span", { class: "fb fb-saved", title: w.meaning ? `${w.word}: ${w.meaning}` : w.word },
      "Saved: ", h("em", { dir: "auto" }, w.word)));
  }
  for (const m of fb.saved_memories || []) {
    chips.append(h("span", { class: "fb fb-saved", title: m.content }, "Remembered: ", h("em", { dir: "auto" }, m.content)));
  }
}

function correctionPanel(fb) {
  const better = fb.better_phrasing;
  return h("div", { class: "fb-panel" },
    h("ul", { class: "fb-list" }, fb.corrections.map(x => h("li", { class: "fb-item" },
      h("div", { class: "fb-change" },
        h("del", { class: "fb-wrong", dir: "auto" }, x.wrong),
        h("span", { class: "fb-arrow", "aria-hidden": "true" }, "→"),
        h("ins", { class: "fb-right", dir: "auto" }, x.right)),
      x.rule ? h("div", { class: "fb-rule" }, x.rule.replaceAll("_", " ")) : null,
      x.explanation ? h("p", { class: "fb-explain" }, x.explanation) : null))),
    better ? h("div", { class: "fb-better" },
      h("div", { class: "fb-better-label" }, "More natural"),
      h("p", { class: "fb-better-text", dir: "auto" }, better),
      h("div", { class: "fb-better-actions" },
        button("Use it", { size: "sm", variant: "primary", title: "Put this sentence in the message box",
                           onclick: () => fillComposer(better) }),
        button("Listen", { size: "sm", variant: "ghost", icon: "play", onclick: () => speakText(better) }))) : null);
}

// A turn that failed: what went wrong, and Retry (the same text again)
const errorCard = (title, message, onRetry) =>
  h("div", { class: "chat-error", role: "alert" },
    icon("alert"),
    h("p", { class: "chat-error-text" }, h("strong", {}, title), " ", message),
    button("Retry", { size: "sm", icon: "refresh", class: "chat-retry", onclick: onRetry }));

// The card under a chat that has ended: what was learned, and what to do next
function endedCard({ session: s, new_memories: added = [], retired_memories: retired = [], error }, withActions = true) {
  return h("section", { class: "ended" + (withActions ? "" : " is-compact"),
                        "aria-label": withActions ? "This chat has ended" : "Previous chat saved" },
    h("div", { class: "ended-title" },
      h("span", { class: "ended-icon", "aria-hidden": "true" }, icon("check")),
      h("span", {}, withActions ? "This chat has ended" : "Previous chat saved"),
      s.ended_at ? h("span", { class: "ended-date" }, fmtDate(s.ended_at)) : null),
    s.summary ? h("p", { class: "ended-summary", dir: "auto" }, s.summary)
              : h("p", { class: "ended-summary muted" }, "This chat was too short to summarise."),
    s.next_step ? h("div", { class: "ended-next" },
      h("span", { class: "ended-label" }, "Next step"), h("p", { dir: "auto" }, s.next_step)) : null,
    added.length ? h("p", { class: "ended-meta" }, icon("brain"), "Remembered: " + added.map(m => m.content).join(" · ")) : null,
    retired.length ? h("p", { class: "ended-meta" }, "No longer true, turned off: " + retired.map(m => m.content).join(" · ")) : null,
    error ? h("p", { class: "ended-meta text-warn" }, error) : null,
    withActions ? h("div", { class: "ended-actions" },
      s.id && !s.discarded ? button("Continue this chat", { icon: "refresh", onclick: run(() => continueChat(s.id)) }) : null,
      button("New chat", { variant: "primary", icon: "plus", onclick: () => newChat() })) : null);
}

// ---------------------------------------------------------------- sessions
export async function openSession(id) {
  if (chat.streaming) { toast(WAIT); return false; }
  stopLive();
  if (mic.recorder) stopRecording(true);
  const { session, messages } = await api(`/api/sessions/${id}`);
  const ended = !!session.ended_at;
  Object.assign(chat, { session: ended ? null : session, viewing: ended ? session : null,
                        messageCount: messages.length, started: true });
  speaker.stop();
  setReading(null);
  announce("");
  $("#messages").replaceChildren();
  for (const m of messages) {
    const el = addMessage(m.role, m.content);
    if (m.role === "assistant") finishReply(el, m.sources);
    else if (m.feedback) renderFeedback(el, m.feedback);
  }
  if (ended) $("#messages").append(endedCard({ session }));
  else if (!messages.length) renderWelcome();
  updateChatUi();
  scrollDown(true);
  renderSessions();
  return true;
}

export async function startSession(courseId, topicId, prefill = "") {
  if (chat.streaming) return;
  stopLive();
  const open = chat.sessions.find(s => !s.ended_at);
  setBusy(open?.message_count >= 2 || chat.messageCount >= 2
    ? "Saving what you learned in the last chat…" : "Starting a new chat…");
  try {
    const res = await api("/api/sessions", { method: "POST", json: {
      course_id: courseId ? Number(courseId) : null, topic_id: topicId ? Number(topicId) : null } });
    Object.assign(chat, { session: res.session, viewing: null, messageCount: 0, started: true });
    setContext(courseId, topicId);
    speaker.stop();
    setReading(null);
    const box = $("#messages");
    box.replaceChildren();
    for (const c of res.closed) if (c.session.summary) box.append(endedCard(c, false));
    renderWelcome();
    scrollDown(true);
    if (prefill) fillComposer(prefill);
  } catch (e) {
    toast(e.message, "error");
  } finally {
    setBusy(null);
    updateChatUi();
    refreshSessions();
  }
}

export async function endSession() {
  if (!chat.session || chat.streaming) return;
  stopLive();
  setBusy("Summarising this chat and updating your memory…");
  try {
    const res = await api(`/api/sessions/${chat.session.id}/end`, { method: "POST" });
    chat.session = null;
    chat.messageCount = 0;
    if (res.session.discarded) {   // nothing was said: nothing to save
      $("#messages").replaceChildren();
      renderWelcome();
    } else {
      chat.viewing = res.session;
      $("#messages").append(endedCard(res));
    }
    updateChatUi();
    scrollDown(true);
  } catch (e) {
    toast(e.message, "error");
  } finally {
    setBusy(null);
    updateChatUi();
    refreshSessions();
    emit("plan");
  }
}

// A blank chat. A session still open on the server is ended (and summarised) when the next one starts.
export function newChat() {
  if (chat.streaming) { toast(WAIT); return; }
  stopLive();
  if (mic.recorder) stopRecording(true);
  speaker.stop();
  setReading(null);
  Object.assign(chat, { session: null, viewing: null, messageCount: 0, started: true });
  announce("");
  $("#messages").replaceChildren();
  renderSessions();
  navigate("chat");   // showChat() draws the welcome and refreshes the courses for the picker
}

// Continue an ended chat: the open one is ended the normal way first (summary, memory)
async function continueChat(id) {
  if (chat.streaming) { toast(WAIT); return; }
  stopLive();
  const open = chat.sessions.find(s => !s.ended_at && s.id !== id);
  setBusy(open?.message_count >= 2 ? "Saving what you learned in the open chat…" : "Opening the chat…");
  try {
    await api(`/api/sessions/${id}/reopen`, { method: "POST" });
  } finally {
    setBusy(null);
  }
  await openSession(id);
  navigate("chat");
  refreshSessions();
  $("#input").focus();
}

async function deleteChat(s) {
  const row = chat.sessions.find(x => x.id === s.id) || s;
  const ok = await confirmDialog({
    title: "Delete this chat?",
    body: `"${row.title || "New chat"}" and all its messages will be deleted. ` +
          "Words, mistakes and memories saved from it stay.",
    confirmLabel: "Delete", danger: true });
  if (!ok) return;
  let gone = false;
  if (!row.ended_at) {
    // The server keeps the open chat: it is ended first, as End session does
    if (chat.streaming) { toast(WAIT); return; }
    stopLive();
    setBusy("Ending this chat…");
    try {
      gone = (await api(`/api/sessions/${row.id}/end`, { method: "POST" })).session.discarded;   // nothing in it
    } finally {
      setBusy(null);
    }
  }
  if (!gone) await api(`/api/sessions/${row.id}`, { method: "DELETE" });
  if (shown()?.id === row.id) {
    Object.assign(chat, { session: null, viewing: null, messageCount: 0 });
    $("#messages").replaceChildren();
    renderWelcome();
  }
  toast("Chat deleted");
  await refreshSessions();
}

async function renameChat(s) {
  const row = chat.sessions.find(x => x.id === s.id) || s;
  const field = input({ value: row.title || "", maxlength: "120", autocomplete: "off", "aria-label": "Chat title" });
  const d = dialog({
    title: "Rename chat",
    className: "dlg-rename",
    body: h("form", { onsubmit: e => { e.preventDefault(); d.close(field.value); } }, field),
    actions: [button("Cancel", { onclick: () => d.close(null) }),
              button("Save", { variant: "primary", onclick: () => d.close(field.value) })],
  });
  field.focus();
  field.select();
  const title = (await d.closed)?.trim();
  if (!title || title === row.title) return;
  const saved = await api(`/api/sessions/${s.id}`, { method: "PATCH", json: { title } });
  for (const x of [chat.session, chat.viewing]) if (x?.id === s.id) x.title = saved.title;
  await refreshSessions();
}

// ---------------------------------------------------------------- the sidebar's chat list
export async function refreshSessions() {
  try {
    chat.sessions = await api("/api/sessions");
  } catch (e) {
    $("#sb-chats").replaceChildren(h("p", { class: "sb-empty" }, "Chats could not be loaded: " + e.message));
    return;
  }
  renderSessions();
}

function dayGroup(iso) {
  const day = new Date(iso);
  day.setHours(0, 0, 0, 0);
  const today = new Date();
  today.setHours(0, 0, 0, 0);
  const days = Math.round((today - day) / 86400000);
  return days <= 0 ? "Today" : days === 1 ? "Yesterday" : days <= 7 ? "Previous 7 days" : "Older";
}

function renderSessions() {
  const box = $("#sb-chats");
  const q = chat.filter.trim().toLowerCase();
  const rows = chat.sessions.filter(s => !q || `${s.title || "New chat"} ${s.course_name || ""}`.toLowerCase().includes(q));
  if (!rows.length) {
    box.replaceChildren(h("p", { class: "sb-empty" }, q ? "No chats match." : "Your chats will appear here"));
    return;
  }
  const groups = new Map();
  for (const s of rows) {
    const g = dayGroup(s.started_at);
    if (!groups.has(g)) groups.set(g, []);
    groups.get(g).push(s);
  }
  const current = shown()?.id;
  box.replaceChildren(...[...groups].map(([label, list]) => h("div", { class: "sb-group" },
    h("div", { class: "sb-group-label" }, label),
    h("div", { role: "list" }, list.map(s => sessionRow(s, s.id === current))))));
}

function sessionRow(s, active) {
  const open = !s.ended_at;
  const row = h("div", { class: "sb-chat" + (active ? " is-active" : "") + (open ? " is-open" : ""), role: "listitem",
                         "data-id": s.id });
  row.append(
    h("button", { type: "button", class: "sb-chat-open", "aria-current": active ? "true" : null,
                  title: s.title || "New chat", onclick: run(() => openFromSidebar(s.id)) },
      h("span", { class: "sb-chat-title" },
        open ? h("span", { class: "sb-chat-dot", title: "Open chat" }, h("span", { class: "sr-only" }, "Open: ")) : null,
        h("span", { class: "sb-chat-text", dir: "auto" }, s.title || "New chat")),
      h("span", { class: "sb-chat-meta" }, s.course_name || "Free conversation")),
    iconButton("more", "Chat options", { class: "icon-btn-sm sb-chat-more", "aria-haspopup": "menu",
                                         onclick: e => rowMenu(e.currentTarget, s, row) }));
  return row;
}

async function openFromSidebar(id) {
  if (await openSession(id)) navigate("chat");
}

function rowMenu(anchor, s, row) {
  menu(anchor, [
    { label: "Rename", icon: "edit", onSelect: () => renameInline(row, s) },
    s.ended_at ? { label: "Continue this chat", icon: "refresh", onSelect: () => continueChat(s.id) } : null,
    "-",
    { label: "Delete", icon: "trash", danger: true, onSelect: () => deleteChat(s) },
  ].filter(Boolean), { label: "Chat options", placement: "bottom-start" });
}

function renameInline(row, s) {
  const field = h("input", { type: "text", class: "input sb-chat-rename", value: s.title || "", maxlength: "120",
                             "aria-label": "Chat title" });
  let done = false;
  const finish = run(async save => {
    if (done) return;
    done = true;
    const title = field.value.trim();
    try {
      if (save && title && title !== s.title) {
        const saved = await api(`/api/sessions/${s.id}`, { method: "PATCH", json: { title } });
        for (const x of [chat.session, chat.viewing]) if (x?.id === s.id) x.title = saved.title;
      }
    } finally {
      await refreshSessions();
    }
  });
  field.addEventListener("keydown", e => {
    if (e.key === "Enter") { e.preventDefault(); finish(true); }
    else if (e.key === "Escape") { e.preventDefault(); e.stopPropagation(); finish(false); }
  });
  field.addEventListener("blur", () => finish(true));
  row.querySelector(".sb-chat-open").replaceWith(field);
  field.focus();
  field.select();
}

// The top bar's ⋯
function chatMenu(anchor) {
  const s = shown();
  menu(anchor, [
    { label: "End session", icon: "check", disabled: !chat.session || chat.streaming, onSelect: endSession },
    { label: "Rename", icon: "edit", disabled: !s, onSelect: () => renameChat(s) },
    "-",
    { label: "Delete chat", icon: "trash", danger: true, disabled: !s || chat.streaming, onSelect: () => deleteChat(s) },
  ], { label: "Chat options" });
}

// ---------------------------------------------------------------- sending
async function submitMessage(spoken = false) {
  const t = $("#input");
  const text = t.value.trim();
  if (!text || chat.streaming || chat.viewing) return;
  if (live.active) {
    // Voice mode: over its connection, answered aloud; the turn is drawn when the server takes it (user_final)
    if (!sendLiveText(text)) { toast(CONNECTING); return; }
    // Send goes away with the text: when it was clicked, the focus stays in the box instead of being lost
    if (document.activeElement === $("#btn-send")) t.focus({ preventScroll: true });
    t.value = "";
    autoGrow();
    updateSend();
    return;
  }
  if (prefs.readAloud) audioContext();   // unlock audio while we are inside the user's click or key press
  if (!chat.session) {
    await startSession(chat.ctx.courseId, chat.ctx.topicId);
    if (!chat.session) return;
  }
  t.value = "";
  autoGrow();
  // Spoken messages, or replies that will be read aloud, get short conversational answers instead of lists
  await sendMessage(text, spoken || prefs.readAloud);
}

// A message that is not typed (a suggestion card)
async function sendText(text) {
  if (chat.streaming || chat.viewing) return;
  if (prefs.readAloud) audioContext();
  if (!chat.session) {
    await startSession(chat.ctx.courseId, chat.ctx.topicId);
    if (!chat.session) return;
  }
  await sendMessage(text, prefs.readAloud);
}

async function sendMessage(text, voice = false) {
  const controller = new AbortController();
  chat.streaming = true;
  chat.abort = controller;
  speaker.stop();
  setReading(null);
  announce("");
  const firstTurn = chat.messageCount === 0;
  const userEl = addMessage("user", text);
  const tutorEl = addMessage("assistant", "");
  tutorEl.classList.add("pending");
  appendCaret(tutorEl.querySelector(".body"));
  renderActions(tutorEl, { streaming: true });
  updateChatUi();
  scrollDown(true);
  let reply = "";
  let failed = false;
  const splitter = prefs.readAloud ? makeSplitter(s => speaker.enqueue(s)) : null;
  // The composer opens again as soon as the reply is complete: the English check that follows on the same
  // stream can take several seconds, and its result goes to this message's own element (userEl) whenever it
  // arrives. Released once only, so a stream that ends later cannot unlock a newer message's stream.
  let released = false;
  const release = () => {
    if (released) return;
    released = true;
    chat.streaming = false;
    if (chat.abort === controller) chat.abort = null;
    updateChatUi();
    // Back to the box, unless the learner has moved on to something else meanwhile (the sidebar, a page)
    const at = document.activeElement;
    if (!at || at === document.body || $("#view-chat").contains(at)) focusComposer();
  };
  // The reply failed: an inline card under the turn says why and offers Retry. With nothing said, the server
  // dropped the question, so Retry asks it again; a reply cut short is kept (as the server keeps it).
  const fail = message => {
    failed = true;
    if (splitter) speaker.stop();
    let after = userEl;
    if (reply) {
      reply += " …";
      setReply(tutorEl, reply, true);
      chat.messageCount += 2;
      after = tutorEl;
    } else {
      tutorEl.remove();
    }
    const card = errorCard(reply ? "The reply was cut short." : "The tutor could not answer.", message, run(async () => {
      if (chat.streaming) { toast(WAIT); return; }
      if (!chat.session) { toast("This chat has ended. Start a new chat to ask again."); return; }
      // In voice mode the question goes over its connection (one conversation at a time), and comes back as a turn
      if (live.active && !sendLiveText(text)) { toast(CONNECTING); return; }
      card.remove();
      if (!reply) userEl.remove();
      if (!live.active) await sendMessage(text, voice);
    }));
    after.after(card);
    scrollDown();
  };

  const handle = ev => {
    if (ev.type === "token") {
      reply += ev.text;
      setReply(tutorEl, reply, false);
      splitter?.push(ev.text);
      scrollDown();
    } else if (ev.type === "reply_done") {
      splitter?.flush();
      sourcesOf.set(tutorEl, ev.sources || []);
      setReply(tutorEl, reply, true);
      renderFeedback(userEl, "pending");
      chat.messageCount += 2;
      announce(reply);
      if (splitter) setReading(tutorEl);
      emit("status-refresh");
      release();
      scrollDown();
      if (firstTurn) refreshSessions();   // the chat now has a title
    } else if (ev.type === "feedback") {
      renderFeedback(userEl, ev.feedback);
      scrollDown();
      emit("plan");
    } else if (ev.type === "error") {
      if (released) renderFeedback(userEl, { error: ev.message });   // the reply is complete: the check failed
      else fail(ev.message);
    }
  };

  try {
    for await (const ev of streamEvents(`/api/sessions/${chat.session.id}/messages`, { text, voice },
                                        { signal: controller.signal })) {
      handle(ev);
    }
    if (!released && !failed) fail("The connection closed before the reply was complete.");
  } catch (e) {
    if (e.name === "AbortError") {
      // Stop: the server keeps what was already said (with " …"), or drops the question if nothing was
      speaker.stop();
      if (!released) {
        if (!reply) {
          userEl.remove();
          tutorEl.remove();
          const t = $("#input");
          if (!t.value.trim()) { t.value = text; autoGrow(); }
        } else {
          reply += " …";
          tutorEl.classList.add("stopped");
          setReply(tutorEl, reply, true);
          chat.messageCount += 2;
          if (firstTurn) refreshSessions();
        }
      }
    } else if (released) {
      renderFeedback(userEl, { error: e.message });
    } else if (!failed) {
      fail(e.message);
    }
  } finally {
    tutorEl.classList.remove("pending");
    release();
  }
}

function stopReply() {
  chat.abort?.abort();
}

async function ensureSession() {
  if (!chat.session) await startSession(chat.ctx.courseId, chat.ctx.topicId);
  return chat.session?.id ?? null;
}

// ---------------------------------------------------------------- the chat view
export async function showChat() {
  if (!chat.started) {
    chat.started = true;
    // The open session (if any) is resumed when the app opens
    const sessions = await api("/api/sessions");
    chat.sessions = sessions;
    const open = sessions.find(s => !s.ended_at);
    if (open && !chat.session) await openSession(open.id);
    else if (!$("#messages").children.length) renderWelcome();
  } else if (!$("#messages").children.length || $("#messages > .chat-empty")) {
    renderWelcome();   // drawn, or its suggestions refreshed (a review may have been done meanwhile)
  }
  await loadContext();
  updateChatUi();
  focusComposer();
  warmUpVoice();
}

export function initChat() {
  $("#chat-context").addEventListener("click", run(openPicker));
  $("#composer").addEventListener("submit", e => {
    e.preventDefault();
    if (chat.streaming) stopReply();
    else run(submitMessage)();
  });
  const t = $("#input");
  t.addEventListener("keydown", e => {
    if (e.key === "Enter" && !e.shiftKey && !e.isComposing) { e.preventDefault(); run(submitMessage)(); }
  });
  t.addEventListener("input", () => { autoGrow(); updateSend(); });
  $("#btn-mic").addEventListener("click", () => toggleRecording());
  $("#btn-mute").addEventListener("click", () => toggleLiveMute());
  $("#btn-voice").addEventListener("click", run(startLive));
  $("#btn-voice-end").addEventListener("click", () => stopLive());
  $("#btn-read-aloud").addEventListener("click", () => {
    prefs.readAloud = !prefs.readAloud;
    if (!prefs.readAloud) speaker.stop();
  });
  on("prefs", updateReadAloud);
  updateReadAloud();
  on("profile", p => {
    chat.profile = p;
    const g = $("#messages .chat-empty-title");
    if (g) g.textContent = greeting(p?.name);
  });
  $("#btn-chat-menu").addEventListener("click", e => chatMenu(e.currentTarget));
  $("#sb-search").addEventListener("input", e => { chat.filter = e.target.value; renderSessions(); });

  const box = $("#messages");
  box.addEventListener("scroll", onMessagesScroll, { passive: true });
  // A citation chip opens the Sources sheet on its passage
  box.addEventListener("click", e => {
    const c = e.target.closest(".cite");
    const el = c?.closest(".msg");
    if (el && (sourcesOf.get(el) || []).some(s => s.label === c.dataset.label)) openSources(el, c.dataset.label);
  });
  $("#chat-jump").addEventListener("click", () => scrollDown(true));

  bindMic({
    blocked: () => chat.streaming || live.active,   // in voice mode the live microphone is already open
    sessionId: () => chat.session?.id,
    onChange: updateMic,
    onLevel: level => $("#btn-mic").style.setProperty("--level", level.toFixed(2)),
    onText: text => {
      const box = $("#input");
      box.value = (box.value.trim() ? box.value.trim() + " " : "") + text;
      autoGrow();
      updateChatUi();
      if (prefs.autoSend) run(submitMessage)(true);
      else box.focus();
    },
  });
  // Voice mode's turns are messages like typed ones, drawn by the same functions. When it ends, an English check
  // that can no longer arrive (the connection is closed) is not left pending.
  const liveTurns = new Set();
  const settleLiveTurns = () => {
    for (const user of liveTurns) if (user.querySelector(".fb-checking")) renderFeedback(user, null);
    liveTurns.clear();
  };
  bindLive({
    busy: () => chat.streaming,
    ensureSession,
    hasMessages: () => !!$("#messages .msg"),
    addTurn: text => {
      const user = addMessage("user", text);
      const tutor = addMessage("assistant", "");
      tutor.classList.add("pending");
      appendCaret(tutor.querySelector(".body"));
      liveTurns.add(user);
      announce("");
      return { user, tutor };
    },
    setReply,
    replyDone: (t, ev) => {
      sourcesOf.set(t.tutor, ev.sources || []);
      setReply(t.tutor, t.reply, true);
      renderFeedback(t.user, "pending");
      chat.messageCount += 2;
      announce(t.reply);
    },
    replyCut: () => { chat.messageCount += 2; },   // the server keeps what was said, as for a typed Stop
    dropTurn: t => {
      t.user?.remove();
      t.tutor?.remove();
      liveTurns.delete(t.user);
    },
    feedback: (user, fb) => {
      renderFeedback(user, fb);
      emit("plan");
    },
    scrollDown,
    onActive: active => {
      // The button that ended or started it (or the mic, or mute) disappears: the focus goes to the one that takes
      // its place
      const gone = active ? ["btn-voice", "btn-mic"] : ["btn-voice-end", "btn-mute"];
      const toggle = gone.includes(document.activeElement?.id);
      if (!active) settleLiveTurns();
      updateChatUi();
      if (toggle) $(active ? "#btn-voice-end" : "#btn-voice").focus({ preventScroll: true });
      if (!active) {
        // Its only turn was dropped (the learner talked over it): the empty chat greets again
        if (!$("#messages .msg, #messages > .chat-empty") && chat.session) renderWelcome();
        refreshSessions();
      }
    },
    onMuted: updateMute,
  });

  // Voice mode handles its keys first (js/live.js: Ctrl+Shift+Space, and while it is on, Ctrl+Space and Esc); these
  // are the chat's, voice mode or not
  document.addEventListener("keydown", e => {
    if ($("#view-chat").hidden || e.defaultPrevented) return;
    if (document.querySelector("dialog[open]") || popoverOpen()) return;   // they handle their own keys
    if (e.ctrlKey && !e.shiftKey && e.code === "Space") { e.preventDefault(); if (!live.active) toggleRecording(); }
    else if (e.key === "Escape") {
      if (mic.recorder) stopRecording(true);
      else { speaker.stop(); setReading(null); }
    } else if (e.key === "/" && !typing(e) && !e.ctrlKey && !e.altKey && !e.metaKey && !$("#composer").hidden) {
      e.preventDefault();
      $("#input").focus();
    } else if (e.ctrlKey && !e.shiftKey && !e.altKey && e.key.toLowerCase() === "k") {
      e.preventDefault();
      run(openPicker)();
    }
  });
  updateMic();
  updateChatUi();
}
