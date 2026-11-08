// Settings: the theme and the voice preferences kept in this browser, and the settings dialog (DESIGN.md section 8):
// tabs General / Voice / Data. Every field is saved on its own as soon as it changes (PUT /api/profile), with a
// brief "Saved" chip; an invalid value is explained under its field and not sent. General also links to the
// keyboard shortcuts (js/shortcuts.js).

import { api } from "./api.js";
import { $$, button, dialog, emit, h, icon, iconButton, input, remembered, run, select, spinner, textarea,
         toast } from "./dom.js";
import { openShortcuts } from "./shortcuts.js";
import { speaker } from "./voice.js";

// ---------------------------------------------------------------- theme
const THEMES = [["system", "System", "settings"], ["light", "Light", "sun"], ["dark", "Dark", "moon"]];

export const theme = {
  get() {
    const t = remembered.get("theme");
    return THEMES.some(([v]) => v === t) ? t : "system";
  },
  set(t) {
    remembered.set("theme", t);
    applyTheme(t);
    emit("theme", t);
  },
};

// <html data-theme="light|dark">; no attribute follows the system (the inline script in index.html does the
// same before the first paint, so the page never flashes the wrong colours)
export function applyTheme(t = theme.get()) {
  if (t === "light" || t === "dark") document.documentElement.dataset.theme = t;
  else delete document.documentElement.dataset.theme;
}

// ---------------------------------------------------------------- preferences kept in this browser
export const prefs = {
  get readAloud() { return remembered.get("readAloud") !== "0"; },
  set readAloud(v) { remembered.set("readAloud", v ? "1" : "0"); emit("prefs"); },
  get autoSend() { return remembered.get("autoSend") !== "0"; },
  set autoSend(v) { remembered.set("autoSend", v ? "1" : "0"); emit("prefs"); },
};

// ---------------------------------------------------------------- choices
const LEVELS = ["A2", "B1", "B2", "C1", "C2"];
const TEACHING = [
  ["english", "English only (Arabic only when I ask)"],
  ["english_with_arabic_support", "English, plus short Arabic help when I'm stuck"],
  ["arabic", "Arabic explanations, English terms"],
];
const PATIENCE = [["short", "Short"], ["normal", "Normal"], ["long", "Long"]];
const TABS = [["general", "General", "settings"], ["voice", "Voice", "speaker"], ["data", "Data", "sources"]];
const SAMPLE = "Hello! I'm your tutor. Today we'll learn how habits form, step by step.";
const NAME_MAX = 60;
const TEXT_MAX = 2000;   // the server keeps the first 2,000 characters of a field

let seq = 0;
const uid = prefix => `set-${prefix}-${++seq}`;

// ---------------------------------------------------------------- field building blocks
// A "Saved" chip that shows for a moment after a change is stored
function savedChip() {
  const el = h("span", { class: "set-saved", hidden: true }, icon("check"), "Saved");
  let timer = 0;
  return {
    el,
    show() {
      el.hidden = false;
      el.classList.remove("is-shown");
      void el.offsetWidth;   // restart the fade-in when it was already showing
      el.classList.add("is-shown");
      clearTimeout(timer);
      timer = setTimeout(() => { el.hidden = true; }, 1800);
    },
  };
}

// A labelled setting: label + "Saved" chip, the control, a hint and the inline error.
// group: the control is a radio group (labelled by aria-labelledby instead of <label for>);
// display: what is shown in place of the control when it sits in a row with other things (a button, a value).
function setting(label, control, { hint, group = false, display = control } = {}) {
  const id = control.id || (control.id = uid("f"));
  const labelId = `${id}-label`;
  const chip = savedChip();
  const hintEl = hint ? h("p", { class: "set-hint", id: `${id}-hint` }, hint) : null;
  const err = h("p", { class: "set-error", id: `${id}-err`, role: "alert", hidden: true });
  const described = () => [hintEl?.id, err.hidden ? null : err.id].filter(Boolean).join(" ");
  const sync = () => {
    const ids = described();
    if (ids) control.setAttribute("aria-describedby", ids);
    else control.removeAttribute("aria-describedby");
  };
  if (group) control.setAttribute("aria-labelledby", labelId);
  sync();
  const el = h("div", { class: "set-field" },
    h("div", { class: "set-label-row" },
      group ? h("span", { class: "set-label", id: labelId }, label) : h("label", { class: "set-label", id: labelId, for: id }, label),
      chip.el),
    display, hintEl, err);
  // A live region outside the chip announces the save (the chip itself is hidden most of the time)
  const say = h("span", { class: "sr-only", role: "status" });
  el.append(say);
  return {
    el, control,
    saved() { chip.show(); say.textContent = ""; setTimeout(() => { say.textContent = `${label}: saved`; }, 50); },
    error(text) {
      err.textContent = text;
      err.hidden = !text;
      if (group) control.toggleAttribute("aria-invalid", !!text);
      else if (text) control.setAttribute("aria-invalid", "true");
      else control.removeAttribute("aria-invalid");
      sync();
    },
  };
}

// An on/off switch with its label and hint (the switch is the control; the whole row is its label)
function switchSetting(label, checked, hint) {
  const id = uid("sw");
  const box = h("input", { type: "checkbox", class: "switch", role: "switch", id, checked: !!checked });
  const chip = savedChip();
  const hintEl = hint ? h("p", { class: "set-hint", id: `${id}-hint` }, hint) : null;
  if (hintEl) box.setAttribute("aria-describedby", hintEl.id);
  const err = h("p", { class: "set-error", role: "alert", hidden: true });
  const say = h("span", { class: "sr-only", role: "status" });
  const el = h("div", { class: "set-switch" },
    h("div", { class: "set-switch-text" },
      h("div", { class: "set-label-row" }, h("label", { class: "set-label", for: id }, label), chip.el),
      hintEl, err),
    box, say);
  return {
    el, control: box,
    saved() { chip.show(); say.textContent = ""; setTimeout(() => { say.textContent = `${label}: ${box.checked ? "on" : "off"}`; }, 50); },
    error(text) { err.textContent = text; err.hidden = !text; },
  };
}

// Radio buttons drawn as a segmented control
function segmented(name, options, current, label) {
  return h("div", { class: "segmented set-segmented", role: "radiogroup", "aria-label": label },
    options.map(([value, text, iconName]) => h("label", { class: "segmented-item" },
      h("input", { type: "radio", name, value, checked: value === current }),
      iconName ? icon(iconName) : null, h("span", {}, text))));
}

// ---------------------------------------------------------------- saving a profile field
// read(): the control's value as the server stores it; check(value): a problem to show, or nothing.
// Returns the field with flush(), which saves a change the "change" event has not delivered (the dialog closing
// while a text box still has the focus).
function profileField(state, key, f, { read = c => c.value, check, same = (a, b) => a === b } = {}) {
  let saved = String(state.profile[key] ?? "");
  let pending = null;
  const commit = async ({ quiet = false } = {}) => {
    const value = read(f.control);
    if (same(value, saved)) { f.error(""); return; }
    const problem = check?.(value);
    if (problem) {
      f.error(problem);
      if (quiet) toast(`Not saved: ${problem}`, "error");
      return;
    }
    // One request at a time per field: a second change waits, then saves the latest value
    if (pending) { await pending.catch(() => {}); return commit({ quiet }); }   // its own error is shown already
    pending = api("/api/profile", { method: "PUT", json: { [key]: value } });
    try {
      state.profile = await pending;
      saved = String(state.profile[key] ?? "");
      f.error("");
      f.saved();
      emit("profile", state.profile);
    } catch (e) {
      f.error(e.message);
      if (quiet) toast(`Not saved: ${e.message}`, "error");
    } finally {
      pending = null;
    }
  };
  f.control.addEventListener("change", run(() => commit()));
  return { ...f, flush: () => run(commit)({ quiet: true }) };
}

const lengthCheck = (max, what) => v => (v.length > max ? `${what} must be ${max} characters or fewer (now ${v.length}).` : null);
const checkedRadio = group => group.querySelector("input:checked")?.value ?? "";

// ---------------------------------------------------------------- the dialog
let open = null;   // the open dialog, or true while it is being prepared (a second Ctrl+, must not open another)

export async function openSettings(tab = "general") {
  if (open) return open;
  open = true;
  let profile;
  try { profile = await api("/api/profile"); } catch (e) { open = null; toast(e.message, "error"); return null; }
  const state = { profile, fields: [], cleanup: [] };

  const panels = {
    general: generalPanel(state),
    voice: voicePanel(state),
    data: dataPanel(state),
  };

  // Tabs: on the left (a vertical list), stacked above the panel on narrow screens; arrow keys move between them
  const tabs = TABS.map(([id, label, iconName]) => h("button", {
    type: "button", role: "tab", class: "set-tab", id: `set-tab-${id}`, "aria-controls": `set-panel-${id}`,
    "aria-selected": "false", tabindex: "-1", onclick: () => choose(id, true),
  }, icon(iconName), h("span", {}, label)));
  const panelEls = TABS.map(([id]) => h("div", { class: "set-panel", role: "tabpanel", id: `set-panel-${id}`,
                                                 "aria-labelledby": `set-tab-${id}`, tabindex: "0", hidden: true }, panels[id]));
  const narrow = window.matchMedia("(max-width: 599px)");
  const tablist = h("div", { class: "set-tabs", role: "tablist", "aria-label": "Settings sections",
                             "aria-orientation": narrow.matches ? "horizontal" : "vertical",
                             onkeydown: e => {
                               const i = tabs.indexOf(document.activeElement);
                               if (i < 0) return;
                               const go = { ArrowDown: i + 1, ArrowRight: i + 1, ArrowUp: i - 1, ArrowLeft: i - 1,
                                            Home: 0, End: tabs.length - 1 }[e.key];
                               if (go === undefined) return;
                               e.preventDefault();
                               choose(TABS[(go + tabs.length) % tabs.length][0], true);
                             } }, tabs);
  const panelBox = h("div", { class: "set-panels" }, panelEls);
  function choose(id, focusTab = false) {
    TABS.forEach(([t], i) => {
      const on = t === id;
      tabs[i].setAttribute("aria-selected", String(on));
      tabs[i].tabIndex = on ? 0 : -1;
      panelEls[i].hidden = !on;
    });
    panelBox.scrollTop = 0;
    if (focusTab) tabs[TABS.findIndex(([t]) => t === id)].focus();
    if (id === "data") state.loadData?.();
  }

  const d = dialog({
    title: "Settings",
    className: "dlg-wide dlg-settings",
    body: h("div", { class: "set-layout" }, tablist, panelBox),
    onClose: () => {
      // A text box that still had the focus has not sent "change": its value is saved now
      for (const f of state.fields) f.flush();
      for (const fn of state.cleanup) fn();
      open = null;
    },
  });
  open = d;
  choose(TABS.some(([t]) => t === tab) ? tab : "general");

  // Keep the theme radios right if the theme changes another way while the dialog is open (and the switches
  // that the chat's own buttons also change)
  const syncTheme = () => { for (const r of $$("input[name=theme]", d.el)) r.checked = r.value === theme.get(); };
  const syncPrefs = () => state.syncPrefs?.();
  const syncNarrow = () => tablist.setAttribute("aria-orientation", narrow.matches ? "horizontal" : "vertical");
  document.addEventListener("tutor:theme", syncTheme);
  document.addEventListener("tutor:prefs", syncPrefs);
  narrow.addEventListener("change", syncNarrow);
  state.cleanup.push(() => {
    document.removeEventListener("tutor:theme", syncTheme);
    document.removeEventListener("tutor:prefs", syncPrefs);
    narrow.removeEventListener("change", syncNarrow);
  });
  return d;
}

// ---------------------------------------------------------------- General
function generalPanel(state) {
  const p = state.profile;
  const area = (value, placeholder) => textarea(value, { dir: "auto", placeholder, rows: 3, class: "textarea set-area" });
  const add = (key, f, opts) => { const field = profileField(state, key, f, opts); state.fields.push(field); return field.el; };

  const nameIn = input({ value: p.name, autocomplete: "name", dir: "auto", placeholder: "What should the tutor call you?" });
  const level = segmented("set-level", LEVELS.map(l => [l, l]), p.english_level, "English level");
  const lang = select(TEACHING, { value: p.teaching_language });
  const themeGroup = segmented("theme", THEMES, theme.get(), "Theme");
  // The theme lives in this browser, not in the profile: it applies at once
  const themeField = setting("Theme", themeGroup, { group: true, hint: "System follows your computer's light or dark setting." });
  themeGroup.addEventListener("change", () => { theme.set(checkedRadio(themeGroup)); themeField.saved(); });

  return [
    h("h3", { class: "set-heading" }, "About you"),
    add("name", setting("Name", nameIn), { read: c => c.value.trim(), check: lengthCheck(NAME_MAX, "Your name") }),
    add("english_level", setting("English level", level, { group: true,
      hint: "Your best guess on the CEFR scale. The tutor pitches its English at this level." }), { read: checkedRadio }),
    add("teaching_language", setting("Teaching language", lang, { hint: "The language the tutor explains things in." })),
    add("interests", setting("Interests", area(p.interests, "e.g. Football, technology, business."),
      { hint: "The tutor uses these for its examples." }), { read: c => c.value.trim(), check: lengthCheck(TEXT_MAX, "Interests") }),
    add("goals", setting("Goals", area(p.goals, "e.g. Speak fluently about psychology; pass IELTS 7; read research papers.")),
      { read: c => c.value.trim(), check: lengthCheck(TEXT_MAX, "Goals") }),
    add("notes", setting("Notes", area(p.notes, "Anything else the tutor should always keep in mind.")),
      { read: c => c.value.trim(), check: lengthCheck(TEXT_MAX, "Notes") }),
    h("h3", { class: "set-heading" }, "Appearance"),
    themeField.el,
    h("h3", { class: "set-heading" }, "Keyboard"),
    h("p", { class: "set-keys" },
      h("button", { type: "button", class: "link-btn", id: "set-shortcuts", onclick: () => openShortcuts() }, "Shortcuts"),
      h("span", { class: "set-hint" }, "Every key the app knows. Ctrl+/ opens the list from anywhere.")),
  ];
}

// ---------------------------------------------------------------- Voice
function voicePanel(state) {
  const p = state.profile;
  const add = (key, f, opts) => { const field = profileField(state, key, f, opts); state.fields.push(field); return field.el; };

  const voiceSel = select([[p.voice, "Loading voices…"]], { value: p.voice, class: "select set-voice" });
  const previewBtn = button("Preview", { icon: "play", class: "set-preview", "aria-label": "Preview the voice" });
  api("/api/voice/voices").then(list => {
    voiceSel.replaceChildren(...list.map(v => h("option", { value: v.id }, v.label + (v.id === "af_heart" ? " · recommended" : ""))));
    voiceSel.value = list.some(v => v.id === state.profile.voice) ? state.profile.voice : list[0]?.id ?? "";
  }).catch(e => {
    voiceSel.replaceChildren(h("option", { value: state.profile.voice }, "Voice not available"));
    voiceSel.disabled = true;
    previewBtn.disabled = true;
    voiceField.error(`The voices could not be loaded: ${e.message}`);
  });
  const voiceField = setting("The tutor's voice", voiceSel, {
    display: h("div", { class: "set-voice-row" }, voiceSel, previewBtn),
    hint: "American and British voices. Preview plays a short sentence at the speed below." });
  state.fields.push(profileField(state, "voice", voiceField));

  const speedIn = h("input", { type: "range", class: "range", min: "0.7", max: "1.3", step: "0.05",
                               value: String(Number(p.speech_speed) || 1) });
  const speedLabel = h("span", { class: "range-value", "aria-hidden": "true" });
  const showSpeed = () => {
    const v = `${Number(speedIn.value).toFixed(2)}×`;
    speedLabel.textContent = v;
    speedIn.setAttribute("aria-valuetext", v);
  };
  showSpeed();
  speedIn.addEventListener("input", showSpeed);
  // Saved when the slider is let go ("change"), not at every step of a drag
  const speedField = setting("Speed", speedIn, { display: h("div", { class: "range-row set-range" }, speedIn, speedLabel),
                                                 hint: "Slower helps while you are getting used to an accent." });
  state.fields.push(profileField(state, "speech_speed", speedField,
    { read: c => Number(c.value).toFixed(2), same: (a, b) => Number(a).toFixed(2) === Number(b).toFixed(2) }));

  // ▶ Preview: POST /api/voice/speak and play the WAV it returns; a second click stops it
  const preview = { audio: null, url: null };
  const resetPreview = () => {
    preview.audio?.pause();
    if (preview.url) URL.revokeObjectURL(preview.url);
    preview.audio = preview.url = null;
    previewBtn.disabled = voiceSel.disabled;
    previewBtn.setAttribute("aria-pressed", "false");
    previewBtn.setAttribute("aria-label", "Preview the voice");
    previewBtn.replaceChildren(icon("play"), "Preview");
  };
  previewBtn.setAttribute("aria-pressed", "false");
  previewBtn.addEventListener("click", run(async () => {
    if (preview.audio) { resetPreview(); return; }
    voiceField.error("");
    speaker.stop();   // a reply being read aloud would talk over the preview
    previewBtn.disabled = true;
    previewBtn.replaceChildren(spinner(), "Loading…");
    try {
      let r;
      try {
        r = await fetch("/api/voice/speak", { method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ text: SAMPLE, voice: voiceSel.value, speed: Number(speedIn.value) }) });
      } catch {
        throw new Error("The tutor's server is not reachable. Is it still running?");
      }
      if (!r.ok) {
        const data = await r.json().catch(() => ({}));
        throw new Error(typeof data.detail === "string" ? data.detail : `The preview failed (${r.status})`);
      }
      preview.url = URL.createObjectURL(await r.blob());
      preview.audio = new Audio(preview.url);
      preview.audio.addEventListener("ended", resetPreview);
      await preview.audio.play();
      previewBtn.disabled = false;
      previewBtn.setAttribute("aria-pressed", "true");
      previewBtn.setAttribute("aria-label", "Stop the preview");
      previewBtn.replaceChildren(icon("stop"), "Stop");
    } catch (e) {
      resetPreview();
      voiceField.error(e.message);   // shown under the voice, where the learner is looking
    }
  }));
  voiceSel.addEventListener("change", () => { if (preview.audio) resetPreview(); });
  state.cleanup.push(resetPreview);

  const read = switchSetting("Read replies aloud", prefs.readAloud,
    "The tutor's replies are spoken as they arrive. The speaker button above the chat does the same.");
  read.control.addEventListener("change", () => {
    prefs.readAloud = read.control.checked;
    if (!read.control.checked) speaker.stop();
    read.saved();
  });
  const autoSend = switchSetting("Send after speaking", prefs.autoSend,
    "What you say into the microphone is sent as soon as it is written out. Off: it waits in the box for you to check.");
  autoSend.control.addEventListener("change", () => { prefs.autoSend = autoSend.control.checked; autoSend.saved(); });
  state.syncPrefs = () => { read.control.checked = prefs.readAloud; autoSend.control.checked = prefs.autoSend; };

  const patience = segmented("set-patience", PATIENCE, p.turn_patience, "Pause before the tutor answers");
  const careful = switchSetting("Careful grammar answers", p.careful_grammar === "on",
    "When you ask whether a sentence is correct, the tutor thinks it through first. More reliable, but the answer " +
    "takes 30 to 60 seconds instead of about one.");
  state.fields.push(profileField(state, "careful_grammar", careful, { read: c => (c.checked ? "on" : "off") }));

  return [
    h("h3", { class: "set-heading" }, "The tutor's voice"),
    voiceField.el,
    speedField.el,
    h("div", { class: "set-switches" }, read.el, autoSend.el),
    h("h3", { class: "set-heading" }, "Conversation"),
    add("turn_patience", setting("Pause before the tutor answers", patience, { group: true,
      hint: "In a live conversation: how long the tutor waits when you pause. Long gives you time to think mid-sentence." }),
      { read: checkedRadio }),
    h("div", { class: "set-switches" }, careful.el),
  ];
}

// ---------------------------------------------------------------- Data
function dataPanel(state) {
  const pathBox = (path, label) => h("div", { class: "set-path" },
    h("code", { class: "set-path-text", dir: "ltr" }, path),
    iconButton("copy", "Copy", { class: "icon-btn-sm", "aria-label": `Copy ${label}`, onclick: run(async () => {
      try { await navigator.clipboard.writeText(path); } catch { throw new Error("Copying is not allowed here; select the text instead."); }
      toast("Copied");
    }) }));

  // Backup
  const backupResult = h("div", { class: "set-result", role: "status" });
  const backupBtn = button("Back up now", { variant: "primary", icon: "check" });
  backupBtn.addEventListener("click", run(async () => {
    backupBtn.disabled = true;
    backupBtn.replaceChildren(spinner(), "Backing up…");
    try {
      const r = await api("/api/backup", { method: "POST" });
      backupResult.replaceChildren(h("p", { class: "set-ok" }, icon("check"), "Saved a complete copy:"), pathBox(r.path, "the backup's path"));
    } catch (e) {
      backupResult.replaceChildren(h("p", { class: "set-error" }, e.message));
    } finally {
      backupBtn.disabled = false;
      backupBtn.replaceChildren(icon("check"), "Back up now");
    }
  }));

  // Where uploaded files are kept: the folder two levels above a stored file (materials/<course>/<file>)
  const folderBox = h("div", { class: "set-result" }, h("p", { class: "set-hint" }, "Looking…"));
  const findFolder = async () => {
    const courses = await api("/api/courses");
    for (const c of courses.filter(x => x.documents)) {
      const docs = await api(`/api/courses/${c.id}/documents`);
      const stored = docs.map(d => d.stored_path).find(s => /[\\/].+[\\/]/.test(s || ""));
      if (stored) {
        const sep = stored.includes("\\") ? "\\" : "/";
        return stored.split(/[\\/]/).slice(0, -2).join(sep);
      }
    }
    return null;
  };

  // The model, as the status pill in the sidebar shows it, in more detail
  const statusBox = h("div", { class: "set-result" });
  const drawStatus = async () => {
    statusBox.replaceChildren(h("p", { class: "set-hint" }, spinner(), " Checking…"));
    let s = null;
    try { s = await api("/api/status"); } catch { /* shown below */ }
    emit("status", s);
    const row = (label, value, ok) => h("tr", {},
      h("th", { scope: "row" }, label),
      h("td", {}, ok === undefined ? null : h("span", { class: `set-dot ${ok ? "is-ok" : "is-bad"}`, "aria-hidden": "true" }), value));
    statusBox.replaceChildren(s
      ? h("table", { class: "table table-plain set-table" }, h("tbody", {},
          row("App", "Personal Tutor, running on this computer"),
          row("Ollama", s.ollama ? `running · version ${s.ollama}` : "not running", !!s.ollama),
          row("Model", `${s.model} · ${s.installed ? "installed" : "not installed"}`, !!s.installed),
          row("Loaded", s.loaded ? `${s.gpu_percent}% on the GPU · ${Number(s.vram_gb).toFixed(1)} GB` : "not yet (it loads with your first message)",
              s.loaded ? s.gpu_percent >= 100 : undefined)))
      : h("p", { class: "set-error" }, "The tutor's server is not reachable."));
  };

  let loaded = false;
  state.loadData = () => {
    if (loaded) return;
    loaded = true;
    run(drawStatus)();
    findFolder().then(folder => folderBox.replaceChildren(folder
      ? pathBox(folder, "the materials folder")
      : h("p", { class: "set-hint" }, "No files uploaded yet. Files you add to a course are copied into the app's materials folder.")))
      .catch(e => folderBox.replaceChildren(h("p", { class: "set-error" }, e.message)));
  };

  return [
    h("h3", { class: "set-heading" }, "Backup"),
    h("p", { class: "set-hint" }, "Saves a complete copy of your data (profile, memory, progress, chats and words) as one file in the backups folder."),
    h("div", { class: "set-actions" }, backupBtn),
    backupResult,
    h("h3", { class: "set-heading" }, "Your material"),
    h("p", { class: "set-hint" }, "The files you add to your courses are kept here:"),
    folderBox,
    h("h3", { class: "set-heading" }, "App and model"),
    statusBox,
    h("div", { class: "set-actions" }, button("Check again", { size: "sm", icon: "refresh", onclick: run(drawStatus) })),
  ];
}
