// Courses (DESIGN.md section 4): the list of courses and, for the selected one, its name and description (edited in
// place), its material (a drop zone with per-file progress, and the documents table) and its topics in syllabus order
// (rename in place, move, link material, delete, suggestions from the material).

import { api } from "../api.js";
import { $$, button, confirmed, dialog, empty, field, fmtDue, h, icon, iconButton, input, pageHeader, plural,
         remembered, run, spinner, toast } from "../dom.js";

const ACCEPT = [".pdf", ".txt", ".md"];
const MAX_MB = 50;   // the server's limit (config.MAX_UPLOAD_MB)
const narrow = window.matchMedia("(max-width: 899px)");

// selected: the course shown; ctx: the detail being shown (its course, documents and topics, and its elements)
const courses = { selected: null, root: null, list: [], ctx: null, openMaterial: new Set() };
// Files upload one at a time, whatever course they were dropped on, and keep going if the learner moves on
const uploads = { queue: [], busy: false, lists: new Set() };
let seq = 0;

const hasFiles = e => [...(e.dataTransfer?.types || [])].includes("Files");
const day = iso => new Date(iso).toLocaleDateString(undefined, { day: "numeric", month: "short", year: "numeric" });
const size = n => (n >= 1048576 ? `${(n / 1048576).toFixed(1)} MB` : `${Math.max(1, Math.round(n / 1024))} KB`);

// A one-line error under a field; it goes away as soon as the learner types again
function errorLine() {
  const el = h("p", { class: "courses-error", id: `courses-err-${++seq}`, hidden: true });
  let target = null;
  const clear = () => {
    el.hidden = true;
    target?.removeAttribute("aria-invalid");
    target = null;
  };
  return {
    el, clear,
    show(control, text) {
      clear();
      target = control;
      el.textContent = text;
      el.hidden = false;
      control.setAttribute("aria-invalid", "true");
      control.setAttribute("aria-describedby", el.id);
      control.addEventListener("input", clear, { once: true });
    },
  };
}

// A small "Saved" chip that shows for a moment after a change reaches the server
function savedChip(cls) {
  const el = h("span", { class: cls, role: "status", hidden: true }, icon("check"), "Saved");
  let timer = 0;
  return {
    el,
    show() {
      el.hidden = false;
      el.classList.remove("is-shown");
      void el.offsetWidth;   // restart the fade-in
      el.classList.add("is-shown");
      clearTimeout(timer);
      timer = setTimeout(() => { el.hidden = true; }, 2000);
    },
  };
}

// ---------------------------------------------------------------- the page
export async function render(el) {
  courses.root = el;
  const list = await api("/api/courses");
  if (courses.root !== el) return;
  courses.list = list;
  if (!list.some(c => c.id === courses.selected)) {
    const last = Number(remembered.get("courses-selected"));
    courses.selected = list.some(c => c.id === last) ? last : list[0]?.id ?? null;
  }
  const header = pageHeader("Courses", "Your courses, their material and the order of their topics.");

  // Files dropped anywhere on the page go to the course shown, and the browser never opens them instead.
  // (render() runs again on the same element after a course is created or deleted: the listeners are added once.)
  if (!el.dataset.dropTarget) {
    el.dataset.dropTarget = "1";
    el.addEventListener("dragover", e => {
      if (!hasFiles(e)) return;
      e.preventDefault();
      e.dataTransfer.dropEffect = courses.ctx?.pane.isConnected ? "copy" : "none";
    });
    el.addEventListener("drop", e => {
      if (!hasFiles(e)) return;
      e.preventDefault();
      courses.ctx?.drop.classList.remove("is-over");
      if (courses.ctx?.pane.isConnected) addFiles(courses.ctx, [...e.dataTransfer.files]);
    });
  }

  if (!list.length) {
    courses.ctx = null;
    el.replaceChildren(header, h("section", { class: "card courses-empty" },
      h("span", { class: "courses-empty-icon", "aria-hidden": "true" }, icon("book")),
      h("p", {}, "Create your first course, then add its material: books, lecture transcripts or notes, as PDF, TXT or Markdown."),
      button("Create a course", { variant: "primary", icon: "plus", onclick: newCourse })));
    return;
  }
  const detail = h("div", { class: "courses-detail" });
  const course = list.find(c => c.id === courses.selected);
  await renderDetail(detail, course);
  if (courses.root !== el) return;
  el.replaceChildren(header, h("div", { class: "courses-body" }, h("div", { class: "courses-layout" },
    h("nav", { class: "courses-list-pane", "aria-label": "Courses" }, courseList(),
      button("New course", { variant: "ghost", icon: "plus", class: "courses-new", onclick: newCourse })),
    detail)));
}

function courseList() {
  return h("ul", { class: "courses-list" }, courses.list.map(c => h("li", {},
    h("button", { type: "button", class: "courses-item", "data-id": c.id,
                  "aria-current": c.id === courses.selected ? "true" : null, onclick: run(() => selectCourse(c.id)) },
      h("span", { class: "courses-item-name", dir: "auto" }, c.name),
      h("span", { class: "courses-item-meta" }, `${plural(c.documents, "file")} · ${plural(c.topics, "topic")}`)))));
}

// The counts in the list follow uploads and topic changes; the buttons are updated in place (focus stays put)
const refreshList = run(async () => {
  const list = await api("/api/courses");
  courses.list = list;
  const pane = courses.root?.querySelector(".courses-list-pane");
  if (!pane) return;
  const items = $$(".courses-item", pane);
  if (items.length !== list.length || items.some((b, i) => Number(b.dataset.id) !== list[i].id)) {
    pane.querySelector(".courses-list").replaceWith(courseList());
    return;
  }
  list.forEach((c, i) => {
    items[i].querySelector(".courses-item-name").textContent = c.name;
    items[i].querySelector(".courses-item-meta").textContent = `${plural(c.documents, "file")} · ${plural(c.topics, "topic")}`;
  });
});

async function selectCourse(id) {
  if (id === courses.selected && courses.ctx?.pane.isConnected) return;
  courses.selected = id;
  remembered.set("courses-selected", id);
  for (const b of $$(".courses-item", courses.root)) {
    if (Number(b.dataset.id) === id) b.setAttribute("aria-current", "true");
    else b.removeAttribute("aria-current");
  }
  const detail = courses.root.querySelector(".courses-detail");
  detail.setAttribute("aria-busy", "true");
  try {
    await renderDetail(detail, courses.list.find(c => c.id === id));
  } finally {
    detail.removeAttribute("aria-busy");
  }
  // The panes are stacked on narrow screens: show the course that was just chosen
  if (narrow.matches && courses.selected === id) detail.scrollIntoView({ block: "start", behavior: "smooth" });
}

function newCourse() {
  const formId = `courses-new-${++seq}`;
  const nameIn = input({ placeholder: "e.g. Introduction to Psychology (Yale)", maxlength: "200", dir: "auto" });
  const descIn = input({ placeholder: "Optional: what the course is about", maxlength: "2000", dir: "auto" });
  const err = errorLine();
  const create = button("Create course", { variant: "primary", type: "submit", form: formId });
  const form = h("form", { id: formId, class: "courses-new-form", novalidate: true, onsubmit: run(async e => {
    e.preventDefault();
    if (!nameIn.value.trim()) { err.show(nameIn, "Give the course a name."); nameIn.focus(); return; }
    create.disabled = true;
    try {
      const c = await api("/api/courses", { method: "POST", json: { name: nameIn.value.trim(), description: descIn.value.trim() } });
      courses.selected = c.id;
      remembered.set("courses-selected", c.id);
      d.close(true);
      if (courses.root?.isConnected) await render(courses.root);
      toast(`Course "${c.name}" created. Add its material next.`);
    } catch (ex) {
      err.show(nameIn, ex.message);
      nameIn.focus();
    } finally {
      create.disabled = false;
    }
  }) }, field("Name", nameIn), err.el, field("Description", descIn));
  const d = dialog({ title: "New course", className: "dlg-courses-new", body: form,
                     actions: [button("Cancel", { onclick: () => d.close(false) }), create] });
  nameIn.focus();
}

// ---------------------------------------------------------------- the selected course
async function renderDetail(pane, course) {
  const base = `/api/courses/${course.id}`;
  const [docs, topics] = await Promise.all([api(`${base}/documents`), api(`${base}/topics`)]);
  if (courses.selected !== course.id) return;   // another course was chosen meanwhile
  const ctx = { course, base, docs, topics, pane };
  courses.ctx = ctx;
  pane.replaceChildren(headCard(ctx), materialCard(ctx), topicsCard(ctx));
}

function headCard(ctx) {
  const saved = savedChip("courses-saved");
  const err = errorLine();
  const nameIn = h("input", { class: "courses-name", value: ctx.course.name, "aria-label": "Course name", maxlength: "200",
                              autocomplete: "off", dir: "auto" });
  const descIn = h("input", { class: "courses-desc", value: ctx.course.description || "", "aria-label": "Course description",
                              placeholder: "Add a short description", maxlength: "2000", autocomplete: "off", dir: "auto" });
  // Saved when the field is left (or Enter); Esc puts the saved text back
  const editable = (control, key) => {
    control.addEventListener("change", run(async () => {
      const value = control.value.trim();
      if (value === (ctx.course[key] || "")) return;
      if (key === "name" && !value) {
        control.value = ctx.course.name;
        err.show(control, "A course needs a name, so the old one was kept.");
        return;
      }
      try {
        ctx.course = await api(ctx.base, { method: "PATCH", json: { [key]: value } });
      } catch (e) {
        control.value = ctx.course[key] || "";
        err.show(control, e.message);
        return;
      }
      control.value = ctx.course[key] || "";
      err.clear();
      saved.show();
      if (key === "name") refreshList();
    }));
    control.addEventListener("keydown", e => {
      if (e.key === "Enter") { e.preventDefault(); control.blur(); }
      else if (e.key === "Escape") { e.preventDefault(); control.value = ctx.course[key] || ""; err.clear(); control.blur(); }
    });
  };
  editable(nameIn, "name");
  editable(descIn, "description");

  const remove = button("Delete course", { variant: "danger-ghost", size: "sm", icon: "trash", onclick: confirmed({
    title: "Delete this course?",
    body: `"${ctx.course.name}", its material and its topics will be deleted. Your chats about it are kept.`,
    confirmLabel: "Delete course",
  }, async () => {
    await api(ctx.base, { method: "DELETE" });
    courses.selected = null;
    remembered.set("courses-selected", "");
    toast(`Course "${ctx.course.name}" deleted`);
    if (courses.root?.isConnected) await render(courses.root);
  }) });

  return h("section", { class: "card courses-head", "aria-label": "Course details" },
    h("div", { class: "courses-head-row" },
      h("div", { class: "courses-head-text" }, nameIn, descIn),
      h("div", { class: "courses-head-side" }, saved.el, remove)),
    err.el,
    h("p", { class: "courses-hint" }, icon("edit"), "Click the name or the description to change it."));
}

// A card with a heading, an optional count and an action on the right
function detailCard(title, count, action, className, ...content) {
  const id = `courses-card-${++seq}`;
  return h("section", { class: `card ${className}`, "aria-labelledby": id },
    h("div", { class: "courses-card-head" },
      h("h2", { class: "card-title", id }, title), count,
      action ? h("div", { class: "courses-card-action" }, action) : null),
    ...content);
}

// ---------------------------------------------------------------- material: the drop zone and the documents
function materialCard(ctx) {
  const hintId = `courses-drop-hint-${++seq}`;
  const fileIn = h("input", { type: "file", class: "courses-file", multiple: true, accept: ACCEPT.join(","), hidden: true,
                              "aria-label": "Files to upload" });
  fileIn.addEventListener("change", () => {
    addFiles(ctx, [...fileIn.files]);
    fileIn.value = "";   // the same file can be chosen again (after fixing it)
  });
  const drop = h("button", { type: "button", class: "courses-drop", "aria-describedby": hintId, onclick: () => fileIn.click() },
    h("span", { class: "courses-drop-icon", "aria-hidden": "true" }, icon("upload")),
    h("span", { class: "courses-drop-text" }, "Drop PDF, TXT or MD files here, or ",
      h("span", { class: "courses-drop-link" }, "click to choose")),
    h("span", { class: "courses-drop-hint", id: hintId },
      `PDFs need selectable text (scanned pages need OCR first). Up to ${MAX_MB} MB each.`));
  // dragenter/dragleave also fire for the zone's children: a counter tells when the files really left
  let depth = 0;
  drop.addEventListener("dragenter", e => { if (hasFiles(e)) { depth += 1; drop.classList.add("is-over"); } });
  drop.addEventListener("dragleave", () => { depth = Math.max(0, depth - 1); if (!depth) drop.classList.remove("is-over"); });
  drop.addEventListener("drop", () => { depth = 0; });   // the page's drop handler uploads the files

  ctx.drop = drop;
  ctx.uploadsEl = h("ul", { class: "courses-uploads", "aria-label": "Uploads" });
  ctx.docsEl = h("div", { class: "courses-docs-box" });
  ctx.docCount = h("span", { class: "badge" });
  drawDocs(ctx);
  return detailCard("Material", ctx.docCount, null, "courses-material",
    h("p", { class: "card-meta" }, "The tutor answers from these files and cites their pages."),
    fileIn, drop, ctx.uploadsEl, ctx.docsEl);
}

function drawDocs(ctx) {
  ctx.docCount.textContent = String(ctx.docs.length);
  ctx.docCount.hidden = !ctx.docs.length;
  if (!ctx.docs.length) {
    ctx.docsEl.replaceChildren(empty("No material yet. Add the course's books, transcripts or notes above."));
    return;
  }
  const th = (label, cls) => h("th", { scope: "col", class: cls }, label);
  ctx.docsEl.replaceChildren(h("div", { class: "table-wrap" }, h("table", { class: "table courses-docs" },
    h("thead", {}, h("tr", {}, th("Name"), th("Pages", "courses-num"), th("Passages", "courses-num"),
      th("Added", "courses-date"), h("th", { scope: "col", class: "cell-actions" }, h("span", { class: "sr-only" }, "Actions")))),
    h("tbody", {}, ctx.docs.map(d => h("tr", {},
      h("td", { class: "courses-doc-name" }, icon("sources"), h("span", { class: "break" }, d.filename)),
      h("td", { class: "courses-num" }, d.pages ?? "—"),
      h("td", { class: "courses-num" }, d.passages),
      h("td", { class: "courses-date" }, day(d.created_at)),
      h("td", { class: "cell-actions" },
        iconButton("trash", "Delete file", { class: "icon-btn-sm icon-btn-danger", "aria-label": `Delete ${d.filename}`,
          onclick: confirmed({
            title: "Delete this file?",
            body: `"${d.filename}" will be removed from the course material, and from the topics that use it.`,
          }, async () => {
            await api(`/api/documents/${d.id}`, { method: "DELETE" });
            toast(`"${d.filename}" deleted`);
            await refreshDocs(ctx);
            refreshList();
            ctx.drop.focus();   // its row is gone; the drop zone is the nearest place to continue
          }) }))))))));
}

async function refreshDocs(ctx) {
  ctx.docs = await api(`${ctx.base}/documents`);
  // Topics list the files they are taught from: their material panels follow the new list
  ctx.topics = await api(`${ctx.base}/topics`);
  drawDocs(ctx);
  drawTopics(ctx);
}

// One row per file: waiting, uploading (with its progress), reading (the server extracts the text), then the
// result or the error, inline
function uploadRow(file) {
  const bar = h("div", { class: "progress-bar" });
  const progress = h("div", { class: "progress courses-upload-progress", role: "progressbar", "aria-valuemin": "0",
                              "aria-valuemax": "100", "aria-valuenow": "0", "aria-label": `Uploading ${file.name}` }, bar);
  const status = h("span", { class: "courses-upload-status" }, "Waiting…");
  const mark = h("span", { class: "courses-upload-mark", "aria-hidden": "true" }, icon("sources"));
  const li = h("li", { class: "courses-upload is-waiting" }, mark,
    h("div", { class: "courses-upload-main" },
      h("div", { class: "courses-upload-line" },
        h("span", { class: "courses-upload-name break" }, file.name),
        h("span", { class: "courses-upload-size" }, size(file.size))),
      progress, status));
  const stage = (name, text) => {
    li.className = `courses-upload is-${name}`;
    status.textContent = text;
  };
  return {
    li,
    uploading(fraction) {
      const pct = Math.round(100 * fraction);
      if (pct >= 100) { stage("reading", "Reading the file…"); progress.setAttribute("aria-valuenow", "100"); }
      else stage("uploading", `Uploading… ${pct}%`);
      bar.style.setProperty("--value", `${pct}%`);
      progress.setAttribute("aria-valuenow", String(pct));
    },
    done(text) {
      stage("done", text);
      mark.replaceChildren(icon("check"));
      progress.hidden = true;
    },
    fail(text) {
      stage("error", text);
      mark.replaceChildren(icon("alert"));
      progress.hidden = true;
      status.setAttribute("role", "alert");
    },
  };
}

function addFiles(ctx, files) {
  if (!files.length) return;
  uploads.lists.add(ctx);
  ctx.uploadsEl.querySelector(".courses-uploads-foot")?.remove();
  for (const file of files) {
    const row = uploadRow(file);
    ctx.uploadsEl.append(row.li);
    const ext = file.name.includes(".") ? file.name.slice(file.name.lastIndexOf(".")).toLowerCase() : "";
    if (!ACCEPT.includes(ext)) row.fail("Not a PDF, TXT or MD file, so it was not uploaded.");
    else if (file.size > MAX_MB * 1048576) row.fail(`Larger than ${MAX_MB} MB, so it was not uploaded.`);
    else if (!file.size) row.fail("This file is empty.");
    else uploads.queue.push({ ctx, file, row });
  }
  pump();
}

async function pump() {
  if (uploads.busy) return;
  uploads.busy = true;
  const touched = new Set();
  try {
    while (uploads.queue.length) {
      const job = uploads.queue.shift();
      job.row.uploading(0);
      try {
        const [r] = await uploadFile(`${job.ctx.base}/documents`, job.file, f => job.row.uploading(f));
        if (r?.ok) {
          const d = r.document;
          job.row.done(`Added: ${plural(d.passages, "passage")}${d.pages ? ` from ${plural(d.pages, "page")}` : ""}`);
          touched.add(job.ctx);
          await refreshDocs(job.ctx).catch(() => {});   // the row already says it worked; the table catches up later
        } else {
          job.row.fail(r?.error || "The server did not accept this file.");
        }
      } catch (e) {
        job.row.fail(e.message);
      }
    }
  } finally {
    uploads.busy = false;
  }
  if (touched.size) refreshList();
  // Every list that finished gets a way to clear it (errors stay until then)
  for (const ctx of uploads.lists) {
    if (!ctx.uploadsEl.children.length || ctx.uploadsEl.querySelector(".courses-uploads-foot")) continue;
    ctx.uploadsEl.append(h("li", { class: "courses-uploads-foot" },
      button("Clear the list", { variant: "ghost", size: "sm", onclick: () => {
        ctx.uploadsEl.replaceChildren();
        ctx.drop.focus();
      } })));
  }
  uploads.lists.clear();
}

// fetch() cannot report upload progress, XMLHttpRequest can. One file per request, so each gets its own result.
function uploadFile(url, file, onProgress) {
  return new Promise((resolve, reject) => {
    const form = new FormData();
    form.append("files", file);
    const xhr = new XMLHttpRequest();
    xhr.open("POST", url);
    xhr.responseType = "json";
    xhr.upload.addEventListener("progress", e => { if (e.lengthComputable) onProgress(Math.min(0.99, e.loaded / e.total)); });
    xhr.upload.addEventListener("load", () => onProgress(1));
    xhr.addEventListener("load", () => {
      if (xhr.status >= 200 && xhr.status < 300) resolve(xhr.response || []);
      else reject(new Error(xhr.response?.detail || `Upload failed (${xhr.status})`));
    });
    xhr.addEventListener("error", () => reject(new Error("The tutor's server is not reachable. Is it still running?")));
    xhr.send(form);
  });
}

// ---------------------------------------------------------------- topics
function topicsCard(ctx) {
  const suggestBox = h("div", { class: "courses-suggest", hidden: true });
  const suggestBtn = button("Suggest topics", { size: "sm", icon: "spark", title: "Read the material and suggest topics" });
  suggestBtn.addEventListener("click", run(() => suggest(ctx, suggestBtn, suggestBox)));
  const topicIn = input({ placeholder: "Add a topic, e.g. Classical conditioning", "aria-label": "New topic title",
                          maxlength: "200", dir: "auto" });
  const err = errorLine();
  const addForm = h("form", { class: "courses-add-topic", novalidate: true, onsubmit: run(async e => {
    e.preventDefault();
    const title = topicIn.value.trim();
    if (!title) { err.show(topicIn, "Write the topic's title first."); topicIn.focus(); return; }
    try {
      await api(`${ctx.base}/topics`, { method: "POST", json: { title } });
    } catch (ex) {
      err.show(topicIn, ex.message);
      topicIn.focus();
      return;
    }
    topicIn.value = "";
    await refreshTopics(ctx);
    refreshList();
  }) }, topicIn, button("Add", { variant: "primary", type: "submit", icon: "plus" }));
  ctx.topicsEl = h("ol", { class: "courses-topics", "aria-label": "Topics in syllabus order" });
  ctx.topicCount = h("span", { class: "badge" });
  drawTopics(ctx);
  return detailCard("Topics", ctx.topicCount, suggestBtn, "courses-topics-card",
    h("p", { class: "card-meta" }, "In syllabus order: the tutor teaches them one by one, then schedules reviews."),
    addForm, err.el, suggestBox, ctx.topicsEl);
}

async function refreshTopics(ctx, focus) {
  ctx.topics = await api(`${ctx.base}/topics`);
  drawTopics(ctx, focus);
}

// focus: { id, act } puts the keyboard focus back on the same control of the same topic after a redraw
function drawTopics(ctx, focus) {
  ctx.topicCount.textContent = String(ctx.topics.length);
  ctx.topicCount.hidden = !ctx.topics.length;
  if (!ctx.topics.length) {
    ctx.topicsEl.replaceChildren(h("li", { class: "courses-topics-empty" },
      "No topics yet. Add them in the order of your syllabus, or let the tutor suggest them from the material."));
    return;
  }
  ctx.topicsEl.replaceChildren(...ctx.topics.map((t, i) => topicRow(ctx, t, i)));
  if (focus) {
    const row = ctx.topicsEl.querySelector(`li[data-id="${focus.id}"]`);
    const target = row?.querySelector(`[data-act="${focus.act}"]:not(:disabled)`)
      || row?.querySelector("[data-act=up]:not(:disabled), [data-act=down]:not(:disabled)")
      || row?.querySelector("[data-act=rename]");
    target?.focus();
  }
}

function topicRow(ctx, t, i) {
  const last = ctx.topics.length - 1;
  const title = h("span", { class: "courses-topic-title", dir: "auto" }, t.title);
  const panelId = `courses-mat-${t.id}`;
  const open = courses.openMaterial.has(t.id);
  const linkedLine = h("span", { class: "courses-topic-docs" });
  const matCount = h("span", { class: "courses-mat-count" });
  const showLinked = () => {
    const linked = ctx.docs.filter(d => t.document_ids.includes(d.id));
    linkedLine.hidden = !linked.length;
    linkedLine.replaceChildren(icon("sources"), h("span", { class: "break" }, linked.map(d => d.filename).join(", ")));
    matCount.textContent = linked.length ? ` (${linked.length})` : "";
  };
  showLinked();

  const panel = h("div", { class: "courses-mat-panel", id: panelId, hidden: !open },
    ctx.docs.length
      ? [h("p", { class: "card-meta" }, "The tutor searches these files first when you study this topic:"),
         ctx.docs.map(d => h("label", { class: "check" },
           h("input", { type: "checkbox", checked: t.document_ids.includes(d.id), onchange: run(async ev => {
             const box = ev.target;
             try {
               const r = await api(`/api/topics/${t.id}/documents/${d.id}`, { method: box.checked ? "PUT" : "DELETE" });
               t.document_ids = r.document_ids;
             } catch (e) {
               box.checked = !box.checked;
               throw e;
             }
             showLinked();
           }) }),
           h("span", { class: "break" }, d.filename)))]
      : h("p", { class: "card-meta" }, "Add material to this course first (above), then choose the files this topic uses."));
  const matBtn = h("button", { type: "button", class: "btn btn-ghost btn-sm courses-mat-btn", "data-act": "material",
                               "aria-expanded": String(open), "aria-controls": panelId,
                               title: "Choose the files this topic is taught from",
                               onclick: () => {
                                 panel.hidden = !panel.hidden;
                                 matBtn.setAttribute("aria-expanded", String(!panel.hidden));
                                 if (panel.hidden) courses.openMaterial.delete(t.id);
                                 else courses.openMaterial.add(t.id);
                               } },
    "Material", matCount, icon("chevron-down"));

  const move = dir => run(async () => {
    await api(`/api/topics/${t.id}/move`, { method: "POST", json: { direction: dir } });
    await refreshTopics(ctx, { id: t.id, act: dir < 0 ? "up" : "down" });
  });

  const rename = () => {
    const edit = h("input", { class: "input input-sm courses-topic-edit", value: t.title, maxlength: "200", dir: "auto",
                              "aria-label": `New title for "${t.title}"` });
    let finished = false;
    const finish = run(async save => {
      if (finished) return;
      finished = true;
      const value = edit.value.trim();
      if (save && value && value !== t.title) {
        try {
          await api(`/api/topics/${t.id}`, { method: "PATCH", json: { title: value } });
        } catch (e) {
          finished = false;   // keep editing: the learner can fix the title or press Esc
          edit.focus();
          throw e;
        }
        toast(`Renamed to "${value}"`);
      }
      await refreshTopics(ctx, { id: t.id, act: "rename" });
    });
    edit.addEventListener("keydown", e => {
      if (e.key === "Enter") { e.preventDefault(); finish(true); }
      else if (e.key === "Escape") { e.preventDefault(); e.stopPropagation(); finish(false); }
    });
    edit.addEventListener("blur", () => finish(true));
    title.replaceWith(edit);
    edit.focus();
    edit.select();
  };
  title.addEventListener("dblclick", rename);

  const overdue = t.due_at && new Date(t.due_at) <= new Date();
  return h("li", { class: "courses-topic", "data-id": t.id },
    h("span", { class: "courses-topic-num", "aria-hidden": "true" }, i + 1),
    h("div", { class: "courses-topic-main" },
      h("div", { class: "courses-topic-line" },
        h("span", { class: "sr-only" }, `${i + 1}. `), title,
        h("span", { class: `badge badge-${t.status}` }, t.status),
        t.status !== "new" && t.due_at
          ? h("span", { class: "courses-due" + (overdue ? " is-due" : "") }, overdue ? "review due" : `review ${fmtDue(t.due_at)}`)
          : null),
      linkedLine, panel),
    h("div", { class: "courses-topic-actions" },
      matBtn,
      iconButton("chevron-up", "Move up", { class: "icon-btn-sm", "data-act": "up", disabled: i === 0,
                                            "aria-label": `Move "${t.title}" up`, onclick: move(-1) }),
      iconButton("chevron-down", "Move down", { class: "icon-btn-sm", "data-act": "down", disabled: i === last,
                                                "aria-label": `Move "${t.title}" down`, onclick: move(1) }),
      iconButton("edit", "Rename", { class: "icon-btn-sm", "data-act": "rename", "aria-label": `Rename "${t.title}"`,
                                     onclick: rename }),
      iconButton("trash", "Delete", { class: "icon-btn-sm icon-btn-danger", "data-act": "delete",
                                      "aria-label": `Delete "${t.title}"`, onclick: confirmed({
        title: "Delete this topic?", body: `"${t.title}" and its review history will be deleted.`,
      }, async () => {
        await api(`/api/topics/${t.id}`, { method: "DELETE" });
        courses.openMaterial.delete(t.id);
        toast(`Topic "${t.title}" deleted`);
        const next = ctx.topics[i + 1] || ctx.topics[i - 1];
        await refreshTopics(ctx, next ? { id: next.id, act: "delete" } : null);
        refreshList();
      }) })));
}

// The tutor reads the material and proposes topics; the learner keeps the ones they want
async function suggest(ctx, btn, box) {
  btn.disabled = true;
  btn.setAttribute("aria-busy", "true");
  btn.replaceChildren(spinner(), "Reading your material…");
  box.hidden = false;
  box.replaceChildren(h("p", { class: "card-meta", role: "status" }, "The tutor is reading your material. This can take a minute."));
  const dismiss = () => button("Dismiss", { variant: "ghost", size: "sm", onclick: () => {
    box.hidden = true;
    box.replaceChildren();
    btn.focus();
  } });
  try {
    const { topics } = await api(`${ctx.base}/topics/suggest`, { method: "POST" });
    if (!topics.length) {
      box.replaceChildren(h("p", { class: "card-meta", role: "status" }, "No new topics found in the material."), dismiss());
      return;
    }
    const boxes = topics.map(t => h("input", { type: "checkbox", checked: true, value: t }));
    box.replaceChildren(
      h("p", { class: "courses-suggest-title", role: "status" },
        `${plural(topics.length, "topic")} suggested from your material. Untick what you don't want:`),
      h("div", { class: "courses-suggest-list" }, topics.map((t, i) => h("label", { class: "check" }, boxes[i],
        h("span", { dir: "auto" }, t)))),
      h("div", { class: "courses-suggest-actions" },
        button("Add selected", { variant: "primary", size: "sm", icon: "plus", onclick: run(async () => {
          let added = 0;
          const failed = [];
          for (const b of boxes.filter(x => x.checked)) {
            try {
              await api(`${ctx.base}/topics`, { method: "POST", json: { title: b.value } });
              added += 1;
            } catch (e) {
              failed.push(e.message);   // a topic added meanwhile is a duplicate now: the others still go in
            }
          }
          box.hidden = true;
          box.replaceChildren();
          toast(added ? `Added ${plural(added, "topic")}` : "No topics added", failed.length && !added ? "error" : "info");
          await refreshTopics(ctx);
          refreshList();
          btn.focus();
        }) }),
        dismiss()));
  } catch (e) {
    box.replaceChildren(h("p", { class: "courses-error", role: "alert" }, e.message), dismiss());
  } finally {
    btn.disabled = false;
    btn.removeAttribute("aria-busy");
    btn.replaceChildren(icon("spark"), "Suggest topics");
  }
}
