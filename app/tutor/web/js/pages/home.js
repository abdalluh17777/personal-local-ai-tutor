// Home: what to study now (DESIGN.md section 4). The last chat and its next step, topics due for review, the next new
// topics, words to review, the weakest sounds and the most frequent English mistakes. Every card has a one-line
// empty state; nothing is ever a blank card.

import { api } from "../api.js";
import { openSession, startSession } from "../chat.js";
import { button, emit, empty, fmtDate, fmtDue, h, icon, navigate, pageHeader, plural, run, toast } from "../dom.js";
import { openSettings } from "../settings.js";

// The same four grades as the vocabulary review (POST /api/topics/{id}/review)
const GRADES = [["again", "Again"], ["hard", "Hard"], ["good", "Good"], ["easy", "Easy"]];
let root = null;
let cardSeq = 0;

function greeting(name) {
  const hour = new Date().getHours();
  const part = hour >= 5 && hour < 12 ? "Good morning" : hour >= 12 && hour < 18 ? "Good afternoon" : "Good evening";
  return name ? `${part}, ${name}` : part;
}

const where = s => (s.course_name ? s.course_name + (s.topic_title ? " › " + s.topic_title : "") : "Free conversation");
const ruleName = rule => rule.replaceAll("_", " ").replace(/^./, c => c.toUpperCase());

// What a weak sound most often became; an empty "heard" means the sound was left out
function insteadNote(s) {
  if (s.instead?.[0] === "") return "often left out";
  return s.instead_ipa?.[0] && s.instead_ipa[0] !== "nothing" ? `often said as /${s.instead_ipa[0]}/` : "";
}

// "right 40% of 5 times", and under it "often left out": the side column is narrow, so each fact has its own line
const soundNote = (...facts) => facts.filter(Boolean).map(f => h("span", { class: "home-sound-fact" }, f));

// The pronunciation page opens the drill named in the address (#pronunciation/<drill id>)
const practise = drillId => navigate(`pronunciation/${drillId}`);

// A chat on a topic with an opening message waiting in the composer (the learner reads it, then sends it)
const startOn = (courseId, topicId, prefill) => run(async () => {
  await startSession(courseId, topicId, prefill);
  navigate("chat");
});

// A Home card: an icon, the title, an optional count and an optional action on the right
function homeCard({ title, iconName, count, action, className = "" }, ...content) {
  const id = `home-card-${++cardSeq}`;
  return h("section", { class: `card home-card ${className}`.trim(), "aria-labelledby": id },
    h("div", { class: "home-card-head" },
      h("span", { class: "home-card-icon", "aria-hidden": "true" }, icon(iconName)),
      h("h2", { class: "home-card-title", id }, title),
      count ? h("span", { class: "badge badge-accent home-count" }, String(count)) : null,
      action ? h("div", { class: "home-card-action" }, action) : null),
    ...content);
}

const allLink = (label, route) => button(label, { variant: "ghost", size: "sm", onclick: () => navigate(route) });

export async function render(el) {
  root = el;
  const [plan, courses, profile, sessions, pron] = await Promise.all([
    api("/api/plan"), api("/api/courses"), api("/api/profile"), api("/api/sessions"),
    // Pronunciation progress is optional on Home: the card says so instead of failing the whole page
    api("/api/pronunciation/progress").catch(() => null)]);
  if (root !== el) return;   // another render started meanwhile
  const again = () => render(el);

  const firstRun = !profile.name || !courses.length || !courses.some(c => c.topics);
  const main = [
    firstRun ? setupCard(profile, courses) : null,
    continueCard(sessions),
    reviewsCard(plan.due_topics, again),
    nextTopicsCard(plan.next_topics),
  ];
  const side = [vocabCard(plan.due_vocab), soundsCard(pron), focusCard(plan.focus_mistakes)];

  el.replaceChildren(
    pageHeader(greeting(profile.name), "What to study now: your last chat, reviews, new topics, words and sounds."),
    h("div", { class: "home-body" }, h("div", { class: "home-layout" },
      h("div", { class: "home-col home-main" }, main),
      h("div", { class: "home-col home-side" }, side))));
}

// ---------------------------------------------------------------- the cards
function setupCard(profile, courses) {
  const step = (done, label, action) => h("li", { class: "home-step" + (done ? " is-done" : "") },
    h("span", { class: "home-step-mark", "aria-hidden": "true" }, done ? icon("check") : null),
    h("span", { class: "sr-only" }, done ? "Done: " : "To do: "),
    h("button", { type: "button", class: "link-btn", onclick: action }, label));
  return homeCard({ title: "Getting started", iconName: "spark", className: "home-setup" },
    h("ol", { class: "home-steps" },
      step(!!profile.name, "Fill in your profile: name, English level, goals", () => openSettings()),
      step(courses.length > 0, "Create a course and add its material", () => navigate("courses")),
      step(courses.some(c => c.topics), "Add topics, or let the tutor suggest them from the material", () => navigate("courses")),
      step(false, "Start studying", () => navigate("chat"))));
}

function continueCard(sessions) {
  const open = sessions.find(s => !s.ended_at && s.message_count);
  if (open) {
    return homeCard({ title: "Continue", iconName: "refresh", className: "home-continue" },
      h("p", { class: "home-continue-title" }, open.title || where(open)),
      h("p", { class: "card-meta" }, `${where(open)} · in progress since ${fmtDate(open.started_at)}`),
      h("div", { class: "home-actions" },
        button("Back to the chat", { variant: "primary", icon: "chevron-right", onclick: run(async () => {
          if (await openSession(open.id)) navigate("chat");
        }) })));
  }
  // The most recently ended chat with a summary (the same one GET /api/plan calls last_session)
  const last = sessions.filter(s => s.ended_at && s.summary).sort((a, b) => b.ended_at.localeCompare(a.ended_at))[0];
  if (!last) {
    return homeCard({ title: "Continue", iconName: "refresh", className: "home-continue" },
      empty("Your chats will show up here, with what to do next. Start your first one whenever you like."),
      h("div", { class: "home-actions" }, button("Start a chat", { variant: "primary", icon: "plus", onclick: () => navigate("chat") })));
  }
  // A course or topic deleted since then has no name any more: the new chat must not ask for it
  const courseId = last.course_name ? last.course_id : null;
  const topicId = last.course_name && last.topic_title ? last.topic_id : null;
  const prefill = last.next_step ? `Let's continue from last time. ${last.next_step}` : "Let's continue where we left off last time.";
  return homeCard({ title: "Continue", iconName: "refresh", className: "home-continue" },
    h("p", { class: "home-continue-title" }, last.title || where(last)),
    h("p", { class: "card-meta" }, `${where(last)} · ended ${fmtDate(last.ended_at)}`),
    h("p", { class: "home-summary", dir: "auto" }, last.summary),
    last.next_step ? h("div", { class: "home-next" },
      h("span", { class: "home-next-label" }, "Next step"),
      h("span", { dir: "auto" }, last.next_step)) : null,
    h("div", { class: "home-actions" },
      button(last.next_step ? "Continue with the next step" : "Continue", { variant: "primary", icon: "chevron-right",
        onclick: startOn(courseId, topicId, prefill) }),
      button("View that chat", { variant: "ghost", onclick: run(async () => {
        if (await openSession(last.id)) navigate("chat");
      }) })));
}

function reviewsCard(topics, again) {
  if (!topics.length) {
    return homeCard({ title: "Due reviews", iconName: "check" }, empty("Nothing to review right now. Nice work."));
  }
  const grade = (t, g) => run(async () => {
    const r = await api(`/api/topics/${t.id}/review`, { method: "POST", json: { grade: g } });
    toast(`"${t.title}": next review ${fmtDue(r.due_at)}`);
    emit("plan");
    await again();
    // The keyboard goes on with the next topic due, if there is one
    root?.querySelector(".home-review .home-grades .btn")?.focus();
  });
  return homeCard({ title: "Due reviews", iconName: "check", count: topics.length },
    h("p", { class: "card-meta" }, "Recall the main ideas first, then rate how well you remembered."),
    h("ul", { class: "home-list" }, topics.map(t => h("li", { class: "home-review" },
      h("div", { class: "home-row-text" },
        h("strong", { dir: "auto" }, t.title),
        h("span", { class: "card-meta" }, `${t.course_name} · ${fmtDue(t.due_at)}`)),
      h("div", { class: "home-review-actions" },
        h("div", { class: "home-grades", role: "group", "aria-label": `How well did you remember "${t.title}"?` },
          GRADES.map(([g, label]) => button(label, { size: "sm", onclick: grade(t, g) }))),
        button("Review with tutor", { variant: "primary", size: "sm", onclick: startOn(t.course_id, t.id,
          `Let's review "${t.title}". Ask me questions so I can recall it before you explain anything.`) }))))));
}

function nextTopicsCard(topics) {
  if (!topics.length) {
    return homeCard({ title: "Next new topics", iconName: "book", action: allLink("Courses", "courses") },
      empty("No new topics waiting. Add some in Courses, or let the tutor suggest them."));
  }
  return homeCard({ title: "Next new topics", iconName: "book", count: topics.length },
    h("ul", { class: "home-list" }, topics.map(t => h("li", { class: "home-row" },
      h("div", { class: "home-row-text" },
        h("strong", { dir: "auto" }, t.title),
        h("span", { class: "card-meta" }, t.course_name)),
      button("Start", { variant: "primary", size: "sm", "aria-label": `Start "${t.title}"`, onclick: startOn(t.course_id, t.id,
        `Let's start "${t.title}". Explain the main idea, then check my understanding.`) })))));
}

function vocabCard(due) {
  return homeCard({ title: "Vocabulary", iconName: "cards" },
    due
      ? h("div", { class: "home-stat" },
          h("span", { class: "home-stat-num" }, String(due)),
          h("span", { class: "home-stat-label" }, `${due === 1 ? "word" : "words"} to review`),
          button("Review words", { variant: "primary", size: "sm", onclick: () => navigate("vocabulary") }))
      : empty("No words to review right now. New ones arrive each day."));
}

function soundsCard(pron) {
  const action = allLink("All sounds", "pronunciation");
  if (!pron) return homeCard({ title: "Pronunciation focus", iconName: "waveform", action }, empty("Your sounds could not be loaded just now."));
  const weak = pron.sounds.filter(s => s.accuracy < 0.8).slice(0, 3);
  if (!weak.length) {
    return homeCard({ title: "Pronunciation focus", iconName: "waveform", action },
      empty(pron.sounds.length ? "No weak sounds right now. Well said!"
                               : "After a few pronunciation checks, your weakest sounds show up here."));
  }
  return homeCard({ title: "Pronunciation focus", iconName: "waveform", action },
    h("ul", { class: "home-list home-sounds" }, weak.map(s => {
      const pct = Math.round(100 * s.accuracy);
      const bar = h("div", { class: "progress is-low", role: "img", "aria-label": `${pct}% right` }, h("div", { class: "progress-bar" }));
      bar.firstChild.style.setProperty("--value", `${pct}%`);
      return h("li", { class: "home-row home-sound" },
        h("span", { class: "home-ipa", lang: "en-fonipa" }, `/${s.ipa}/`),
        h("div", { class: "home-row-text" }, bar,
          h("span", { class: "card-meta" }, soundNote(`right ${pct}% of ${plural(s.total, "time")}`, insteadNote(s)))),
        s.drill ? button("Practise", { size: "sm", "aria-label": `Practise /${s.ipa}/`, onclick: () => practise(s.drill) }) : null);
    })));
}

function focusCard(mistakes) {
  const action = allLink("All mistakes", "mistakes");
  if (!mistakes.length) {
    return homeCard({ title: "English focus", iconName: "alert", action }, empty("No repeated mistakes right now. Keep writing!"));
  }
  return homeCard({ title: "English focus", iconName: "alert", action },
    h("ul", { class: "home-list" }, mistakes.map(m => {
      const x = m.examples[0];
      return h("li", { class: "home-row" },
        h("div", { class: "home-row-text" },
          h("strong", {}, ruleName(m.rule)),
          x ? h("span", { class: "home-example" },
            h("span", { class: "sr-only" }, "Wrong: "), h("span", { class: "wrong", dir: "auto" }, x.wrong),
            h("span", { "aria-hidden": "true" }, " → "),
            h("span", { class: "sr-only" }, ", right: "), h("span", { class: "right", dir: "auto" }, x.right)) : null),
        h("span", { class: "badge", title: `Seen ${plural(m.count, "time")}` }, `${m.count}×`,
          h("span", { class: "sr-only" }, ` (seen ${plural(m.count, "time")})`)));
    })));
}
