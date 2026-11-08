# Personal Tutor — UI design specification

The web app is a single page served by `app/tutor/server.py` from this folder. No build step, no CDN, no
frameworks: ES modules (`<script type="module">`), one stylesheet, inline SVG icons. Everything works offline.
The learner is an Arabic-speaking adult studying alone on a Windows PC (Chrome `--app=` window, also usable on a
phone over the LAN is not a goal). The UI is in English (the learner is immersed in English); the tutor's text
may contain Arabic, which must render right-to-left correctly inside left-to-right pages.

The target experience is the one of ChatGPT and Gemini: a quiet, spacious chat as the centre of the app, a
sidebar with the conversation history, a floating composer, a full-screen voice mode, settings in a dialog,
light and dark themes. Everything else the app does (courses, vocabulary, pronunciation, mistakes, memory) is a
page reached from the sidebar, designed with the same components.

## 1. Shell

```
┌──────────────┬───────────────────────────────────────────────────────┐
│ ≡  Tutor     │ [Course › Topic ▾]                      🔈   ⋯        │  top bar (chat only)
│ + New chat   │                                                       │
│ 🔍 Search     │                 ┌───────────────────────┐             │
│              │                 │  centred column        │             │
│ Today        │                 │  max-width 768px       │             │
│  • Lesson 5… │                 │  messages              │             │
│ Yesterday    │                 │                        │             │
│  • Pronunc…  │                 └───────────────────────┘             │
│  • …         │                                                       │
│              │        ┌────────────────────────────────┐             │
│ Home         │        │ 🎤 ⫶  Message the tutor…    ➤   │  composer   │
│ Courses      │        └────────────────────────────────┘             │
│ Vocabulary   │          Enter to send · Shift+Enter new line          │
│ Pronunciation│                                                       │
│ Mistakes     │                                                       │
│ Memory       │                                                       │
│ ⚙ Abdullah   │                                                       │
│ ● GPU 100%   │                                                       │
└──────────────┴───────────────────────────────────────────────────────┘
```

- **Sidebar** (`<nav aria-label="Main">`), 272 px, collapsible to a 56 px icon rail (button at the top, state
  remembered in localStorage, `[` shortcut). Below 900 px it becomes an off-canvas drawer opened by a ≡ button
  in the top bar and closed by Esc, a click outside, or choosing an item.
  - Top: product name "Tutor" (serif), collapse button.
  - **New chat** button (primary, full width, `+` icon).
  - **Search chats**: a filter field that narrows the list below as you type (client side).
  - **Chats**: the sessions from `GET /api/sessions`, grouped by local date: *Today*, *Yesterday*, *Previous 7
    days*, *Older*. Each row: title (the topic title, else the first user message trimmed to 60 characters,
    else "New chat"), the course name in muted small text; the open session shows a small green dot and is
    highlighted. Hover (or focus) reveals a `⋯` menu: *Rename* (inline edit of the title), *Continue this
    chat* (for an ended session: reopens it, ending the currently open one first, and the composer comes back),
    *Delete* (confirm dialog). Clicking a row opens it in the chat view (read-only when ended, with its summary
    card and a **Continue this chat** button). The list virtualises nothing: at most 100 rows.
  - **Pages**: Home, Courses, Vocabulary, Pronunciation, Mistakes, Memory, each with an icon; the active one
    highlighted. Badges: due reviews count on Home, due words on Vocabulary.
  - **Footer**: the learner's name and avatar circle (initial) opening **Settings** (section 8); under it the
    model status pill (`● On GPU 100% · 6.0 GB`, red when not, from `GET /api/status`).
- **Main** (`<main>`): the chat view, or a page. Pages have a header (title, one-line description, actions on
  the right) and a content area with max-width 980 px, centred, padding 24–32 px.
- **Top bar** (chat view only, 56 px): left the ≡ (drawer) button on small screens and the **context picker**;
  right the **read-aloud** toggle (speaker icon, pressed state) and a `⋯` menu (End session, Rename, Delete
  chat). The voice-mode button lives in the composer (section 2), not here.

## 2. Chat

### Context picker
A pill button `Course › Topic ▾` (or `Choose a course and topic`) opening a popover: two columns on wide
screens (courses | topics of the selected course), stacked on narrow ones; topics show their status badge
(new / learning / reviewing) and position number. Choosing a topic while no session is open just sets the
context for the next message (the session is created on the first message, with `POST /api/sessions`). Choosing
a different topic while a session is open ends it (confirm in a small dialog: "End this chat and start one on
<topic>?") and starts a new one. "No course" is allowed (free conversation).

### Welcome state (no open session)
Centred, vertically balanced: greeting by time of day ("Good evening, Abdullah"), one line of what the tutor
does, and up to four **suggestion cards** from `GET /api/plan`: *Continue* (last session's next step, if any),
*Next lesson* (next new topic: starts a session on it with the standard opening message), *Reviews due* (N
topics / words), *Practise pronunciation* (weakest sound, or the drills page). Clicking a card performs the
action. The composer sits below the cards. On the first ever run (no profile name, no courses) the cards are
the three setup steps instead.

### Messages
- Column max-width 768 px, 24 px gutters, 20 px between turns.
- **Assistant**: no bubble. A 28 px round mark (the app's spark icon) at the left on wide screens, then the
  text at 16 px / 1.7 line-height, markdown rendered (headings, lists, code, bold/italic, links). Citations
  `[S1]` render as small numbered chips; hovering shows the source name, clicking opens the **Sources** panel
  (section 2, below) scrolled to that passage. Under the text, a quiet **action row** visible on hover/focus:
  Copy, Read aloud (▶/■), Sources (n), and for the last reply only, Stop (while streaming). After a reply that
  cited nothing from the material but the course has passages, nothing is shown (no "general knowledge" label).
- **User**: right-aligned bubble (surface-2 background, 18 px radius, max-width 80%, `white-space: pre-wrap`,
  `dir="auto"`). Under it, the **English check**: while pending, a tiny spinner chip "Checking English…"; then
  either a green chip "✓ Good English" or an amber card "2 corrections" that is **open by default** (the
  learner watches their mistakes as the conversation goes, spoken turns included): the list (`wrong → right`,
  rule in small text, explanation) and the better phrasing with a **Use it** button that puts it in the
  composer. The card's header collapses it for that message only; the default is always open. Saved
  words/memories appear as small "Saved: *word*" chips.
- **Streaming**: tokens appended live with a blinking caret at the end; the view auto-scrolls while the user is
  at the bottom and shows a "↓ Jump to latest" pill otherwise. **Stop** aborts the fetch (`AbortController`);
  the server keeps the partial reply (it already does).
- **Errors**: a red inline card under the failed turn with a Retry button (re-sends the same text).
- **Ended session**: a card with the summary and next step, "This chat has ended" and a **New chat** button; the
  composer is hidden. Past sessions (from the sidebar) open read-only in the same way: `GET /api/sessions/{id}`.
- **Sources panel**: a right-side drawer (360 px; a bottom sheet on narrow screens) listing the passages the
  reply was given, cited ones first and marked, each with file name, page and excerpt. Esc closes.

### Composer
A floating rounded container (24 px radius, surface, 1 px border, soft shadow) 40 px above the bottom, same
max-width as the column. Inside, left to right: the **mic** button (push-to-talk), the **voice mode** button
(waveform icon, tooltip "Voice mode · Ctrl+Shift+Space"), a `textarea` that grows to 8 lines then scrolls
(placeholder "Message the tutor…"), and **send** (right, primary, disabled when empty; becomes a **stop**
button while streaming). Under it, one line of hints in muted 12 px: "Enter to send · Shift+Enter for a new
line · Ctrl+Space to talk". The mic button: click toggles recording (red, pulsing ring driven by the input
level, elapsed time in the hint line, Esc cancels); when the transcript arrives it is inserted into the
textarea and, if "send after speaking" is on, sent. Transcribing shows a spinner in the mic button. Arabic
typed text renders right-to-left (`dir="auto"`).

While **voice mode** is on (section 3) the same composer stays in place and usable, pared down to ChatGPT's
voice composer: the placeholder reads "Type"; at the right a round **mute** toggle (mic-off icon; pressed =
muted, filled) and a round **×** "End voice mode" button in inverted colours (light circle, dark × on the dark
theme; the reverse on the light theme); the **send** button appears only when the box has text; the mic
push-to-talk button and the voice-mode button are hidden; the hint line under the composer is hidden. Text
sent during voice mode goes over the live connection (section 3) and is answered aloud like a spoken turn.

### Keyboard
Enter send, Shift+Enter newline, Ctrl+Space talk, Ctrl+Shift+Space voice mode, Esc cancel/close, `/` focuses the
composer from anywhere in the chat view, `[` toggles the sidebar, Ctrl+K opens the context picker, `Ctrl+,`
Settings; Space and 1–4 in the vocabulary review. **Ctrl+/** opens a small "Keyboard shortcuts" dialog listing
them all (`js/shortcuts.js`; also linked from Settings › General). Tab order in the chat: sidebar → top bar →
the messages (the list itself is a tab stop, so the keyboard can scroll it, then each reply's action row) →
composer; every stop shows a focus ring (the message box shows it on the composer's border).

## 3. Voice mode (live conversation)
Voice mode runs **inside the chat view**, like the current ChatGPT voice mode: no overlay, no dialog, the
conversation stays readable and the composer stays usable. It is opened by the composer's voice-mode button or
Ctrl+Shift+Space and ended by the composer's × button or Esc.

- **The orb** is the whole interface: one soft, monochrome sphere, as in ChatGPT's voice mode — no ring, no
  border, no label, no hint. CSS only: a radial-gradient sphere (dark theme: a light sphere, highlight at
  38% 32%, from near-white through mid grey to a dark edge; light theme: a dark sphere with a soft highlight)
  with two blurred, slowly drifting cloud layers (`::before`/`::after`, blurred radial gradients, soft-light
  blend, 14–20 s loops) and a very soft outer glow. Size 128 px when the chat has no messages, centred in the
  empty message area (the welcome cards are hidden while voice mode is on); 44 px once there are messages,
  floating centred just above the composer with no strip, no background and no border (the messages scroll
  behind it with a small bottom padding so the last message is not covered).
  States are shown by motion only: *listening* a slow breath (scale 1 → 1.04, 3 s); *you are speaking* scale
  1 + 0.25 × level and a touch brighter; *thinking* the clouds drift faster and the sphere dims slightly;
  *speaking* the scale follows the tutor's voice level; *muted* opacity 0.5 and no motion; *connecting*
  opacity 0.35 with the slow breath. The only text is a 12 px caption under the orb for *Connecting…*,
  *Muted* and errors (red), nothing for the other states. Reduced motion: static sphere, caption only.
  Never show timings or metrics in the interface (they go to the metrics log).
- **Turns are ordinary messages**: what the learner said (from `user_final`) appears as a user bubble with its
  English check (open by default, as everywhere), and the tutor's reply streams in as an assistant message with
  the caret, exactly as a typed turn; nothing is shown twice. An interrupted reply keeps its partial text with
  a "…" tag.
- **Typing during voice mode**: the composer sends `{"type": "text", "text": "…"}` over the live WebSocket. The
  server treats it as a completed utterance: if the tutor is answering or speaking it is interrupted first,
  then the text runs through the same turn as a transcription (`user_final`, reply streamed and spoken). The
  push-to-talk recorder is not available in voice mode (the live microphone is already open); the mic button
  mutes instead.
- **Controls**: in the composer (section 2): mute toggle (Ctrl+Space), × end (Esc). Errors from the server show
  as a toast and as the red caption under the orb. The patience (pause) setting is in Settings › Voice.
- Server support: `live.py` accepts the `text` control message (above). Everything else in the live protocol is
  unchanged.

## 4. Pages
Every page: header (h1, description, actions), content cards on a 12-column-ish grid (`grid` with
`minmax(300px, 1fr)`), consistent card padding 20 px, radius 16 px, 1 px border, no shadows in light mode.

- **Home** (was Today): a greeting; **Continue** card (last session summary + next step + button); **Due reviews**
  (topics with the four grade buttons and "Review with tutor"); **Next new topics**; **Vocabulary** (N words due,
  button); **Pronunciation focus** (weakest sounds); **English focus** (frequent mistakes). Empty states are one
  friendly line each, never a blank card.
- **Courses**: two panes (list | detail). Detail: course name (editable inline), description, documents table
  (name, pages, passages, date, delete) with a **drop zone** ("Drop PDF, TXT or MD files here, or click") that
  accepts drag-and-drop and shows per-file upload progress/results; topics list (numbered, status badge,
  rename inline, move up/down, delete, "Material" expands linked documents) and a **Suggest topics** button.
  Deleting asks for confirmation in a dialog (section 7), not a two-click button.
- **Vocabulary**: a **review card** in the centre (word large, "Hear it", reveal, four grades; Space reveals,
  1–4 grade; progress "3 of 12"); **Word lists** cards with progress bars and the daily pace select; **Add a
  word** form; **All words** table with search filter and delete.
- **Pronunciation**: as today, restyled: the drill chips, the practice panel (big text, IPA line, Listen /
  Record / My recording), the coloured word results, the sounds list with bars, recent checks.
- **Mistakes**: rule cards with examples and Mark resolved / Delete.
- **Memory**: what the tutor remembers (kind badge, edit inline, on/off switch, delete) and the add form; below,
  the session history (the same list as the sidebar, with summaries).

## 5. Visual system
- **Tokens** (CSS custom properties on `:root`, dark set under `[data-theme=dark]` and, when the theme is
  "system", `@media (prefers-color-scheme: dark)`): `--bg` (page), `--surface` (cards, composer), `--surface-2`
  (user bubble, hover), `--border`, `--text`, `--text-2` (muted), `--text-3` (decoration only),
  `--text-placeholder` (light #71717a, dark #84848c: 4.5:1 on `--surface`), `--accent`
  (buttons, links, focus; a calm teal/green, not blue, to feel like a tutor), `--accent-ink`, `--accent-soft`,
  `--good/--good-soft`, `--warn/--warn-soft`, `--bad/--bad-soft`, `--radius-s 8px`, `--radius 12px`,
  `--radius-l 16px`, `--shadow-1`, `--shadow-2`, `--font-sans`, `--font-serif` (headings and the product
  name only), `--font-mono`.
  Light: bg #ffffff, surface #ffffff, surface-2 #f4f4f5, border #e4e4e7, text #18181b, text-2 #52525b, text-3
  #a1a1aa. Dark: bg #111113, surface #18181b, surface-2 #27272a, border #2e2e33, text #f4f4f5, text-2 #a1a1aa,
  text-3 #71717a. Accent light #0f766e on white; dark #5eead4 on #0b1a18.
- **Type**: `--font-sans: "Segoe UI Variable Text", "Segoe UI", Inter, system-ui, sans-serif`; base 15 px on
  pages, 16 px in messages; headings 600 weight; `--font-serif` for the product name and the vocabulary word.
- **Spacing**: 4 px scale (4, 8, 12, 16, 20, 24, 32, 48).
- **Buttons**: primary (accent fill), secondary (surface, border), ghost (no border, hover surface-2), icon
  buttons 36 px round with `aria-label` and a tooltip (`title`). Danger variants red.
- **Inputs**: surface, border, 10 px radius, focus ring `0 0 0 3px var(--accent-soft)` plus accent border.
- **Chips/badges**, **cards**, **popover**, **dialog** (section 7), **toast** (bottom-centre, slides up, a
  column with the newest at the bottom and 8 px between, at most 3 (a fourth pushes the oldest out); each has its
  own close button and timer: 4 s, errors 8 s, paused while hovered or focused; `role="status"`), **kbd** (a key).
- **Motion**: 150–200 ms ease-out for hover/open; the orb and caret animations; everything under
  `@media (prefers-reduced-motion: reduce)` is turned off.
- **Icons**: a single inline `<svg>` sprite of `<symbol>`s in index.html (24 px grid, stroke 1.75, round caps:
  plus, search, menu, chevron-down, chevron-left, mic, send, stop, speaker, speaker-off, copy, check, sources,
  home, book, cards, waveform, alert, brain, settings, sun, moon, trash, edit, play, pause, x, refresh,
  arrow-down, spark). Used as `<svg class="icon"><use href="#i-mic"/></svg>`.
- **Theme switch**: Settings › General: System / Light / Dark, stored in localStorage, applied to
  `<html data-theme>` before first paint (a tiny inline script in index.html reads localStorage).

## 6. Accessibility and quality
- Landmarks: `nav`, `main`, `header`; every icon button has `aria-label`; dialogs and popovers trap focus,
  restore focus on close, close on Esc; the sidebar drawer has `aria-expanded` on its button.
- `:focus-visible` rings everywhere; no `outline: none` without a replacement.
- The message list is `aria-live="polite"` for the completed reply only (streaming tokens are not announced).
- Colour contrast ≥ 4.5:1 for text in both themes; status never conveyed by colour alone (icons/text too).
- No layout shift: images/avatars have fixed sizes; skeleton rows while lists load (> 150 ms).
- All network errors surface as toasts or inline cards; nothing fails silently; no unhandled rejections.
- Works at 390 px wide: drawer sidebar, full-width messages, composer above the keyboard, dialogs full-screen.

## 7. Dialogs and confirmations
A `<dialog>`-based component: title, body, actions (secondary Cancel, primary/danger Confirm). Used for: delete
course/topic/word/memory/chat, end chat when switching topic, first-run profile. `confirm()`/`alert()` are never
used.

## 8. Settings dialog
Opened from the sidebar footer (and `Ctrl+,`). Tabs on the left (stacked on narrow screens):
- **General**: name, English level (A2–C2), teaching language, interests, goals, notes, theme, and a
  **Shortcuts** link (the keyboard shortcuts dialog, as Ctrl+/).
- **Voice**: the tutor's voice (with ▶ preview), speed slider, read replies aloud, send after speaking, pause
  before the tutor answers (patience), careful grammar answers (with its explanation).
- **Data**: backup now (shows the file path), the materials folder path, the app version and model status.
Saving is per field on change (PUT `/api/profile`), with a brief "Saved" chip; invalid values show inline.

## 9. Code structure
```
web/
  index.html          shell, SVG sprite, theme bootstrap script, <script type="module" src="/static/js/main.js">
  app.css             tokens, base, layout, components, pages (sections in this order, commented)
  js/main.js          boot, routing (hash), shell (sidebar, drawer, status), page registry
  js/api.js           api(), SSE helper (streamEvents with AbortSignal), errors
  js/dom.js           h(), icon(), toast(), dialog(), popover(), confirmDialog(), fmtDate/fmtDue, remembered
  js/markdown.js      escape + render (as today, with dir="auto" blocks)
  js/settings.js      settings store (localStorage) + the settings dialog
  js/shortcuts.js     the keyboard shortcuts dialog (Ctrl+/, Settings › General)
  js/chat.js          sessions list, context picker, welcome, messages, streaming, feedback, sources, composer
  js/voice.js         speaker (read aloud queue), push-to-talk recorder
  js/live.js          voice mode inside the chat (WebSocket client, worklet, playback, the orb)
  js/pages/home.js, courses.js, vocab.js, pronunciation.js, mistakes.js, memory.js
  live-worklet.js     unchanged
  DESIGN.md           this file
```
Rules: no globals except the module exports; state objects per module; every async handler goes through a
`run()` wrapper that toasts errors; the DOM is built with `h()` (no innerHTML except the markdown renderer,
which escapes first); CSS classes are kebab-case, scoped by component prefix (`chat-`, `composer-`, `sb-`,
`card`, `btn`, `dlg-`…); no inline styles except CSS custom properties set from JS (`--level`, widths).

## 10. Server support
Existing endpoints are enough, plus:
- `DELETE /api/sessions/{id}`: removes the session and its messages; the open session cannot be deleted.
- `title` in the `GET /api/sessions` rows and in `GET /api/sessions/{id}`: the learner's own title if set, else
  the topic title, else the first user message trimmed to 60 characters, else null.
- `PATCH /api/sessions/{id}` with `{"title": "..."}`: the learner's own title (1–120 characters, stored in a
  new nullable `title` column of `sessions`, created with `ALTER TABLE` at start-up when missing).
- `POST /api/sessions/{id}/reopen`: continue an ended session: the currently open session (if any) is ended
  the normal way (summary, memory), then `ended_at` is cleared on this one; returns the session. Its summary
  stays until it is ended again, when it is written anew.
Everything else (SSE events `user`, `token`, `reply_done`, `feedback`, `error`; live WebSocket protocol; voice
endpoints; pronunciation endpoints) is unchanged.

## 11. Verification
`tests/ui_check.py` starts a throwaway server (temp data, port 8817) with a fake Ollama (SSE streaming) and
stubbed voice endpoints, seeds a course, a topic and a past session, drives headless Chrome over the DevTools
protocol through: load → welcome → pick context → send a message (streamed, feedback rendered) → stop a
reply → open a past session → delete it → open every page → open Settings and change the theme → open the
voice mode (with a stubbed WebSocket: orb centred, then docked above the composer; a typed turn; mute; end) → 390 px width pass → the toast stack, the shortcuts dialog,
the sidebar's empty state and long titles, a long topic in the pill, the chat's tab order with visible focus in both
themes (a fake reply with SLOW in the message streams slowly, so a check can act while it streams); it collects `window.onerror` and
unhandled rejections and saves screenshots to a folder. It exits non-zero on any error. Run it after every UI
change: `uv run python tests/ui_check.py <screenshot folder>`.
