// The keyboard shortcuts (DESIGN.md section 2, "Keyboard"): a small dialog that lists them, opened with Ctrl+/ from
// anywhere and from Settings › General. The keys themselves are handled where they act (main.js, chat.js, live.js,
// pages/vocab.js); this list only says what they are.

import { dialog, h } from "./dom.js";

// [keys, what they do]: "+" joins keys pressed together, "–" a range
const GROUPS = [
  ["In the chat", [
    ["Enter", "Send the message"],
    ["Shift+Enter", "New line"],
    ["Ctrl+Space", "Talk: start and finish a voice message (in voice mode: mute)"],
    ["Ctrl+Shift+Space", "Open or end voice mode"],
    ["Esc", "Cancel the recording or stop reading aloud; close a menu or a dialog; end voice mode"],
    ["/", "Go to the message box"],
    ["Ctrl+K", "Choose the course and topic"],
  ]],
  ["Anywhere", [
    ["[", "Collapse or expand the sidebar (open it on a small screen)"],
    ["Ctrl+,", "Settings"],
    ["Ctrl+/", "This list"],
  ]],
  ["Vocabulary review", [
    ["Space", "Show the meaning"],
    ["1–4", "Grade the word: Again, Hard, Good, Easy"],
  ]],
];

// "Ctrl+Shift+Space" → <kbd>Ctrl</kbd>+<kbd>Shift</kbd>+<kbd>Space</kbd>
const keys = combo => combo.split(/(?<=.)([+–])(?=.)/).map((part, i) =>
  (i % 2 ? h("span", { class: "keys-join" }, part) : h("kbd", { class: "kbd" }, part)));

let open = null;

export function openShortcuts() {
  if (open) return open;
  open = dialog({
    title: "Keyboard shortcuts",
    className: "dlg-keys",
    body: GROUPS.map(([title, rows]) => h("section", { class: "keys-group" },
      h("h3", { class: "keys-title" }, title),
      h("dl", { class: "keys-list" }, rows.map(([combo, what]) => h("div", { class: "keys-row" },
        h("dt", { class: "keys-combo" }, keys(combo)),
        h("dd", { class: "keys-what" }, what)))))),
    onClose: () => { open = null; },
  });
  return open;
}

// Ctrl+/ opens the list, and closes it again
export function toggleShortcuts() {
  if (open) open.close(null);
  else openShortcuts();
}
