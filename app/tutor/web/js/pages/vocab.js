// Vocabulary: the review of due words (a card: Space shows the meaning, 1–4 grade it), the word lists (a few new
// words a day), adding a word, and all words with a search filter.

import { api } from "../api.js";
import { button, confirmed, emit, empty, field, fmtDue, h, iconButton, input, pageHeader, plural, popoverOpen, run, select,
         toast, typing } from "../dom.js";
import { speakText } from "../voice.js";

export const GRADES = [["again", "Again"], ["hard", "Hard"], ["good", "Good"], ["easy", "Easy"]];
const VOCAB_BATCH = 20;   // the server sends at most this many due words at a time
const PACES = [[0, "Paused"], [5, "5 new words a day"], [10, "10 new words a day"], [15, "15 new words a day"],
               [20, "20 new words a day"]];

const vocab = { queue: [], index: 0, revealed: false, grading: false, root: null, box: null, filter: "", nextDue: null };

export async function render(el) {
  vocab.root = el;
  const [due, all, lists] = await Promise.all([api("/api/vocab/due"), api("/api/vocab"), api("/api/word-lists")]);
  if (vocab.root !== el) return;
  const later = all.map(w => w.due_at).filter(d => d && new Date(d) > Date.now()).sort();
  Object.assign(vocab, { queue: due, index: 0, revealed: false, grading: false, nextDue: later[0] || null });
  const again = () => render(el);
  vocab.box = h("section", { class: "card vocab-review", tabindex: "-1", "aria-labelledby": "vocab-review-title" });

  // Word lists: a few new words a day join the review above
  const listCard = lists.length ? h("section", { class: "card vocab-lists" }, h("h2", { class: "card-title" }, "Word lists"),
    lists.map(wl => {
      const pace = select(PACES, { class: "select select-sm", value: String(wl.daily_new), "aria-label": `New words a day from ${wl.title}`,
        onchange: run(async () => {
          await api(`/api/word-lists/${wl.id}`, { method: "PATCH", json: { daily_new: Number(pace.value) } });
          emit("plan");
          await again();
        }) });
      const percent = wl.total ? Math.round(100 * wl.introduced / wl.total) : 0;
      return h("div", { class: "vocab-list" },
        h("div", { class: "vocab-list-head" }, h("strong", {}, wl.title), h("span", { class: "vocab-list-pct" }, `${percent}%`)),
        h("div", { class: "progress", role: "progressbar", "aria-label": `${wl.title}: words started`, "aria-valuenow": percent,
                   "aria-valuemin": 0, "aria-valuemax": 100 },
          h("div", { class: "progress-bar", style: `--value:${percent}%` })),
        h("p", { class: "card-meta" }, `${wl.introduced} of ${wl.total} started · ${wl.introduced_today} new today`),
        wl.description ? h("p", { class: "vocab-list-desc" }, wl.description) : null,
        h("div", { class: "vocab-list-foot" }, h("span", { class: "card-meta" }, "Source: " + wl.source), pace));
    })) : null;

  const wordIn = input({ name: "word", required: true, autocomplete: "off", dir: "auto" });
  const meaningIn = input({ name: "meaning", required: true, autocomplete: "off", dir: "auto" });
  const exampleIn = input({ name: "example", autocomplete: "off", dir: "auto" });
  const addCard = h("form", { class: "card vocab-add", onsubmit: run(async e => {
    e.preventDefault();
    const row = await api("/api/vocab", { method: "POST", json: { word: wordIn.value, meaning: meaningIn.value, example: exampleIn.value } });
    emit("plan");
    toast(`Added "${row.word ?? wordIn.value.trim()}". It is in today's review.`);
    await again();
    vocab.root.querySelector(".vocab-add input")?.focus();   // ready for the next word
  }) }, h("h2", { class: "card-title" }, "Add a word"),
    field("Word or expression", wordIn),
    field("Meaning", meaningIn, "In simple English (or in Arabic, if that is clearer)."),
    field("Example sentence", exampleIn, "Optional: a sentence that uses it."),
    h("div", { class: "form-actions" }, button("Add word", { variant: "primary", type: "submit", icon: "plus" })));

  el.replaceChildren(pageHeader("Vocabulary", "Review the words that are due, and keep adding new ones."),
    h("div", { class: "stack" }, vocab.box, h("div", { class: "vocab-grid" }, listCard, addCard), allWords(all, again)));
  drawFlash();
}

// ---------------------------------------------------------------- all words
function allWords(all, again) {
  const rows = all.map(w => h("tr", { "data-search": [w.word, w.meaning, w.example].join(" ").toLowerCase() },
    h("td", { class: "vocab-cell-word" }, h("strong", { dir: "auto" }, w.word),
      w.list_title ? h("span", { class: "badge" }, w.list_title)
        : w.origin === "auto" ? h("span", { class: "badge badge-accent" }, "from a chat") : null),
    h("td", { class: "vocab-cell-meaning" }, h("div", { dir: "auto" }, w.meaning),
      w.example ? h("div", { class: "card-meta", dir: "auto" }, w.example) : null),
    h("td", { class: "vocab-cell-due card-meta nowrap" }, fmtDue(w.due_at)),
    h("td", { class: "cell-actions" }, iconButton("trash", `Delete "${w.word}"`, { class: "icon-btn-sm icon-btn-danger vocab-delete",
      onclick: confirmed({ title: "Delete this word?", body: `"${w.word}" and its review history will be deleted.` }, async () => {
        await api(`/api/vocab/${w.id}`, { method: "DELETE" });
        emit("plan");
        await again();
      }) }))));
  const noMatch = h("p", { class: "empty-line vocab-no-match", hidden: true });
  const count = h("span", { class: "badge vocab-total" }, plural(all.length, "word"));
  const filter = () => {
    const q = vocab.filter.trim().toLowerCase();
    let shown = 0;
    for (const r of rows) {
      r.hidden = !!q && !r.dataset.search.includes(q);
      if (!r.hidden) shown += 1;
    }
    count.textContent = q ? `${shown} of ${plural(all.length, "word")}` : plural(all.length, "word");
    noMatch.hidden = !!shown;
    noMatch.textContent = `No word matches "${vocab.filter.trim()}".`;
  };
  const search = input({ type: "search", class: "input input-sm vocab-search", placeholder: "Search words", "aria-label": "Search words",
                         value: vocab.filter, oninput: () => { vocab.filter = search.value; filter(); } });
  const box = h("section", { class: "card vocab-all", "aria-labelledby": "vocab-all-title" },
    h("div", { class: "vocab-all-head" }, h("h2", { class: "card-title", id: "vocab-all-title" }, "All words"), count,
      all.length ? search : null),
    all.length ? [h("div", { class: "table-wrap" }, h("table", { class: "table vocab-table" },
      h("thead", {}, h("tr", {}, h("th", {}, "Word"), h("th", {}, "Meaning"), h("th", {}, "Next review"),
                              h("th", {}, h("span", { class: "sr-only" }, "Actions")))),
      h("tbody", {}, rows))), noMatch]
      : empty("Words you ask about or learn in chats are saved here automatically."));
  if (all.length) filter();
  return box;
}

// ---------------------------------------------------------------- the review
function reviewHead(q) {
  const done = Math.min(vocab.index, q.length);
  return [
    h("div", { class: "vocab-review-head" },
      h("h2", { class: "card-title", id: "vocab-review-title" }, "Review"),
      q.length && vocab.index < q.length ? h("span", { class: "vocab-count" }, `${vocab.index + 1} of ${q.length}`) : null),
    q.length ? h("div", { class: "progress vocab-progress", role: "progressbar", "aria-label": "Words reviewed",
                          "aria-valuenow": done, "aria-valuemin": 0, "aria-valuemax": q.length },
      h("div", { class: "progress-bar", style: `--value:${Math.round(100 * done / q.length)}%` })) : null,
  ];
}

// Redraws the card; keyboard focus that was on it stays on it (its buttons are replaced)
function show(...content) {
  const box = vocab.box;
  const hadFocus = box.contains(document.activeElement);
  box.replaceChildren(...content.filter(Boolean));
  if (hadFocus) box.focus({ preventScroll: true });
}

async function drawFlash() {
  const q = vocab.queue;
  if (vocab.index >= q.length) {
    // A full batch is done: more words may be due than the server sent, so ask again before saying "done"
    if (q.length === VOCAB_BATCH) {
      try {
        const more = await api("/api/vocab/due");
        if (vocab.queue !== q) return;   // the page was redrawn meanwhile
        if (more.length) {
          Object.assign(vocab, { queue: more, index: 0, revealed: false });
          drawFlash();
          return;
        }
      } catch (e) { toast(e.message, "error"); }
    }
    show(...reviewHead(q), h("div", { class: "vocab-done" },
      h("p", { class: "vocab-done-title" }, q.length ? "Done for now. Well done!" : "No words are due right now."),
      h("p", { class: "card-meta" }, !q.length && vocab.nextDue ? `The next word comes back ${fmtDue(vocab.nextDue)}.`
        : "Words come back here when they are due.")));
    return;
  }
  const w = q[vocab.index];
  const flash = h("div", { class: "vocab-flash" },
    h("p", { class: "vocab-word", dir: "auto" }, w.word),
    button("Hear it", { icon: "speaker", size: "sm", variant: "ghost", class: "vocab-hear", onclick: () => speakText(w.word) }));
  if (!vocab.revealed) {
    flash.append(h("p", { class: "vocab-prompt" }, "Say what it means and use it in a sentence, then check."),
      h("button", { type: "button", class: "btn btn-primary vocab-reveal", "aria-keyshortcuts": "Space", onclick: reveal },
        "Show meaning", h("kbd", { class: "vocab-kbd", "aria-hidden": "true" }, "Space")));
  } else {
    // filtered: append() would write a missing example as the text "null"
    flash.append(...[h("p", { class: "vocab-meaning", dir: "auto" }, w.meaning),
      w.example ? h("p", { class: "vocab-example", dir: "auto" }, w.example, " ",
        iconButton("play", "Hear the example", { class: "icon-btn-sm", onclick: () => speakText(w.example) })) : null,
      h("p", { class: "vocab-ask", id: "vocab-ask" }, "How well did you know it?"),
      h("div", { class: "vocab-grades", role: "group", "aria-labelledby": "vocab-ask" }, GRADES.map(([g, label], i) =>
        h("button", { type: "button", class: `btn btn-secondary vocab-grade is-${g}`, "data-grade": g,
                      "aria-keyshortcuts": String(i + 1), onclick: () => grade(g) },
          label, h("kbd", { class: "vocab-kbd", "aria-hidden": "true" }, String(i + 1)))))].filter(Boolean));
  }
  show(...reviewHead(q), flash);
}

function reveal() {
  if (vocab.revealed || !vocab.queue[vocab.index]) return;
  vocab.revealed = true;
  drawFlash();
}

// One grade at a time: a quick second key press must not grade the next word unseen
const grade = run(async g => {
  const w = vocab.queue[vocab.index];
  if (!w || !vocab.revealed || vocab.grading) return;
  vocab.grading = true;
  try {
    await api(`/api/vocab/${w.id}/review`, { method: "POST", json: { grade: g } });
  } finally {
    vocab.grading = false;
  }
  if (vocab.queue[vocab.index] !== w) return;   // the page was redrawn meanwhile
  vocab.index += 1;
  vocab.revealed = false;
  emit("plan");
  drawFlash();
});

// Space shows the meaning, 1–4 grade it: while the page is showing, and not while typing or in a dialog
document.addEventListener("keydown", e => {
  const box = vocab.box;
  if (!box?.isConnected || box.closest("[hidden]") || e.defaultPrevented || e.ctrlKey || e.altKey || e.metaKey || typing(e)) return;
  if (document.querySelector("dialog[open]") || popoverOpen() || !vocab.queue[vocab.index]) return;
  if (e.key === " " && !vocab.revealed) {
    if (e.target instanceof Element && e.target.closest("button, a, summary")) return;   // Space presses that button
    e.preventDefault();
    reveal();
  } else if (vocab.revealed && /^[1-4]$/.test(e.key)) {
    e.preventDefault();
    grade(GRADES[Number(e.key) - 1][0]);
  }
});
