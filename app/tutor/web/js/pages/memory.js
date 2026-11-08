// Memory (DESIGN.md section 4): everything the tutor knows about the learner (edit in place, turn on or off, delete,
// add), and below it the session history: the same chats as the sidebar, with their summaries.

import { api } from "../api.js";
import { openSession } from "../chat.js";
import { button, confirmed, empty, fmtDate, h, iconButton, input, navigate, pageHeader, plural, run, select, textarea,
         toast } from "../dom.js";

const KINDS = ["preference", "goal", "background", "interest", "note"];
const KIND_LABELS = { preference: "Preference", goal: "Goal", background: "Background", interest: "Interest", note: "Note" };
let root = null;
let seq = 0;

const where = s => (s.course_name ? s.course_name + (s.topic_title ? " › " + s.topic_title : "") : "Free conversation");
const kindBadge = kind => h("span", { class: `badge mem-kind mem-kind-${kind}` }, KIND_LABELS[kind] || kind);

export async function render(el) {
  root = el;
  const [memories, sessions] = await Promise.all([api("/api/memories"), api("/api/sessions")]);
  if (root !== el) return;
  const state = { memories };

  const list = h("ul", { class: "mem-list", "aria-label": "What your tutor remembers" });
  const count = h("span", { class: "badge badge-accent" });
  const draw = focus => {
    const active = state.memories.filter(m => m.active).length;
    count.textContent = `${active} in use`;
    count.hidden = !state.memories.length;
    if (!state.memories.length) {
      list.replaceChildren(h("li", { class: "mem-empty" }, empty("Nothing yet. It fills up as you study, or tell your tutor something above.")));
      return;
    }
    list.replaceChildren(...state.memories.map(m => memoryRow(m, state, draw)));
    if (focus) list.querySelector(`li[data-id="${focus.id}"] [data-act="${focus.act}"]`)?.focus();
  };
  const reload = async focus => {
    state.memories = await api("/api/memories");
    draw(focus);
  };
  draw();

  const titleId = `mem-title-${++seq}`;
  const histId = `mem-hist-${++seq}`;
  el.replaceChildren(
    pageHeader("Memory", "Everything your tutor knows about you. Items learned in chats are saved automatically; " +
      "you can edit, turn off or delete anything. Turned-off items are kept but never used."),
    h("div", { class: "mem-page" },
      addForm(reload),
      h("section", { class: "card mem-card", "aria-labelledby": titleId },
        h("div", { class: "mem-card-head" }, h("h2", { class: "card-title", id: titleId }, "What your tutor remembers"), count),
        list),
      h("section", { class: "card mem-card", "aria-labelledby": histId },
        h("div", { class: "mem-card-head" },
          h("h2", { class: "card-title", id: histId }, "Session history"),
          sessions.length ? h("span", { class: "badge" }, String(sessions.length)) : null),
        sessions.length
          ? h("ul", { class: "mem-sessions" }, sessions.map(sessionRow))
          : empty("Your chats appear here with their summaries once you have talked to the tutor."))));
}

function addForm(reload) {
  const kindIn = select(KINDS.map(k => [k, KIND_LABELS[k]]), { "aria-label": "Kind", class: "select mem-add-kind", value: "note" });
  const contentIn = input({ placeholder: "e.g. I study best with short questions after each idea.", dir: "auto",
                            maxlength: "500", "aria-label": "What the tutor should remember" });
  const err = h("p", { class: "mem-error", id: `mem-err-${++seq}`, hidden: true });
  const clear = () => { err.hidden = true; contentIn.removeAttribute("aria-invalid"); };
  contentIn.addEventListener("input", clear);
  const titleId = `mem-add-${++seq}`;
  return h("form", { class: "card mem-add", "aria-labelledby": titleId, novalidate: true, onsubmit: run(async e => {
    e.preventDefault();
    const content = contentIn.value.trim();
    const fail = text => {
      err.textContent = text;
      err.hidden = false;
      contentIn.setAttribute("aria-invalid", "true");
      contentIn.setAttribute("aria-describedby", err.id);
      contentIn.focus();
    };
    if (!content) { fail("Write what the tutor should remember."); return; }
    try {
      await api("/api/memories", { method: "POST", json: { kind: kindIn.value, content } });
    } catch (ex) {
      fail(ex.message);
      return;
    }
    contentIn.value = "";
    clear();
    toast("Your tutor will remember that");
    await reload();
    contentIn.focus();
  }) },
    h("h2", { class: "card-title", id: titleId }, "Tell your tutor something to remember"),
    h("div", { class: "mem-add-row" }, kindIn, contentIn, button("Add", { variant: "primary", type: "submit", icon: "plus" })),
    err);
}

function memoryRow(m, state, draw) {
  const contentId = `mem-content-${m.id}`;
  const content = h("p", { class: "mem-content", id: contentId, dir: "auto" }, m.content);
  const meta = h("span", { class: "card-meta" },
    `${m.origin === "auto" ? "Learned in a chat" : "Added by you"} · ${fmtDate(m.updated_at)}`,
    m.active ? null : " · off: not used");
  const save = async patch => {
    const updated = await api(`/api/memories/${m.id}`, { method: "PATCH", json: patch });
    Object.assign(m, updated);
    // Active items come first, as the server lists them
    state.memories.sort((a, b) => b.active - a.active || b.updated_at.localeCompare(a.updated_at));
  };

  const toggle = h("input", { type: "checkbox", class: "switch", role: "switch", checked: !!m.active, "data-act": "toggle",
                              "aria-label": "Use this memory", "aria-describedby": contentId,
                              onchange: run(async e => {
                                const on = e.target.checked;
                                try {
                                  await save({ active: on });
                                } catch (ex) {
                                  e.target.checked = !on;
                                  throw ex;
                                }
                                toast(on ? "Turned on: the tutor uses it again" : "Turned off: kept, but not used");
                                draw({ id: m.id, act: "toggle" });
                              }) });

  const main = h("div", { class: "mem-main" }, h("div", { class: "mem-line" }, kindBadge(m.kind)), content, meta);
  const edit = () => {
    const kindIn = select(KINDS.map(k => [k, KIND_LABELS[k]]), { class: "select select-sm", "aria-label": "Kind", value: m.kind });
    const textIn = textarea(m.content, { class: "textarea mem-edit-text", dir: "auto", rows: 2, maxlength: "500",
                                         "aria-label": "What the tutor remembers" });
    const err = h("p", { class: "mem-error", hidden: true });
    const cancel = () => draw({ id: m.id, act: "edit" });
    const commit = run(async () => {
      const text = textIn.value.trim();
      if (!text) { err.textContent = "A memory cannot be empty. Delete it instead."; err.hidden = false; textIn.focus(); return; }
      if (text !== m.content || kindIn.value !== m.kind) {
        try {
          await save({ content: text, kind: kindIn.value });
        } catch (ex) {
          err.textContent = ex.message;
          err.hidden = false;
          textIn.focus();
          return;
        }
        toast("Memory updated");
      }
      draw({ id: m.id, act: "edit" });
    });
    textIn.addEventListener("keydown", e => {
      if (e.key === "Enter" && !e.shiftKey && !e.isComposing) { e.preventDefault(); commit(); }
      else if (e.key === "Escape") { e.preventDefault(); e.stopPropagation(); cancel(); }
    });
    kindIn.addEventListener("keydown", e => { if (e.key === "Escape") { e.preventDefault(); cancel(); } });
    main.replaceChildren(
      h("div", { class: "mem-edit" }, kindIn, textIn, err,
        h("div", { class: "mem-edit-actions" },
          button("Save", { variant: "primary", size: "sm", onclick: commit }),
          button("Cancel", { variant: "ghost", size: "sm", onclick: cancel }),
          h("span", { class: "field-hint" }, "Enter saves · Esc cancels"))));
    actions.hidden = true;
    textIn.focus();
    textIn.setSelectionRange(textIn.value.length, textIn.value.length);
  };

  const actions = h("div", { class: "mem-actions" },
    toggle,
    iconButton("edit", "Edit", { class: "icon-btn-sm", "data-act": "edit", "aria-label": "Edit this memory",
                                 "aria-describedby": contentId, onclick: edit }),
    iconButton("trash", "Delete", { class: "icon-btn-sm icon-btn-danger", "data-act": "delete", "aria-label": "Delete this memory",
                                    "aria-describedby": contentId, onclick: confirmed({
      title: "Delete this memory?", body: `The tutor will forget: "${m.content}"`,
    }, async () => {
      await api(`/api/memories/${m.id}`, { method: "DELETE" });
      const i = state.memories.indexOf(m);
      state.memories.splice(i, 1);
      toast("Deleted: the tutor has forgotten it");
      const next = state.memories[i] || state.memories[i - 1];
      draw(next ? { id: next.id, act: "delete" } : null);
    }) }));
  content.addEventListener("dblclick", edit);

  return h("li", { class: "mem-item" + (m.active ? "" : " is-off"), "data-id": m.id }, main, actions);
}

function sessionRow(s) {
  const open = !s.ended_at;
  return h("li", { class: "mem-session" },
    h("div", { class: "mem-session-main" },
      h("div", { class: "mem-line" },
        h("strong", { class: "mem-session-title", dir: "auto" }, s.title || "New chat"),
        open ? h("span", { class: "badge badge-good" }, "open") : null),
      h("span", { class: "card-meta" }, `${fmtDate(s.started_at)} · ${where(s)} · ${plural(s.message_count, "message")}`),
      s.summary
        ? h("p", { class: "mem-summary", dir: "auto" }, s.summary)
        : h("p", { class: "mem-summary is-none" }, open ? "In progress: summarised when it ends." : "No summary."),
      s.next_step ? h("p", { class: "mem-next", dir: "auto" }, h("span", { class: "mem-next-label" }, "Next: "), s.next_step) : null),
    button("Open", { size: "sm", variant: "ghost", icon: "chevron-right", "aria-label": `Open "${s.title || "New chat"}"`,
      onclick: run(async () => { if (await openSession(s.id)) navigate("chat"); }) }));
}
