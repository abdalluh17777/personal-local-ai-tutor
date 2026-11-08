// DOM building and the shared UI components: icons, buttons, toast, dialog, confirmDialog, popover, menu,
// skeleton, page header, card, formatting helpers, and a tiny app-wide event bus. No app state lives here.

export const $ = (sel, root = document) => root.querySelector(sel);
export const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];

// h("button", { class: "btn", onclick }, "Label", child, [more]): attributes first, children flattened.
// null/undefined/false attributes and children are skipped; "value" is set last, after a <select>'s options exist.
export function h(tag, attrs, ...children) {
  const el = document.createElement(tag);
  let value;
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v === null || v === undefined || v === false) continue;
    if (k === "class") el.className = v;
    else if (k === "value") value = v;
    else if (k.startsWith("on") && typeof v === "function") el.addEventListener(k.slice(2), v);
    else if (v === true) el.setAttribute(k, "");
    else el.setAttribute(k, v);
  }
  for (const c of children.flat(Infinity)) {
    if (c === null || c === undefined || c === false) continue;
    el.append(c instanceof Node ? c : document.createTextNode(String(c)));
  }
  if (value !== undefined) el.value = value;
  return el;
}

const SVG_NS = "http://www.w3.org/2000/svg";

// An icon from the sprite in index.html: <svg class="icon"><use href="#i-name"/></svg>
export function icon(name, cls = "") {
  const svg = document.createElementNS(SVG_NS, "svg");
  svg.setAttribute("class", cls ? `icon ${cls}` : "icon");
  svg.setAttribute("aria-hidden", "true");
  const use = document.createElementNS(SVG_NS, "use");
  use.setAttribute("href", `#i-${name}`);
  svg.append(use);
  return svg;
}

export function setIcon(button, name) {
  button.querySelector("svg.icon use")?.setAttribute("href", `#i-${name}`);
}

// A round icon-only button: the label is both its accessible name and its tooltip
export function iconButton(name, label, attrs = {}) {
  const { class: cls, ...rest } = attrs;
  return h("button", { type: "button", class: "icon-btn" + (cls ? " " + cls : ""), "aria-label": label, title: label, ...rest },
    icon(name));
}

// A text button: variant primary | secondary | ghost | danger | danger-ghost; size "sm" for compact rows
export function button(label, { variant = "secondary", size, icon: iconName, class: cls, ...attrs } = {}) {
  const classes = ["btn", `btn-${variant}`, size ? `btn-${size}` : "", cls || ""].filter(Boolean).join(" ");
  return h("button", { type: "button", class: classes, ...attrs }, iconName ? icon(iconName) : null, label);
}

// ---------------------------------------------------------------- errors and events
// Every async handler goes through run(): a failed request becomes a toast instead of an unhandled rejection
export const run = fn => async (...args) => {
  try {
    return await fn(...args);
  } catch (e) {
    if (e?.name === "AbortError") return undefined;
    toast(e?.message || String(e), "error");
    return undefined;
  }
};

// App-wide events between modules that must not import each other (pages -> router, chat -> status pill)
export const emit = (name, detail) => document.dispatchEvent(new CustomEvent(`tutor:${name}`, { detail }));
export const on = (name, fn) => document.addEventListener(`tutor:${name}`, e => fn(e.detail));
export const navigate = route => emit("navigate", route);

// ---------------------------------------------------------------- storage and formatting
export const remembered = {
  get(key) { try { return localStorage.getItem(key); } catch { return null; } },
  set(key, value) { try { localStorage.setItem(key, value ?? ""); } catch { /* storage unavailable */ } },
};

export function fmtDate(iso) {
  if (!iso) return "";
  const d = new Date(iso);
  return d.toLocaleDateString(undefined, { month: "short", day: "numeric" }) + ", " +
         d.toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" });
}

export function fmtDue(iso) {
  if (!iso) return "";
  const diff = new Date(iso) - Date.now();
  if (diff <= 0) return "due now";
  const mins = Math.round(diff / 60000);
  if (mins < 60) return `in ${mins} min`;
  if (mins < 60 * 24) return `in ${Math.round(mins / 60)} h`;
  const days = Math.round(mins / 1440);
  return `in ${days} day${days > 1 ? "s" : ""}`;
}

export const plural = (n, word, many = word + "s") => `${n} ${n === 1 ? word : many}`;

// ---------------------------------------------------------------- toast
// A column at the bottom centre, the newest at the bottom, three at most (a fourth pushes the oldest out). Each
// toast has its own close button and its own timer: info goes after 4 s, an error after 8 s (it may need reading);
// the timer waits while the pointer or the keyboard focus is on the toast.
const TOAST_MAX = 3;

export function toast(message, kind = "info") {
  let region = document.getElementById("toasts");
  if (!region) {
    region = h("div", { id: "toasts", class: "toast-region", role: "status", "aria-live": "polite" });
    document.body.append(region);
  }
  let left = kind === "error" ? 8000 : 4000;
  let timer = 0;
  let since = 0;
  const holds = new Set();   // "pointer" and/or "focus"
  const close = () => {
    clearTimeout(timer);
    if (!el.isConnected || el.classList.contains("is-leaving")) return;
    el.classList.add("is-leaving");
    setTimeout(() => el.remove(), 160);
  };
  const schedule = () => {
    if (holds.size && timer) {
      clearTimeout(timer);
      timer = 0;
      left -= Date.now() - since;
    } else if (!holds.size && !timer) {
      since = Date.now();
      timer = setTimeout(close, Math.max(left, 1500));   // back from a pause: time to read it once more
    }
  };
  const hold = (why, on) => () => { if (on) holds.add(why); else holds.delete(why); schedule(); };
  const el = h("div", { class: `toast toast-${kind}`, onpointerenter: hold("pointer", true), onpointerleave: hold("pointer", false),
                        onfocusin: hold("focus", true), onfocusout: hold("focus", false) },
    icon(kind === "error" ? "alert" : "check"),
    h("span", { class: "toast-text" }, message),
    iconButton("x", "Dismiss", { class: "icon-btn-sm toast-close", onclick: close }));
  region.append(el);
  while (region.children.length > TOAST_MAX) region.firstElementChild.remove();
  schedule();
  return el;
}

// ---------------------------------------------------------------- dialog
// A modal <dialog>: showModal() makes the page behind inert (focus stays inside), Esc and the backdrop close it,
// and focus returns to what had it before. Returns { el, body, close(value), closed: Promise<value> }.
let dialogSeq = 0;

export function dialog({ title, body = [], actions = [], className = "", dismissible = true, onClose } = {}) {
  const id = `dlg-title-${++dialogSeq}`;
  openPopover?.close(true);   // a menu or picker left open would wait under the dialog; its anchor gets the focus back
  const opener = document.activeElement;
  const bodyEl = h("div", { class: "dlg-body" }, body);
  // The title takes the focus first (not the close button), so the dialog opens on its beginning
  const titleEl = h("h2", { class: "dlg-title", id, tabindex: "-1" }, title);
  const el = h("dialog", { class: `dlg ${className}`.trim(), "aria-labelledby": id },
    h("header", { class: "dlg-head" },
      titleEl,
      dismissible ? iconButton("x", "Close", { class: "dlg-close", onclick: () => close(null) }) : null),
    bodyEl,
    actions.length ? h("footer", { class: "dlg-actions" }, actions) : null);
  let result = null;
  let resolve;
  const closed = new Promise(r => { resolve = r; });
  const close = value => {
    if (!el.open) return;
    result = value ?? null;
    el.close();
  };
  el.addEventListener("cancel", e => { if (!dismissible) e.preventDefault(); });
  // A click on the backdrop lands on the <dialog> itself (its content box is covered by the inner elements)
  el.addEventListener("mousedown", e => { el.dataset.downOnBackdrop = String(e.target === el); });
  el.addEventListener("click", e => {
    if (dismissible && e.target === el && el.dataset.downOnBackdrop === "true") close(null);
  });
  el.addEventListener("close", () => {
    el.remove();
    if (opener?.isConnected) opener.focus({ preventScroll: true });
    onClose?.(result);
    resolve(result);
  });
  document.body.append(el);
  el.showModal();
  titleEl.focus();
  return { el, body: bodyEl, close, closed };
}

// Resolves true when confirmed. Danger dialogs focus Cancel first, so Enter alone never deletes anything.
export function confirmDialog({ title, body = "", confirmLabel = "Confirm", cancelLabel = "Cancel", danger = false } = {}) {
  const cancel = button(cancelLabel, { variant: "secondary", onclick: () => d.close(false) });
  const ok = button(confirmLabel, { variant: danger ? "danger" : "primary", onclick: () => d.close(true) });
  const d = dialog({ title, className: "dlg-confirm", body: typeof body === "string" ? h("p", {}, body) : body,
                     actions: [cancel, ok] });
  (danger ? cancel : ok).focus();
  return d.closed.then(v => v === true);
}

// A click handler that asks first: onclick: confirmed({ title, body, confirmLabel }, async () => { ... })
export const confirmed = (question, fn) => run(async (...args) => {
  if (await confirmDialog({ danger: true, confirmLabel: "Delete", ...question })) await fn(...args);
});

// ---------------------------------------------------------------- popover and menu
// One popover at a time, positioned under (or above) its anchor. Focus moves inside and Tab cycles there;
// Esc, a click outside, scrolling or resizing close it; focus returns to the anchor.
let openPopover = null;

const FOCUSABLE = "button:not(:disabled), [href], input:not(:disabled), select:not(:disabled), textarea:not(:disabled), [tabindex]:not([tabindex='-1'])";

export function popover(anchor, content, { className = "", role = "dialog", label, placement = "bottom-start", onClose } = {}) {
  openPopover?.close();
  const el = h("div", { class: `popover ${className}`.trim(), role, "aria-label": label, tabindex: "-1" }, content);
  document.body.append(el);
  const place = () => {
    const a = anchor.getBoundingClientRect();
    const p = el.getBoundingClientRect();
    const gap = 6;
    const vw = document.documentElement.clientWidth;
    const vh = window.innerHeight;
    let top = a.bottom + gap;
    if (top + p.height > vh - 8 && a.top - gap - p.height > 8) top = a.top - gap - p.height;
    let left = placement.endsWith("end") ? a.right - p.width : a.left;
    left = Math.max(8, Math.min(left, vw - p.width - 8));
    el.style.setProperty("--x", `${Math.round(left)}px`);
    el.style.setProperty("--y", `${Math.round(Math.max(8, top))}px`);
  };
  place();
  anchor.setAttribute("aria-expanded", "true");
  const onDown = e => { if (!el.contains(e.target) && !anchor.contains(e.target)) close(false); };
  const onKey = e => {
    if (e.key === "Escape") { e.preventDefault(); e.stopPropagation(); close(true); }
    else if (e.key === "Tab") {
      const items = $$(FOCUSABLE, el);
      if (!items.length) return;
      const first = items[0];
      const last = items.at(-1);
      if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
      else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
    }
  };
  const onMove = e => { if (!el.contains(e.target)) close(false); };
  function close(restoreFocus = true) {
    if (!el.isConnected) return;
    el.remove();
    anchor.setAttribute("aria-expanded", "false");
    document.removeEventListener("pointerdown", onDown, true);
    el.removeEventListener("keydown", onKey);
    window.removeEventListener("resize", onMove);
    document.removeEventListener("scroll", onMove, true);
    if (openPopover?.el === el) openPopover = null;
    if (restoreFocus && anchor.isConnected) anchor.focus({ preventScroll: true });
    onClose?.();
  }
  document.addEventListener("pointerdown", onDown, true);
  el.addEventListener("keydown", onKey);
  window.addEventListener("resize", onMove);
  document.addEventListener("scroll", onMove, true);
  (el.querySelector(FOCUSABLE) || el).focus({ preventScroll: true });
  openPopover = { el, close };
  return openPopover;
}

export const popoverOpen = () => !!openPopover;

// items: [{ label, icon, onSelect, danger, disabled }] or "-" for a separator. Arrow keys move between items.
export function menu(anchor, items, { label = "Options", placement = "bottom-end" } = {}) {
  const buttons = [];
  const list = items.map(item => {
    if (item === "-") return h("div", { class: "menu-sep", role: "separator" });
    const b = h("button", { type: "button", role: "menuitem", class: "menu-item" + (item.danger ? " is-danger" : ""),
                            disabled: item.disabled, title: item.title,
                            onclick: () => { pop.close(true); run(item.onSelect)(); } },
      item.icon ? icon(item.icon) : null, h("span", {}, item.label));
    buttons.push(b);
    return b;
  });
  const box = h("div", { class: "menu", onkeydown: e => {
    const enabled = buttons.filter(b => !b.disabled);
    const i = enabled.indexOf(document.activeElement);
    if (e.key === "ArrowDown") { e.preventDefault(); enabled[(i + 1) % enabled.length]?.focus(); }
    else if (e.key === "ArrowUp") { e.preventDefault(); enabled[(i - 1 + enabled.length) % enabled.length]?.focus(); }
    else if (e.key === "Home") { e.preventDefault(); enabled[0]?.focus(); }
    else if (e.key === "End") { e.preventDefault(); enabled.at(-1)?.focus(); }
  } }, list);
  const pop = popover(anchor, box, { className: "popover-menu", role: "menu", label, placement });
  return pop;
}

// ---------------------------------------------------------------- page building blocks
// The header of every page: the drawer button (narrow screens), title, one-line description, actions
export function pageHeader(title, description, ...actions) {
  return h("header", { class: "page-head" },
    iconButton("menu", "Open the sidebar", { class: "drawer-btn", "data-action": "drawer", "aria-controls": "sidebar",
                                              "aria-expanded": "false" }),
    h("div", { class: "page-head-text" },
      h("h1", { class: "page-title" }, title),
      description ? h("p", { class: "page-desc" }, description) : null),
    actions.length ? h("div", { class: "page-actions" }, actions) : null);
}

export const card = (title, ...content) =>
  h("section", { class: "card" }, title ? h("h2", { class: "card-title" }, title) : null, ...content);

export const empty = text => h("p", { class: "empty-line" }, text);

// Grey placeholder rows while a list loads
export const skeleton = (rows = 4) =>
  h("div", { class: "skeleton", "aria-hidden": "true" }, Array.from({ length: rows }, () => h("div", { class: "skeleton-row" })));

export const spinner = () => h("span", { class: "spinner", "aria-hidden": "true" });

// A labelled form control: field("Name", input, "optional hint")
export const field = (label, control, hint) =>
  h("label", { class: "field" }, h("span", { class: "field-label" }, label), control,
    hint ? h("span", { class: "field-hint" }, hint) : null);

export const input = (attrs = {}) => h("input", { type: "text", class: "input", ...attrs });
export const select = (options, attrs = {}) =>
  h("select", { class: "select", ...attrs }, options.map(([value, label]) => h("option", { value: String(value) }, label)));
export const textarea = (value = "", attrs = {}) => h("textarea", { class: "textarea", rows: 3, ...attrs }, value);

// Is the keyboard focus in something the user types into? (single-key shortcuts must not fire there)
export function typing(e) {
  const t = e.target;
  return t instanceof HTMLElement && (t.isContentEditable || /^(INPUT|TEXTAREA|SELECT)$/.test(t.tagName));
}
