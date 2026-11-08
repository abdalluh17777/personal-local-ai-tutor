// Mistakes (DESIGN.md section 4): the learner's English mistakes grouped by rule, as cards with their examples.
// A rule is marked resolved when the learner stops making it (it reopens by itself if it comes back) or deleted.

import { api } from "../api.js";
import { button, confirmed, empty, fmtDate, h, icon, iconButton, pageHeader, plural, run, toast } from "../dom.js";

let root = null;
let seq = 0;

const ruleName = rule => rule.replaceAll("_", " ").replace(/^./, c => c.toUpperCase());

export async function render(el) {
  root = el;
  const items = await api("/api/mistakes");
  if (root !== el) return;
  const again = focusId => render(el).then(() => {
    // After a redraw the keyboard focus goes back to the same card's first button (or the page's heading)
    const target = el.querySelector(`.mist-card[data-id="${focusId}"] .mist-actions .btn`) || el.querySelector(".page-title");
    target?.focus({ preventScroll: false });
  });
  const open = items.filter(m => !m.resolved);
  const resolved = items.filter(m => m.resolved);

  const section = (title, list, note) => {
    const id = `mist-sec-${++seq}`;
    return h("section", { class: "mist-section", "aria-labelledby": id },
      h("div", { class: "mist-section-head" },
        h("h2", { class: "mist-section-title", id }, title),
        h("span", { class: "badge" }, String(list.length))),
      note ? h("p", { class: "card-meta" }, note) : null,
      h("div", { class: "mist-grid" }, list.map(m => mistakeCard(m, again))));
  };

  el.replaceChildren(
    pageHeader("Mistakes", "Your English mistakes, grouped by rule. The tutor practises the frequent ones with you. " +
      "Mark a rule resolved when you stop making it; it reopens by itself if it comes back."),
    items.length
      ? h("div", { class: "mist-page" },
          open.length ? section("Working on", open) : h("section", { class: "card" }, empty("Nothing to work on: every rule is resolved. Well done!")),
          resolved.length ? section("Resolved", resolved, "These come back here by themselves if the mistake appears again.") : null)
      : h("section", { class: "card" }, empty("No mistakes recorded yet. They appear here as you write and talk to the tutor.")));
}

function mistakeCard(m, again) {
  const id = `mist-card-${m.id}`;
  const name = ruleName(m.rule);
  return h("article", { class: "card mist-card" + (m.resolved ? " is-resolved" : ""), "data-id": m.id, "aria-labelledby": id },
    h("div", { class: "mist-head" },
      h("h3", { class: "mist-title", id }, name),
      h("span", { class: "mist-count", title: `Seen ${plural(m.count, "time")}` }, `${m.count}×`,
        h("span", { class: "sr-only" }, ` (seen ${plural(m.count, "time")})`))),
    h("div", { class: "mist-meta" },
      h("span", { class: "badge" }, m.category.replaceAll("_", " ")),
      m.resolved ? h("span", { class: "badge badge-good" }, icon("check"), "resolved") : null,
      h("span", { class: "card-meta" }, `last seen ${fmtDate(m.last_seen)}`)),
    m.explanation ? h("p", { class: "mist-explain", dir: "auto" }, m.explanation) : null,
    m.examples.length ? h("ul", { class: "mist-examples", "aria-label": "Examples" }, m.examples.map(x => h("li", {},
      h("span", { class: "sr-only" }, "You wrote: "), h("span", { class: "wrong", dir: "auto" }, x.wrong),
      h("span", { class: "mist-arrow", "aria-hidden": "true" }, "→"),
      h("span", { class: "sr-only" }, ", better: "), h("span", { class: "right", dir: "auto" }, x.right)))) : null,
    h("div", { class: "mist-actions" },
      button(m.resolved ? "Reopen" : "Mark resolved", { size: "sm", icon: m.resolved ? "refresh" : "check",
        onclick: run(async () => {
          await api(`/api/mistakes/${m.id}`, { method: "PATCH", json: { resolved: !m.resolved } });
          toast(m.resolved ? `"${name}" is back in your focus list` : `"${name}" marked resolved`);
          await again(m.id);
        }) }),
      iconButton("trash", "Delete", { class: "icon-btn-sm icon-btn-danger", "aria-label": `Delete "${name}"`,
        onclick: confirmed({
          title: "Delete this mistake?", body: `"${name}" and its examples will be deleted.`,
        }, async () => {
          await api(`/api/mistakes/${m.id}`, { method: "DELETE" });
          toast(`"${name}" deleted`);
          await again(null);
        }) })));
}
