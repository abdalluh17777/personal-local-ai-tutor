// Boot, routing (#hash), the shell (sidebar, drawer, model status, badges, profile) and the page registry.

import { api } from "./api.js";
import { initChat, newChat, refreshSessions, showChat } from "./chat.js";
import { $, $$, button, card, h, on, pageHeader, remembered, skeleton, toast, typing } from "./dom.js";
import { applyTheme, openSettings } from "./settings.js";
import { toggleShortcuts } from "./shortcuts.js";
import * as courses from "./pages/courses.js";
import * as home from "./pages/home.js";
import * as memory from "./pages/memory.js";
import * as mistakes from "./pages/mistakes.js";
import * as pronunciation from "./pages/pronunciation.js";
import * as vocab from "./pages/vocab.js";

// Every page module exports render(root): it fills the element it is given (a fresh one per visit)
const PAGES = { home, courses, vocabulary: vocab, pronunciation, mistakes, memory };
const TITLES = { chat: "Chat", home: "Home", courses: "Courses", vocabulary: "Vocabulary",
                 pronunciation: "Pronunciation", mistakes: "Mistakes", memory: "Memory" };
// Addresses from the previous version of the app keep working (bookmarks)
const ALIASES = { study: "chat", today: "home", vocab: "vocabulary", pron: "pronunciation" };
const SETTINGS_HASHES = ["settings", "profile"];

const app = $("#app");
const narrow = window.matchMedia("(max-width: 899px)");
let current = "";

// hasOwn: "#constructor" must not reach Object.prototype
function resolve(hash) {
  const name = hash.replace(/^#/, "");
  const route = Object.hasOwn(ALIASES, name) ? ALIASES[name] : name;
  return route === "chat" || Object.hasOwn(PAGES, route) ? route : null;
}

// ---------------------------------------------------------------- routing
async function show(route) {
  current = route;
  // The address follows the page, so a reload or a bookmark opens it again (replaced: no history entry per click)
  history.replaceState(null, "", "#" + route);
  document.title = route === "chat" ? "Tutor" : `${TITLES[route]} · Tutor`;
  for (const a of $$(".sb-item")) {
    if (a.dataset.route === route) a.setAttribute("aria-current", "page");
    else a.removeAttribute("aria-current");
  }
  closeDrawer(false);
  const isChat = route === "chat";
  $("#view-chat").hidden = !isChat;
  $("#view-page").hidden = isChat;
  if (isChat) {
    try { await showChat(); } catch (e) { toast(e.message, "error"); }
  } else {
    // A fresh element per visit: a slow page that finishes after the learner moved on fills a detached element
    const root = h("div", { class: "page" });
    const host = $("#page-host");
    host.replaceChildren(root);
    host.scrollTop = 0;
    const slow = setTimeout(() => { if (!root.children.length) root.append(loadingPage(route)); }, 150);
    try {
      await PAGES[route].render(root);
    } catch (e) {
      if (root.isConnected) root.replaceChildren(failedPage(route, e));
      toast(e.message, "error");
    } finally {
      clearTimeout(slow);
    }
  }
  refreshBadges();
}

const loadingPage = route => h("div", {}, pageHeader(TITLES[route]),
  h("div", { class: "stack" }, card(null, skeleton(3)), card(null, skeleton(4))));

const failedPage = (route, e) => h("div", {}, pageHeader(TITLES[route]),
  card(null, h("p", { class: "text-bad" }, `This page could not be loaded: ${e.message}`),
    button("Try again", { icon: "refresh", onclick: () => show(route) })));

function onHash() {
  const name = location.hash.slice(1);
  if (SETTINGS_HASHES.includes(name)) {
    history.replaceState(null, "", "#" + (current || "chat"));
    if (!current) show("chat");
    openSettings();
    return;
  }
  const route = resolve(name);
  if (route && route !== current) show(route);
  else history.replaceState(null, "", "#" + (current || "chat"));
}

// ---------------------------------------------------------------- sidebar: rail and drawer
function setCollapsed(collapsed) {
  app.classList.toggle("sb-collapsed", collapsed);
  remembered.set("sidebar", collapsed ? "collapsed" : "open");
  const b = $("#sb-collapse");
  const label = collapsed ? "Expand the sidebar" : "Collapse the sidebar";
  b.setAttribute("aria-expanded", String(!collapsed));
  b.setAttribute("aria-label", label);
  b.title = `${label} ([)`;
}

let drawerOpener = null;

function openDrawer(opener) {
  drawerOpener = opener || document.activeElement;
  app.classList.add("sb-open");
  $("#sb-scrim").hidden = false;
  syncDrawer();
  $("#sb-new-chat").focus();
}

function closeDrawer(restoreFocus = true) {
  if (!app.classList.contains("sb-open")) return;
  app.classList.remove("sb-open");
  $("#sb-scrim").hidden = true;
  syncDrawer();
  if (restoreFocus && drawerOpener?.isConnected) drawerOpener.focus();
}

// On narrow screens the sidebar is off-canvas: unreachable by Tab while closed, and the only reachable part while open
function syncDrawer() {
  const open = app.classList.contains("sb-open");
  $("#sidebar").inert = narrow.matches && !open;
  $("main").inert = narrow.matches && open;
  for (const b of $$("[data-action=drawer]")) b.setAttribute("aria-expanded", String(narrow.matches && open));
}

function toggleSidebar() {
  if (narrow.matches) {
    if (app.classList.contains("sb-open")) closeDrawer();
    else openDrawer();
  } else {
    setCollapsed(!app.classList.contains("sb-collapsed"));
  }
}

function initShell() {
  setCollapsed(remembered.get("sidebar") === "collapsed");
  $("#sb-collapse").addEventListener("click", () => setCollapsed(!app.classList.contains("sb-collapsed")));
  $("#sb-search-btn").addEventListener("click", () => { setCollapsed(false); $("#sb-search").focus(); });
  $("#sb-new-chat").addEventListener("click", () => newChat());
  $("#sb-user").addEventListener("click", () => openSettings());
  $("#sb-scrim").addEventListener("click", () => closeDrawer());
  for (const a of $$(".sb-item, .sb-brand")) {
    a.addEventListener("click", e => {
      if (e.button !== 0 || e.ctrlKey || e.metaKey || e.shiftKey || e.altKey) return;   // new tab: the browser's
      e.preventDefault();
      show(a.dataset.route);
    });
  }
  // Drawer buttons are in the chat's top bar and in every page header (built later), so clicks are delegated
  document.addEventListener("click", e => {
    const b = e.target.closest("[data-action=drawer]");
    if (b) openDrawer(b);
  });
  narrow.addEventListener("change", () => { closeDrawer(false); syncDrawer(); });
  syncDrawer();

  document.addEventListener("keydown", e => {
    if (e.ctrlKey && e.key === ",") { e.preventDefault(); openSettings(); return; }
    // The list of shortcuts, also over a dialog (Settings); the key by its place too, for a non-English layout
    if (e.ctrlKey && !e.altKey && !e.metaKey && (e.key === "/" || e.code === "Slash")) { e.preventDefault(); toggleShortcuts(); return; }
    if (document.querySelector("dialog[open]") || e.defaultPrevented) return;
    if (e.key === "Escape" && app.classList.contains("sb-open")) closeDrawer();
    else if (e.key === "[" && !typing(e) && !e.ctrlKey && !e.altKey && !e.metaKey) { e.preventDefault(); toggleSidebar(); }
  });

  window.addEventListener("hashchange", onHash);
  on("navigate", route => show(route));
  on("status", showStatus);
  on("status-refresh", refreshStatus);
  on("plan", refreshBadges);
  on("profile", showProfile);
  // A safety net: an error no handler caught still reaches the learner instead of failing silently
  window.addEventListener("unhandledrejection", e => toast(e.reason?.message || String(e.reason), "error"));
}

// ---------------------------------------------------------------- model status, badges, the learner's name
function showStatus(s) {
  let label;
  let kind = "";
  if (!s) { label = "Tutor server not reachable"; kind = "bad"; }
  else if (!s.ollama) { label = "Ollama is not running"; kind = "bad"; }
  else if (!s.installed) { label = `Model "${s.model}" is not installed`; kind = "bad"; }
  else if (s.loaded && s.gpu_percent >= 100) { label = `On GPU 100% · ${Number(s.vram_gb).toFixed(1)} GB`; kind = "ok"; }
  else if (s.loaded) { label = `Warning: only ${s.gpu_percent}% on GPU`; kind = "bad"; }
  else label = "Model ready · loads with your first message";
  const el = $("#model-status");
  el.className = `status-pill ${kind}`.trim();
  el.title = label;
  el.querySelector(".status-text").textContent = label;
}

async function refreshStatus() {
  let s = null;
  try { s = await api("/api/status"); } catch { /* shown as "not reachable" */ }
  showStatus(s);
  return s;
}

function setBadge(id, n, what) {
  const el = $(id);
  el.hidden = !n;
  el.replaceChildren(n > 99 ? "99+" : String(n), h("span", { class: "sr-only" }, ` ${what}`));
}

let badgeTimer = 0;
function refreshBadges() {
  // Several events can ask at once (a review graded, a page shown): one request for them all
  clearTimeout(badgeTimer);
  badgeTimer = setTimeout(async () => {
    try {
      const plan = await api("/api/plan");
      setBadge("#badge-home", plan.due_topics.length, "topics due");
      setBadge("#badge-vocabulary", plan.due_vocab, "words due");
    } catch { /* the status pill already says when the server is unreachable */ }
  }, 100);
}

function showProfile(p) {
  const name = (p?.name || "").trim();
  $("#sb-name").textContent = name || "Your profile";
  $("#sb-avatar").textContent = name ? [...name][0].toUpperCase() : "?";
}

// ---------------------------------------------------------------- boot
applyTheme();
initShell();
initChat();
refreshStatus();
setInterval(refreshStatus, 15000);
refreshSessions();
api("/api/profile").then(showProfile).catch(() => showProfile(null));
const start = location.hash.slice(1);
if (SETTINGS_HASHES.includes(start)) {
  show("chat");
  openSettings();
} else {
  show(resolve(start) || "chat");
}
