"""Headless-Chrome check of the whole browser UI on throwaway data (DESIGN.md section 11).

    uv run python tests/ui_check.py <screenshot folder>

Starts a fake Ollama (streamed replies, quick English checks and summaries) and the tutor's server on port 8817
with a temporary data folder, seeds a course with material and topics, words, a mistake, a memory and two past
chats, then drives Chrome over the DevTools protocol through the app: the welcome, choosing the course and topic,
a streamed reply with its English check, stopping a reply, opening, renaming, continuing and deleting chats from
the sidebar, every page, Settings and the theme, voice mode in the chat (stubbed WebSocket and microphone), the
keyboard shortcuts and their dialog (Ctrl+/), hash routing, a 390 px wide pass, the toast stack, the chat's tab order
with a visible focus ring, and text contrast on every page in both themes. The voice endpoints are stubbed in the
page, so no speech model is loaded. Every JavaScript error or unhandled rejection is a failure. Screenshots (light and
dark, 1300 px and 390 px) go to the folder. Exit code 0 only when every check passed.
"""

import base64
import json
import os
import re
import shutil
import socket
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
from contextlib import closing
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import httpx
from websockets.sync.client import connect

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "app"))
CHROME = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
PORT = 8817                     # never the learner's 8765
BASE = f"http://127.0.0.1:{PORT}"
WIDE, NARROW = (1300, 900), (390, 844)

results: list[tuple[str, bool, str]] = []


def check(name: str, ok, detail="") -> bool:
    results.append((name, bool(ok), str(detail)))
    print(("PASS  " if ok else "FAIL  ") + name + ("" if ok or detail == "" else f"   [{detail}]"), flush=True)
    return bool(ok)


def free_port() -> int:
    with closing(socket.socket()) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


# ---------------------------------------------------------------------------------------------- fake Ollama
WORD_DELAY = 0.05
SLOW_WORD_DELAY = 0.25   # a message with SLOW in it: the reply takes 20 s, so a check can act while it streams
LONG_REPLY = " ".join(f"word{i}" for i in range(80)) + "."


class FakeOllama(BaseHTTPRequestHandler):
    """Ollama's HTTP API as the tutor uses it: streamed chat, JSON calls (English check, summary), status.
    Markers in the learner's message: LONG (an 80-word reply), SLOW (streamed slowly), BREAK / BREAKONCE (the model
    fails; BREAKONCE only the first time)."""

    protocol_version = "HTTP/1.1"
    broken: set[str] = set()   # BREAKONCE messages that already failed once

    def log_message(self, *args):
        pass

    def _json(self, obj, code=200):
        data = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        model = {"name": "tutor:latest", "size": 6_000_000_000, "size_vram": 6_000_000_000}
        answers = {"/api/version": {"version": "fake"}, "/api/tags": {"models": [model]}, "/api/ps": {"models": [model]}}
        self._json(answers.get(self.path, {"error": "not found"}), 200 if self.path in answers else 404)

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))))
        last = body["messages"][-1]["content"]
        if body.get("stream"):
            said = last.split("\n\n[Note")[0]
            # The model fails before answering; BREAKONCE only the first time (the chat's Retry then succeeds)
            if "BREAK" in said and not ("BREAKONCE" in said and said in FakeOllama.broken):
                FakeOllama.broken.add(said)
                return self._json({"error": "model crashed"}, 500)
            # A reply cites the first passage when the course material gave it any
            cite = " [S1]" if "[S1] (" in body["messages"][0]["content"] else ""
            reply = LONG_REPLY if "LONG" in said else (
                f"Good question. **Classical conditioning** pairs a neutral stimulus with one that already causes a "
                f"response{cite}.\n\n- The bell comes first\n- Then the food\n\nAfter a few pairings, the bell alone "
                f"makes the dog salivate. You said: {said[:40]}")
            self.send_response(200)
            self.send_header("Content-Type", "application/x-ndjson")
            self.send_header("Transfer-Encoding", "chunked")
            self.end_headers()

            def chunk(obj):
                data = (json.dumps(obj) + "\n").encode()
                self.wfile.write(f"{len(data):X}\r\n".encode() + data + b"\r\n")
                self.wfile.flush()

            delay = SLOW_WORD_DELAY if "SLOW" in said else WORD_DELAY
            try:
                for i, w in enumerate(reply.split(" ")):
                    time.sleep(delay)
                    chunk({"message": {"content": (" " if i else "") + w}, "done": False})
                chunk({"message": {"content": ""}, "done": True})
                self.wfile.write(b"0\r\n\r\n")
                self.wfile.flush()
            except OSError:
                pass   # the tutor stopped reading (the learner pressed Stop)
            return
        props = (body.get("format") or {}).get("properties", {})
        if "corrections" in props:
            time.sleep(0.8)
            if "goed" in last:
                out = {"corrections": [{"wrong": "goed", "right": "went", "rule": "past tense of go",
                                        "category": "grammar", "explanation": "Go is irregular: go, went, gone."}],
                       "better_phrasing": "I went to school yesterday.", "vocabulary": [], "memories": []}
            else:
                out = {"corrections": [], "better_phrasing": "", "vocabulary": [], "memories": []}
        elif "summary" in props:
            out = {"summary": "We talked about classical conditioning and practised the past tense.",
                   "next_step": "Explain operant conditioning in your own words.", "new_memories": [],
                   "outdated_memory_ids": []}
        elif "keywords" in props:
            out = {"keywords": ["conditioning"]}
        elif "meaning" in props:
            out = {"meaning": "a meaning", "example": "An example."}
        else:
            out = {"topics": ["Habituation"]}
        self._json({"message": {"content": json.dumps(out)}, "done": True})


class QuietServer(ThreadingHTTPServer):
    daemon_threads = True

    def handle_error(self, request, client_address):
        if not isinstance(sys.exc_info()[1], ConnectionError):   # the tutor closing a stream it stopped is normal
            super().handle_error(request, client_address)


# ---------------------------------------------------------------------------------------------- seed data
MATERIAL = ("Classical conditioning is a type of learning in which a neutral stimulus comes to trigger a response "
            "after being paired with a stimulus that already causes it. Pavlov showed this with dogs that salivated "
            "when they heard a bell. ") * 4


def seed(data_dir: Path) -> dict:
    from tutor.store import Store
    store = Store(data_dir / "tutor.sqlite3")
    store.update_profile({"name": "Abdullah", "english_level": "B2"})
    course = store.create_course("Psychology", "Introduction to Psychology (Yale)")
    store.add_document(course["id"], "chapter 1.txt", "chapter 1.txt", "sha-ui-1", 12, [(1, MATERIAL), (2, MATERIAL)])
    topics = [store.add_topic(course["id"], t) for t in ("Classical conditioning", "Operant conditioning", "Memory")]
    for i in range(6):
        store.add_vocab(f"word{i}", f"meaning number {i}", f"An example sentence with word{i}.")
    store.add_vocab("plasticity", "the brain's ability to change", "Neural plasticity helps us learn.")
    store.log_mistake("past tense of go", "grammar", "Go is irregular.", "I goed", "I went")
    store.add_memory("goal", "Wants to pass IELTS with a 7.", "user")
    store.save_word_list("ui-awl", "Academic words", "The most frequent academic words.", "Coxhead (2000)",
                         [{"word": f"academic{i}", "meaning": f"meaning {i}"} for i in range(30)], daily_new=5)

    def past(course_id, topic_id, texts, summary, days_ago):
        s = store.create_session(course_id, topic_id)
        for t in texts:
            store.add_message(s["id"], "user", t)
            store.add_message(s["id"], "assistant", f"Here is an answer about: {t}")
        store.end_session(s["id"], summary, "Review the main ideas.")
        when = (datetime.now(timezone.utc) - timedelta(days=days_ago)).isoformat(timespec="seconds")
        store._exec("UPDATE sessions SET started_at = ?, ended_at = ? WHERE id = ?", (when, when, s["id"]))
        return s["id"]

    memory_chat = past(course["id"], topics[2]["id"], ["How does memory work?", "What is working memory?"],
                       "Studied the stages of memory.", 1)
    free_chat = past(None, None, ["Can you help me with my email to a professor?"], "Wrote a polite email.", 12)

    def word(text, phones):
        return {"text": text, "status": "wrong", "phones": phones, "added": []}

    for _ in range(5):
        store.add_pron_attempt("think right", None, {"score": 40, "issues": [], "words": [
            word("think", [{"phone": "TH", "status": "wrong", "heard": ""}, {"phone": "IH", "status": "good"}]),
            word("right", [{"phone": "R", "status": "wrong", "heard": "L"}, {"phone": "AY", "status": "good"}])]})
    store.conn.close()
    return {"course": course["id"], "topics": [t["id"] for t in topics], "memory_chat": memory_chat, "free_chat": free_chat,
            "db": str(data_dir / "tutor.sqlite3"), "tmp": str(data_dir.parent)}


# ---------------------------------------------------------------------------------------------- page stubs
# Injected before the app's scripts: errors are collected, and nothing touches the GPU or a real device.
STUBS = r"""
window.__errors = [];
addEventListener("error", e => __errors.push("error: " + e.message + " @ " + (e.filename || "") + ":" + (e.lineno || "")));
addEventListener("unhandledrejection", e => __errors.push("rejection: " + (e.reason && e.reason.message || e.reason)));
try { localStorage.setItem("readAloud", "0"); localStorage.setItem("autoSend", "0"); } catch { /* none */ }

const __sleep = ms => new Promise(r => setTimeout(r, ms));
function __wav(n = 2400, rate = 24000) {
  const buf = new ArrayBuffer(44 + n * 2), v = new DataView(buf);
  const w = (o, s) => { for (let i = 0; i < s.length; i++) v.setUint8(o + i, s.charCodeAt(i)); };
  w(0, "RIFF"); v.setUint32(4, 36 + n * 2, true); w(8, "WAVE"); w(12, "fmt "); v.setUint32(16, 16, true);
  v.setUint16(20, 1, true); v.setUint16(22, 1, true); v.setUint32(24, rate, true); v.setUint32(28, rate * 2, true);
  v.setUint16(32, 2, true); v.setUint16(34, 16, true); w(36, "data"); v.setUint32(40, n * 2, true);
  return buf;
}
const __json = (obj, status = 200) => new Response(JSON.stringify(obj), { status, headers: { "Content-Type": "application/json" } });
window.__log = [];
const __realFetch = window.fetch.bind(window);
window.fetch = async (input, init = {}) => {
  const url = typeof input === "string" ? input : input.url;
  __log.push((init.method || "GET").toUpperCase() + " " + url);
  if (url.includes("/api/voice/speak")) return new Response(__wav(), { status: 200, headers: { "Content-Type": "audio/wav" } });
  if (url.includes("/api/voice/warmup")) return __json({});
  if (url.includes("/api/voice/voices")) return __json([{ id: "af_heart", label: "Heart (American, female)" }]);
  if (url.includes("/api/voice/transcribe")) return __json({ text: "I goed to the library" });
  if (url.includes("/api/pronunciation/check")) {
    // Every kind of mark: the first word swapped a sound (said alone, it sounded like its pair), the second is
    // worth checking and has a vowel added, the rest are clear
    const words = String(init.body.get("text")).split(/\s+/).map((text, i) => ({ text, status: ["wrong", "unsure"][i] || "good",
      phones: i === 0 ? [{ phone: "P", ipa: "p", status: "wrong", heard: "B", heard_ipa: "b", tip: "A puff of air." }, { phone: "IH", ipa: "ɪ", status: "good" }]
        : i === 1 ? [{ phone: "S", ipa: "s", status: "unsure", heard: "Z", heard_ipa: "z", tip: "Voice off." }] : [{ phone: "T", ipa: "t", status: "good" }],
      added: i === 1 ? [{ before: 0, heard: "AH", heard_ipa: "ə", tip: "No vowel here." }] : [] }));
    return __json({ score: 62, id: 0, words, issues: [{ word: words[0].text, phone: "P", ipa: "p", status: "wrong", tip: "A puff of air." }],
                    ...(words.length === 1 ? { sounded_like: "bin" } : {}) });
  }
  return __realFetch(input, init);
};

// Microphone: a synthetic tone
window.__streams = [];
let __micCtx = null;
navigator.mediaDevices.getUserMedia = async () => {
  __micCtx = __micCtx || new AudioContext();
  const osc = __micCtx.createOscillator();
  const dest = __micCtx.createMediaStreamDestination();
  osc.connect(dest);
  osc.start();
  __streams.push(dest.stream);
  return dest.stream;
};

// The live WebSocket never reaches the server (its endpoint would load the speech models)
window.__ws = [];
class __FakeWS {
  constructor(url) {
    this.url = url; this.readyState = 0; this.binaryType = "blob"; this.sent = [];
    this.onopen = this.onerror = this.onclose = this.onmessage = null;
    __ws.push(this);
    setTimeout(() => { if (this.readyState === 0) { this.readyState = 1; this.onopen && this.onopen({}); } }, 200);
  }
  send(d) { this.sent.push(d); }
  close() {
    if (this.readyState >= 2) return;
    const was = this.readyState;
    this.readyState = 3;
    setTimeout(() => { if (was === 0 && this.onerror) this.onerror({}); if (this.onclose) this.onclose({}); }, 0);
  }
  serverSays(obj) { this.onmessage && this.onmessage({ data: JSON.stringify(obj) }); }
}
__FakeWS.CONNECTING = 0; __FakeWS.OPEN = 1; __FakeWS.CLOSING = 2; __FakeWS.CLOSED = 3;
window.WebSocket = __FakeWS;
"""


# ---------------------------------------------------------------------------------------------- DevTools
class Page:
    def __init__(self, ws_url: str):
        self.ws = connect(ws_url, max_size=100_000_000, legacy=True)
        self.n = 0
        self.events: list[dict] = []

    def call(self, method: str, **params):
        self.n += 1
        my = self.n
        self.ws.send(json.dumps({"id": my, "method": method, "params": params}))
        while True:
            msg = json.loads(self.ws.recv())
            if msg.get("id") == my:
                if "error" in msg:
                    raise RuntimeError(f"{method}: {msg['error']}")
                return msg.get("result", {})
            if "method" in msg:
                self.events.append(msg)

    def js(self, expr: str):
        r = self.call("Runtime.evaluate", expression=expr, awaitPromise=True, returnByValue=True)
        if r.get("exceptionDetails"):
            raise RuntimeError(f"{expr[:90]}: {r['exceptionDetails'].get('exception', {}).get('description')}")
        return r.get("result", {}).get("value")

    def wait(self, expr: str, timeout: float = 8.0) -> bool:
        end = time.time() + timeout
        while time.time() < end:
            try:
                if self.js(expr):
                    return True
            except RuntimeError:
                pass
            time.sleep(0.1)
        return False

    def size(self, width: int, height: int, mobile: bool = False):
        self.call("Emulation.setDeviceMetricsOverride", width=width, height=height, deviceScaleFactor=1, mobile=mobile)
        time.sleep(0.35)

    def shot(self, path: Path):
        time.sleep(0.3)   # let transitions finish
        path.write_bytes(base64.b64decode(self.call("Page.captureScreenshot", format="png")["data"]))

    def key(self, key: str, code: str, vk: int, modifiers: int = 0, text: str | None = None):
        down = {"type": "keyDown", "key": key, "code": code, "windowsVirtualKeyCode": vk, "modifiers": modifiers}
        if text:
            down["text"] = text
        self.call("Input.dispatchKeyEvent", **down)
        self.call("Input.dispatchKeyEvent", type="keyUp", key=key, code=code, windowsVirtualKeyCode=vk, modifiers=modifiers)

    def escape(self):
        self.key("Escape", "Escape", 27)

    def type(self, selector: str, text: str):
        self.js(f"document.querySelector({json.dumps(selector)}).focus()")
        self.call("Input.insertText", text=text)

    def click(self, selector: str):
        self.js(f"(() => {{ const el = document.querySelector({json.dumps(selector)}); if (!el) throw new Error('no element: ' + "
                f"{json.dumps(selector)}); el.click(); }})()")

    def click_text(self, selector: str, text: str):
        self.js(f"(() => {{ const el = [...document.querySelectorAll({json.dumps(selector)})].find(e => e.textContent.trim() === {json.dumps(text)});"
                f" if (!el) throw new Error('no ' + {json.dumps(selector)} + ' with ' + {json.dumps(text)}); el.click(); }})()")

    def errors(self) -> list[str]:
        """JavaScript errors so far: from the page's own listeners and from the DevTools events."""
        out = list(self.js("window.__errors || []") or [])
        for ev in self.events:
            if ev["method"] == "Runtime.exceptionThrown":
                d = ev["params"]["exceptionDetails"]
                out.append("exception: " + (d.get("exception", {}).get("description") or d.get("text", "")))
            elif ev["method"] == "Log.entryAdded" and ev["params"]["entry"]["level"] == "error":
                e = ev["params"]["entry"]
                out.append(f"log: {e.get('text')} {e.get('url', '')}")
        return sorted(set(out))


# ---------------------------------------------------------------------------------------------- the checks
def visible(selector: str) -> str:
    return (f"(() => {{ const el = document.querySelector({json.dumps(selector)}); if (!el) return false;"
            f" const r = el.getBoundingClientRect(); const s = getComputedStyle(el);"
            f" return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && !el.closest('[hidden]'); }})()")


NO_OVERFLOW = """(() => {
  const doc = document.documentElement;
  const wide = [...document.querySelectorAll('main *')].filter(e => {
    const r = e.getBoundingClientRect();
    return r.width > 0 && r.right > innerWidth + 1 && !e.closest('.table-wrap, pre, .sb, dialog');
  }).map(e => e.className || e.tagName).slice(0, 5);
  return { ok: doc.scrollWidth <= innerWidth && !wide.length, doc: doc.scrollWidth, wide };
})()"""

# Every visible text (and placeholder) against the colours behind it (DESIGN.md section 6: 4.5:1, or 3:1 for large
# text); decorative (aria-hidden) and disabled text is exempt, and so is text on a gradient. Text cut off without an
# ellipsis is reported too. Running transitions are finished first, so a fading element is judged as it ends up.
READABLE = r"""(() => {
  for (const a of document.getAnimations()) { try { a.finish(); } catch { /* an endless one */ } }
  const parse = c => {
    let m = c && c.match(/^rgba?\(([^)]+)\)/);
    if (m) { const p = m[1].split(/[ ,/]+/).filter(Boolean).map(Number); return [p[0], p[1], p[2], p.length > 3 ? p[3] : 1]; }
    m = c && c.match(/^color\(srgb ([^)]+)\)/);
    if (m) { const p = m[1].split(/[ /]+/).filter(Boolean).map(Number); return [p[0] * 255, p[1] * 255, p[2] * 255, p.length > 3 ? p[3] : 1]; }
    return null;
  };
  const over = (f, b) => [0, 1, 2].map(i => f[i] * f[3] + b[i] * (1 - f[3])).concat(1);
  const lum = c => { const f = v => { v /= 255; return v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4; };
                     return 0.2126 * f(c[0]) + 0.7152 * f(c[1]) + 0.0722 * f(c[2]); };
  const ratio = (a, b) => { const x = lum(a), y = lum(b); return (Math.max(x, y) + 0.05) / (Math.min(x, y) + 0.05); };
  const behind = el => {
    const chain = [];
    for (let e = el; e; e = e.parentElement) chain.unshift(e);
    let bg = [255, 255, 255, 1], opacity = 1;
    for (const e of chain) {
      const s = getComputedStyle(e);
      if (s.backgroundImage !== 'none' && !s.backgroundImage.startsWith('url(')) return null;
      const c = parse(s.backgroundColor);
      if (c && c[3] > 0) bg = over(c, bg);
      opacity *= Number(s.opacity);
    }
    return { bg, opacity };
  };
  const modal = document.querySelector('dialog[open]:modal');
  const low = [], clipped = [];
  for (const el of document.querySelectorAll('body *')) {
    const own = [...el.childNodes].some(n => n.nodeType === 3 && n.textContent.trim());
    const ph = /^(INPUT|TEXTAREA)$/.test(el.tagName) && el.placeholder && !el.value;
    if (!own && !ph) continue;
    const r = el.getBoundingClientRect(), s = getComputedStyle(el);
    if (!r.width || !r.height || s.visibility === 'hidden' || r.bottom < 0 || r.top > innerHeight || r.right < 0 || r.left > innerWidth) continue;
    if (el.closest('[hidden], .sr-only, [aria-hidden=true], :disabled, [inert]') || (modal && !modal.contains(el) && !el.closest('.toast-region'))) continue;
    const b = behind(el);
    if (!b || b.opacity < 0.15) continue;
    const need = parseFloat(s.fontSize) >= 24 || (parseFloat(s.fontSize) >= 18.6 && Number(s.fontWeight) >= 700) ? 3 : 4.5;
    const judge = (colour, text) => {
      const f = parse(colour);
      if (!f) return;
      const k = ratio(over([f[0], f[1], f[2], f[3] * b.opacity], b.bg), b.bg);
      if (k < need) low.push(`${String(el.className || el.tagName).slice(0, 40)} "${text.slice(0, 30)}" ${k.toFixed(2)}:1`);
    };
    if (own) judge(s.color, el.textContent.trim());
    if (ph) judge(getComputedStyle(el, '::placeholder').color, el.placeholder);
    if (own && /hidden|clip/.test(s.overflowX) && s.textOverflow !== 'ellipsis' && el.scrollWidth > el.clientWidth + 1)
      clipped.push(`${String(el.className).slice(0, 40)} "${el.textContent.trim().slice(0, 30)}"`);
  }
  return { low: [...new Set(low)], clipped: [...new Set(clipped)] };
})()"""


def readable(p: Page, label: str) -> None:
    r = p.js(READABLE)
    check(f"{label}: text contrast 4.5:1 or more (3:1 large) and no text cut off", not r["low"] and not r["clipped"], r)


PAGES = ["home", "courses", "vocabulary", "pronunciation", "mistakes", "memory"]


def page_ok(p: Page, route: str) -> bool:
    return p.wait(f"location.hash === '#{route}' && !!document.querySelector('#page-host .page .page-title')"
                  f" && !document.querySelector('#page-host .skeleton')", 10)


# ---------------------------------------------------------------------------------------------- chat (DESIGN.md section 2)
LAST_REPLY = "[...document.querySelectorAll('#messages .msg.assistant')].at(-1)"
AT_BOTTOM = "(() => { const b = document.querySelector('#messages'); return b.scrollHeight - b.scrollTop - b.clientHeight < 4; })()"


def hover(p: Page, js_element: str) -> None:
    """Moves the mouse over the middle of an element (CSS :hover), as a real pointer does."""
    r = p.js(f"(() => {{ const r = ({js_element}).getBoundingClientRect(); return [r.left + r.width / 2, r.top + Math.min(r.height / 2, 40)]; }})()")
    p.call("Input.dispatchMouseEvent", type="mouseMoved", x=r[0], y=r[1])


def picker_choose(p: Page, course, topic=None) -> None:
    """Chooses a course and topic in the context picker ("" course: a free conversation; topic None: any topic)."""
    time.sleep(0.2)   # a chat just replaced settles first: its scroll position changing would close the popover
    p.click("#chat-context")
    p.wait("!!document.querySelector('.chat-picker-item[data-course]')")
    if course == "":
        p.click(".chat-picker-item[data-course='']")
        return
    p.click(f".chat-picker-item[data-course='{course}']")
    p.wait(f"!!document.querySelector(\".chat-picker-item.is-browsed[data-course='{course}']\")"
           " && !!document.querySelector('.chat-picker-item[data-topic]')")
    p.click(f".chat-picker-item[data-topic='{topic or ''}']")


def send_and_wait(p: Page, text: str) -> bool:
    """Sends a message from the composer; true once the reply and its English check are complete."""
    p.type("#input", text)
    p.key("Enter", "Enter", 13, text="\r")
    return p.wait("!!document.querySelector('.msg.assistant') && !document.querySelector('.msg.assistant.pending')"
                  " && !document.querySelector('.fb-checking') && !document.querySelector('#btn-send.is-stop')", 15)


def check_welcome(p: Page) -> None:
    title = p.js("document.querySelector('#messages .chat-empty-title')?.textContent")
    check("the welcome greets the learner by the time of day",
          re.fullmatch(r"Good (morning|afternoon|evening), Abdullah", title or ""), title)
    check("the welcome suggests what to do now, from the plan", p.wait(
        "[...document.querySelectorAll('.chat-suggest .chat-suggest-title')].map(t => t.textContent).join()"
        " === 'Continue,Next lesson,Reviews due,Practise pronunciation'"),
          p.js("[...document.querySelectorAll('.chat-suggest')].map(c => c.textContent).join(' | ')"))
    check("the cards say what they will do (next step, next topic, what is due, the weakest sound)", p.js(
        "(() => { const d = [...document.querySelectorAll('.chat-suggest-desc')].map(e => e.textContent);"
        " return d[0] === 'Review the main ideas.' && d[1] === 'Classical conditioning · Psychology'"
        " && /\\d+ (topic|word)/.test(d[2]) && d[3].startsWith('/'); })()"),
          p.js("[...document.querySelectorAll('.chat-suggest-desc')].map(e => e.textContent)"))
    check("an empty chat has the welcome and the composer together, mid-screen", p.js(
        "document.querySelector('#view-chat').classList.contains('is-welcome')"
        " && document.querySelector('#composer').getBoundingClientRect().bottom < innerHeight - 100"))
    check("the picker invites a choice before any", p.js(
        "document.querySelector('#chat-context').textContent.includes('Choose a course and topic')"))


def check_context_picker(p: Page, out: Path, ids: dict) -> None:
    p.js("document.activeElement.blur()")
    p.key("k", "KeyK", 75, modifiers=2)
    check("Ctrl+K opens the course and topic picker", p.wait(visible(".chat-picker"))
          and p.js("document.querySelector('#chat-context').getAttribute('aria-expanded')") == "true")
    p.key("ArrowDown", "ArrowDown", 40)
    p.key("ArrowRight", "ArrowRight", 39)
    check("arrow keys move down the courses and into a course's topics", p.wait(
        f"document.querySelector('.chat-picker-item.is-browsed')?.dataset.course === '{ids['course']}'"
        " && document.activeElement?.dataset.topic === ''"))
    rows = p.js("[...document.querySelectorAll('.chat-picker-item[data-topic]')].map(b =>"
                " [b.querySelector('.chat-picker-num')?.textContent || '', b.querySelector('.chat-picker-name').textContent,"
                " b.querySelector('.badge')?.textContent || ''].join(' ').trim())")
    check("the topics are numbered and show their status", len(rows or []) == 4 and rows[0] == "Any topic"
          and [r.rsplit(" ", 1)[0] for r in rows[1:]] == ["1 Classical conditioning", "2 Operant conditioning", "3 Memory"]
          and all(r.rsplit(" ", 1)[1] in ("new", "learning", "reviewing") for r in rows[1:]), rows)
    check("courses and topics sit side by side", p.js(
        "(() => { const [a, b] = document.querySelectorAll('.chat-picker-col'); return a.getBoundingClientRect().right <= b.getBoundingClientRect().left + 1; })()"))
    p.shot(out / "06-context-picker-light.png")
    p.click(f".chat-picker-item[data-topic='{ids['topics'][0]}']")
    check("choosing a topic sets the context; the chat starts with the first message", p.wait("!document.querySelector('.chat-picker')")
          and p.js("document.querySelector('#chat-context .chat-context-course')?.textContent") == "Psychology"
          and p.js("document.querySelector('#chat-context .chat-context-topic')?.textContent") == "Classical conditioning"
          and not p.js("!!document.querySelector('.sb-chat.is-open')")
          and p.js("document.activeElement?.id") == "input")


def check_reply_details(p: Page, out: Path) -> None:
    """The action row (Copy, Read aloud, Sources), citations and the Sources sheet, the English check's chip."""
    check("a citation is a small numbered chip named after its passage", p.js(
        f"(() => {{ const c = {LAST_REPLY}.querySelector('.body .cite'); return !!c && c.textContent === '1' && c.title.startsWith('chapter 1.txt'); }})()"))
    labels = p.js(f"[...{LAST_REPLY}.querySelectorAll('.msg-actions button')].map(b => b.getAttribute('aria-label'))") or []
    check("under the reply: Copy, Read aloud, Sources (n)", len(labels) == 3 and labels[:2] == ["Copy", "Read aloud"]
          and re.fullmatch(r"Sources \(\d\)", labels[2]), labels)
    p.call("Input.dispatchMouseEvent", type="mouseMoved", x=5, y=5)
    quiet = p.wait(f"getComputedStyle({LAST_REPLY}.querySelector('.msg-actions')).opacity === '0'", 2)
    hover(p, LAST_REPLY)
    check("the action row is quiet until the reply is hovered", quiet
          and p.wait(f"getComputedStyle({LAST_REPLY}.querySelector('.msg-actions')).opacity === '1'", 2))

    # Copy (the page may write the clipboard, and is focused, as a real window is)
    p.call("Browser.grantPermissions", permissions=["clipboardReadWrite", "clipboardSanitizedWrite"], origin=BASE)
    p.call("Emulation.setFocusEmulationEnabled", enabled=True)
    p.js(f"{LAST_REPLY}.querySelector('.msg-copy').click()")
    copied = p.wait(f"{LAST_REPLY}.querySelector('.msg-copy').getAttribute('aria-label') === 'Copied'"
                    f" && {LAST_REPLY}.querySelector('.msg-copy').textContent === 'Copied'", 3)
    text = p.js("navigator.clipboard.readText()")
    check("Copy puts the reply (without the citation labels) on the clipboard and says Copied", copied
          and (text or "").startswith("Good question. **Classical conditioning**") and "[S1]" not in text, text)
    check("Copied turns back into Copy", p.wait(f"{LAST_REPLY}.querySelector('.msg-copy').getAttribute('aria-label') === 'Copy'", 4))
    p.call("Emulation.setFocusEmulationEnabled", enabled=False)

    # Read aloud ▶ / ■ (the stubbed voice says a short sound)
    p.js("__log.length = 0")
    p.js(f"{LAST_REPLY}.querySelector('.msg-read').click()")
    reading = p.js(f"{LAST_REPLY}.querySelector('.msg-read').getAttribute('aria-label')") == "Stop reading"
    check("Read aloud plays the reply (■ while it plays, ▶ after)", reading
          and p.wait("__log.some(l => l.includes('/api/voice/speak'))", 4)
          and p.wait(f"{LAST_REPLY}.querySelector('.msg-read').getAttribute('aria-label') === 'Read aloud'", 6))

    # The Sources sheet: from the action row, and from a citation (scrolled to its passage)
    p.js(f"{LAST_REPLY}.querySelector('.msg-sources').focus()")
    p.js(f"{LAST_REPLY}.querySelector('.msg-sources').click()")
    check("Sources opens a side sheet, cited passages first and marked", p.wait(visible("dialog.chat-sources[open]"))
          and p.js("(() => { const s = [...document.querySelectorAll('dialog.chat-sources .chat-source')];"
                   " return s.length >= 1 && s[0].classList.contains('is-cited') && s[0].textContent.includes('Cited')"
                   " && s[0].textContent.includes('chapter 1.txt') && s.filter(x => x.classList.contains('is-cited')).length === 1; })()")
          and p.wait("(() => { const d = document.querySelector('dialog.chat-sources').getBoundingClientRect(); return Math.abs(d.right - innerWidth) < 2 && d.width <= 361; })()", 2),
          p.js("(() => { const d = document.querySelector('dialog.chat-sources')?.getBoundingClientRect(); return [d?.left, d?.right, d?.width, innerWidth]; })()"))
    p.shot(out / "07-sources-sheet-light.png")
    p.escape()
    check("Esc closes the sheet and focus returns to Sources", p.wait("!document.querySelector('dialog.chat-sources')")
          and p.js("document.activeElement?.classList.contains('msg-sources')"))
    p.js(f"{LAST_REPLY}.querySelector('.body .cite').click()")
    check("a citation chip opens the sheet on its passage", p.wait(
        "document.activeElement?.matches('dialog.chat-sources .chat-source.is-target[data-label=S1]')"))
    p.escape()
    p.wait("!document.querySelector('dialog.chat-sources')")

    # The English check: the "1 correction" card is open as it arrives (the list and the better phrasing); its header
    # folds it and opens it again; Use it fills the composer
    fix = ".msg.user .fb-fix"
    check("the English check is a card, open without a click: 1 correction, wrong → right, the rule, the explanation"
          " and the better phrasing", p.wait(
        f"document.querySelector('{fix}')?.textContent === '1 correction'"
        f" && document.querySelector('{fix}').getAttribute('aria-expanded') === 'true' && " + visible(".msg.user .fb-panel"), 8)
          and p.js("(() => { const t = document.querySelector('.msg.user .fb-panel').textContent;"
                   " return ['goed', 'went', 'past tense of go', 'Go is irregular', 'I went to school yesterday.'].every(x => t.includes(x)); })()"),
          p.js("document.querySelector('.msg.user .feedback')?.textContent"))
    p.click(fix)
    check("the card's header folds it into a chip", p.wait(
        f"document.querySelector('{fix}').getAttribute('aria-expanded') === 'false'"
        " && document.querySelector('.msg.user .fb-panel').hidden && document.querySelector('.msg.user .fb-card').classList.contains('is-folded')"
        " && document.querySelector('.msg.user .fb-card').getBoundingClientRect().height < 40"))
    p.click(fix)
    check("and opens it again", p.wait(f"document.querySelector('{fix}').getAttribute('aria-expanded') === 'true' && "
                                       + visible(".msg.user .fb-panel")))
    p.click_text(".msg.user .fb-panel .btn", "Use it")
    check("Use it puts the better phrasing in the composer", p.wait(
        "document.querySelector('#input').value === 'I went to school yesterday.' && document.activeElement?.id === 'input'"
        " && !document.querySelector('#btn-send').disabled"))
    p.js("(() => { const i = document.querySelector('#input'); i.value = ''; i.dispatchEvent(new Event('input')); i.blur(); })()")
    hover(p, LAST_REPLY)   # the screenshot that follows shows the action row


def check_stop(p: Page, out: Path, current_id: int) -> None:
    p.size(WIDE[0], 520)   # a short window: the long reply scrolls
    # SLOW: the fake model streams this reply over 20 s, so it is still coming in while the view is checked, however
    # busy the machine is
    p.type("#input", "SLOW LONG reply please")
    p.key("Enter", "Enter", 13, text="\r")
    streaming = p.wait("(document.querySelector('.msg.assistant.pending .body')?.textContent || '').length > 20", 10)
    p.js("document.querySelector('#messages').scrollTop = 0")
    stays = p.wait(visible("#chat-jump"), 3)
    time.sleep(0.4)
    check("scrolled up while a reply streams: the view stays and Jump to latest appears", streaming and stays
          and p.js("document.querySelector('#messages').scrollTop") == 0 and p.js("!!document.querySelector('.msg.assistant.pending')"),
          {"streaming": streaming, "jump": stays, "top": p.js("document.querySelector('#messages').scrollTop")})
    p.shot(out / "08-jump-to-latest-light.png")
    p.click("#chat-jump")
    time.sleep(0.3)
    check("Jump to latest goes to the end and follows the reply again", p.wait(AT_BOTTOM, 2)
          and p.js("document.querySelector('#chat-jump').hidden"))
    check("the streaming reply still has its own Stop", p.wait("!!document.querySelector('.msg.assistant.pending .msg-stop')", 3))
    p.click(".msg.assistant.pending .msg-stop")   # the reply's own Stop (the Send button is a Stop too)
    stopped = p.wait("!!document.querySelector('.msg.assistant.stopped') && !document.querySelector('.msg.assistant.pending')", 5)
    check("Stop ends the reply, keeps what was said and marks it", stopped
          and p.js(f"{LAST_REPLY}.querySelector('.body').textContent.trim().endsWith('…')")
          and p.js(f"{LAST_REPLY}.querySelector('.msg-flag')?.textContent") == "Stopped")
    check("after Stop the composer is ready again", p.wait(
        "!document.querySelector('#btn-send').classList.contains('is-stop') && document.querySelector('#btn-send').getAttribute('aria-label') === 'Send'"))
    time.sleep(1.0)
    saved = httpx.get(f"{BASE}/api/sessions/{current_id}").json()["messages"]
    check("the server kept the partial reply", len(saved) == 4 and saved[-1]["content"].endswith("…")
          and len(saved[-1]["content"]) < len(LONG_REPLY), [m["content"][:40] for m in saved])
    check("no error toast after Stop", not p.js("!!document.querySelector('.toast-error')"),
          p.js("document.querySelector('.toast-error')?.textContent"))
    p.size(*WIDE)


def check_retry(p: Page, out: Path, current_id: int) -> None:
    question = "BREAKONCE: what is a stimulus?"
    p.type("#input", question)
    p.key("Enter", "Enter", 13, text="\r")
    check("a failed reply shows a red card with Retry under the message", p.wait(
        "(() => { const e = document.querySelector('#messages .chat-error'); return !!e && e.textContent.includes('model crashed')"
        " && e.previousElementSibling?.matches('.msg.user') && !!e.querySelector('.chat-retry'); })()", 8),
          p.js("document.querySelector('#messages').lastElementChild?.textContent"))
    check("no empty reply is left, and the composer is free again", not p.js("!!document.querySelector('.msg.assistant.pending')")
          and p.js(f"!{LAST_REPLY}.textContent.includes('BREAKONCE')") and p.wait("!document.querySelector('#btn-send.is-stop')"))
    p.shot(out / "08b-error-retry-light.png")
    p.click("#messages .chat-retry")
    check("Retry sends the same text again and the reply arrives", p.wait(
        f"!document.querySelector('.chat-error') && !document.querySelector('.msg.assistant.pending')"
        f" && {LAST_REPLY}.textContent.includes('You said: BREAKONCE')"
        " && [...document.querySelectorAll('.msg.user .body')].filter(b => b.textContent === " + json.dumps(question) + ").length === 1", 15))
    p.wait("!document.querySelector('.fb-checking')", 8)
    saved = [m["content"] for m in httpx.get(f"{BASE}/api/sessions/{current_id}").json()["messages"]]
    check("the server has the question once, with its answer", saved.count(question) == 1 and "You said: BREAKONCE" in saved[-1],
          [m[:30] for m in saved])


def check_past_chat(p: Page, ids: dict) -> None:
    check("the ended chat's card: summary, next step, Continue this chat and New chat", p.js(
        "(() => { const e = document.querySelector('#messages .ended'); return !!e && e.querySelector('.ended-next')?.textContent.includes('Review the main ideas.')"
        " && [...e.querySelectorAll('.btn')].map(b => b.textContent).join() === 'Continue this chat,New chat'; })()"))
    check("the picker shows the past chat's course and topic", p.js(
        "document.querySelector('#chat-context .chat-context-topic')?.textContent === 'Memory'"))
    check("past replies have their action row, and no 'general knowledge' line", p.js(
        "document.querySelectorAll('#messages .msg.assistant .msg-copy').length === 2"
        " && !/general knowledge/i.test(document.querySelector('#messages').textContent)"))


def check_chat_more(p: Page, out: Path, ids: dict, current_id: int) -> None:
    """Switching the topic of a chat in progress, Rename from the top bar, the read-aloud toggle, the suggestion
    cards' actions, the first-run welcome, and the chat at 390 px and in the dark theme."""
    course, (classical, operant, memory) = ids["course"], ids["topics"]
    p.size(*WIDE)
    p.js("localStorage.setItem('theme', 'light'); document.documentElement.dataset.theme = 'light'")
    p.js("location.hash = '#chat'")
    p.wait(visible("#view-chat"))
    p.click("#sb-new-chat")
    p.wait("!!document.querySelector('#messages .chat-empty')")

    # A topic chosen with no chat open is only remembered; with a chat in progress it asks first
    picker_choose(p, course, operant)
    check("with no chat open, a new topic is chosen without asking", p.wait(
        "document.querySelector('#chat-context .chat-context-topic')?.textContent === 'Operant conditioning'")
          and not p.js("!!document.querySelector('dialog.dlg[open]')"))
    check("the first message starts the chat on it", send_and_wait(p, "What is operant conditioning?"))
    first = p.js("Number(document.querySelector('.sb-chat.is-open')?.dataset.id)")
    picker_choose(p, course, memory)
    check("switching the topic of a chat in progress asks first", p.wait(
        "document.querySelector('dialog.dlg[open] .dlg-title')?.textContent === 'End this chat and start one on Memory?'"))
    p.shot(out / "09-confirm-switch-light.png")
    p.click_text("dialog.dlg .btn", "Cancel")
    check("Cancel keeps the chat as it was", p.wait("!document.querySelector('dialog.dlg')")
          and p.js("document.querySelector('#chat-context .chat-context-topic')?.textContent") == "Operant conditioning"
          and p.js("Number(document.querySelector('.sb-chat.is-open')?.dataset.id)") == first)
    picker_choose(p, course, memory)
    p.wait("!!document.querySelector('dialog.dlg[open]')")
    p.click("dialog.dlg .btn-primary")
    check("confirming ends the chat (saved, summarised) and starts one on the new topic", p.wait(
        "document.querySelector('#chat-context .chat-context-topic')?.textContent === 'Memory'"
        " && !!document.querySelector('#messages .ended.is-compact') && !!document.querySelector('#messages .chat-empty')"
        f" && Number(document.querySelector('.sb-chat.is-open')?.dataset.id) !== {first}", 15)
          and httpx.get(f"{BASE}/api/sessions/{first}").json()["session"]["summary"])

    # Rename from the top bar's ⋯
    p.click("#btn-chat-menu")
    p.wait("!!document.querySelector('.popover-menu')")
    check("the chat menu has End session, Rename and Delete chat", p.js(
        "[...document.querySelectorAll('.popover-menu .menu-item')].map(b => b.textContent).join()") == "End session,Rename,Delete chat")
    p.click_text(".popover-menu .menu-item", "Rename")
    check("Rename asks for the title in a dialog", p.wait("document.activeElement?.matches('dialog.dlg-rename input')"))
    p.js("document.activeElement.select()")
    p.call("Input.insertText", text="Memory practice")
    p.key("Enter", "Enter", 13, text="\r")
    open_id = p.js("Number(document.querySelector('.sb-chat.is-open')?.dataset.id)")
    check("the chat is renamed", p.wait("!document.querySelector('dialog.dlg-rename')"
                                        " && document.querySelector('.sb-chat.is-open .sb-chat-text')?.textContent === 'Memory practice'")
          and httpx.get(f"{BASE}/api/sessions/{open_id}").json()["session"]["title"] == "Memory practice")

    # The read-aloud toggle in the top bar
    p.click("#btn-read-aloud")
    on = p.js("document.querySelector('#btn-read-aloud').getAttribute('aria-pressed')") == "true" and p.js("localStorage.getItem('readAloud')") == "1"
    p.click("#btn-read-aloud")
    check("the read-aloud toggle is a pressed button, remembered", on and p.js(
        "document.querySelector('#btn-read-aloud').getAttribute('aria-pressed') === 'false' && /off/.test(document.querySelector('#btn-read-aloud').title)"))

    # The suggestion cards do what they say
    httpx.post(f"{BASE}/api/courses/{course}/topics", json={"title": "Extinction"})
    p.click("#sb-new-chat")
    p.wait("[...document.querySelectorAll('.chat-suggest-title')].some(t => t.textContent === 'Next lesson')")
    p.js("[...document.querySelectorAll('.chat-suggest')].find(c => c.textContent.startsWith('Continue')).click()")
    check("Continue puts the last chat's next step in the composer, on its topic", p.wait(
        "document.querySelector('#input').value === \"Let's continue from last time. Explain operant conditioning in your own words.\""
        " && document.querySelector('#chat-context .chat-context-topic')?.textContent === 'Operant conditioning'"),
          p.js("document.querySelector('#input').value + ' / ' + document.querySelector('#chat-context').textContent"))
    p.js("(() => { const i = document.querySelector('#input'); i.value = ''; i.dispatchEvent(new Event('input')); })()")
    p.js("[...document.querySelectorAll('.chat-suggest')].find(c => c.textContent.startsWith('Next lesson')).click()")
    check("Next lesson starts a chat on the next new topic with the standard opening", p.wait(
        "[...document.querySelectorAll('.msg.user .body')].some(b => b.textContent.startsWith('Let\\'s start \"Extinction\"'))"
        " && document.querySelector('#chat-context .chat-context-topic')?.textContent === 'Extinction'"
        " && !document.querySelector('.msg.assistant.pending')", 15))
    p.wait("!document.querySelector('.fb-checking')", 8)
    p.click("#sb-new-chat")
    p.wait("[...document.querySelectorAll('.chat-suggest-title')].some(t => t.textContent === 'Practise pronunciation')")
    sound = p.js("[...document.querySelectorAll('.chat-suggest')].find(c => c.textContent.startsWith('Practise pronunciation'))"
                 ".querySelector('.chat-suggest-desc').textContent.split(' ')[0]")
    p.js("[...document.querySelectorAll('.chat-suggest')].find(c => c.textContent.startsWith('Practise pronunciation')).click()")
    check("Practise pronunciation opens the weakest sound's drill", page_ok(p, "pronunciation") and p.wait(
        "!!document.querySelector('#pron-practice .pron-say-text')"
        f" && (document.querySelector('#pron-practice .card-title')?.textContent || '').includes({json.dumps(sound)})"),
          [sound, p.js("document.querySelector('#pron-practice .card-title')?.textContent")])
    p.js("location.hash = '#chat'")
    p.wait(visible("#view-chat"))

    # The first run: no name yet, so the welcome shows the setup steps
    name = httpx.get(f"{BASE}/api/profile").json()["name"]
    httpx.put(f"{BASE}/api/profile", json={"name": ""})
    p.click("#sb-new-chat")
    check("on the first run the welcome shows the setup steps", p.wait(
        "[...document.querySelectorAll('.chat-suggest-title')].map(t => t.lastChild.textContent).join()"
        " === 'Tell the tutor about you,Add a course,Start talking'")
          and p.js("[...document.querySelectorAll('.chat-suggest')].map(c => c.classList.contains('is-done')).join()") == "false,true,false"
          and p.js("document.querySelector('.chat-empty-title').textContent").startswith("Good "))
    p.shot(out / "09b-welcome-first-run-light.png")
    httpx.put(f"{BASE}/api/profile", json={"name": name})

    # Dark
    p.js("localStorage.setItem('theme', 'dark'); document.documentElement.dataset.theme = 'dark'")
    p.click("#sb-new-chat")
    p.wait("document.querySelectorAll('.chat-suggest:not(.is-loading)').length >= 2")
    p.shot(out / "09c-welcome-dark.png")
    p.click(f".sb-chat[data-id='{current_id}'] .sb-chat-open")
    p.wait("document.querySelectorAll('#messages .msg').length >= 4")
    check("a chat opened from the sidebar shows its corrections open", p.js(
        "document.querySelector('.msg.user .fb-fix')?.getAttribute('aria-expanded') === 'true' && "
        + visible(".msg.user .fb-panel")))
    p.js("document.querySelector('#messages').scrollTop = 0")
    hover(p, "document.querySelector('#messages .msg.assistant')")
    p.shot(out / "09d-chat-dark.png")

    # 390 px: the picker stacks, the sources sheet comes up from the bottom, everything fits
    p.js("localStorage.setItem('theme', 'light'); document.documentElement.dataset.theme = 'light'")
    p.size(*NARROW, mobile=True)
    p.js("document.querySelector('#messages').scrollTop = 0")
    fit = p.js(NO_OVERFLOW)
    check("narrow: a chat with an open correction fits 390 px", fit["ok"], fit)
    p.shot(out / "41-mobile-chat-feedback-light.png")
    p.click(".msg.assistant .msg-sources")
    check("narrow: the Sources sheet comes up from the bottom", p.wait(
        "(() => { const d = document.querySelector('dialog.chat-sources[open]')?.getBoundingClientRect();"
        " return !!d && Math.abs(d.bottom - innerHeight) < 2 && d.width === innerWidth; })()"))
    p.shot(out / "42-mobile-sources-light.png")
    p.escape()
    p.wait("!document.querySelector('dialog.chat-sources')")
    p.click("#chat-context")
    check("narrow: the picker stacks the topics under the courses and fits", p.wait(
        "(() => { const c = document.querySelectorAll('.chat-picker-col'); const p = document.querySelector('.chat-picker')?.getBoundingClientRect();"
        " return c.length === 2 && c[1].getBoundingClientRect().top >= c[0].getBoundingClientRect().bottom - 1 && p.left >= 0 && p.right <= innerWidth; })()"))
    p.shot(out / "43-mobile-picker-light.png")
    p.escape()
    p.click("#sb-new-chat")
    check("narrow: the welcome stacks its cards and fits", p.wait(
        "document.querySelectorAll('.chat-suggest:not(.is-loading)').length >= 2")
          and p.js("(() => { const c = [...document.querySelectorAll('.chat-suggest')]; return c.every(x => Math.abs(x.getBoundingClientRect().left - c[0].getBoundingClientRect().left) < 1); })()")
          and p.js(NO_OVERFLOW)["ok"])
    p.shot(out / "44-mobile-welcome-light.png")
    p.size(*WIDE)
    p.js("localStorage.setItem('theme', 'system')")


# ---------------------------------------------------------------------------------------------- pages and settings
def wait_server(fn, timeout: float = 8.0) -> bool:
    """Polls the throwaway server until fn() is true: what the page saves arrives there a moment later."""
    end = time.time() + timeout
    while time.time() < end:
        try:
            if fn():
                return True
        except (httpx.HTTPError, KeyError, IndexError, StopIteration):
            pass
        time.sleep(0.1)
    return False


def set_files(p: Page, selector: str, paths: list[Path]) -> None:
    """Chooses files in an <input type=file> as the file picker would (DevTools sets them and fires change)."""
    obj = p.call("Runtime.evaluate", expression=f"document.querySelector({json.dumps(selector)})")["result"]["objectId"]
    p.call("DOM.setFileInputFiles", files=[str(x) for x in paths], objectId=obj)


def drop_files(p: Page, selector: str, files: dict[str, str]) -> bool:
    """Drags files (name -> text) over an element and drops them, with a real DataTransfer as from Explorer.
    Returns whether the element was highlighted while the files were over it."""
    return p.js(f"""(() => {{
      const dt = new DataTransfer();
      for (const [name, text] of Object.entries({json.dumps(files)})) dt.items.add(new File([text], name, {{ type: "text/plain" }}));
      const el = document.querySelector({json.dumps(selector)});
      const ev = type => el.dispatchEvent(new DragEvent(type, {{ dataTransfer: dt, bubbles: true, cancelable: true }}));
      ev("dragenter"); ev("dragover");
      const over = el.classList.contains("is-over");
      ev("drop");
      return over && !el.classList.contains("is-over");
    }})()""")


def home_card(title: str) -> str:
    return f"[...document.querySelectorAll('.home-card')].find(c => c.querySelector('.home-card-title').textContent === {json.dumps(title)})"


def check_home(p: Page, out: Path, ids: dict) -> None:
    p.click(".sb-item[data-route=home]")
    page_ok(p, "home")
    title = p.js("document.querySelector('.page-title').textContent")
    check("Home greets the learner by the time of day", re.fullmatch(r"Good (morning|afternoon|evening), Abdullah", title or ""), title)
    check("Home: Continue shows the last chat's summary and next step", p.js(
        "(() => { const c = document.querySelector('.home-continue'); return !!c && c.textContent.includes('We talked about classical')"
        " && c.querySelector('.home-next').textContent.includes('Explain operant conditioning'); })()"))
    check("Home: a due topic has the four grades and Review with tutor", p.js(
        f"(() => {{ const c = {home_card('Due reviews')}; const r = c?.querySelector('.home-review');"
        " return !!r && r.textContent.includes('Memory') && [...r.querySelectorAll('.home-grades .btn')].map(b => b.textContent).join() === 'Again,Hard,Good,Easy'"
        " && [...r.querySelectorAll('.btn')].some(b => b.textContent === 'Review with tutor'); })()"))
    check("Home: next topic, words due, weakest sounds and English focus", p.js(
        f"(() => {{ const next = {home_card('Next new topics')}, words = {home_card('Vocabulary')},"
        f" sounds = {home_card('Pronunciation focus')}, focus = {home_card('English focus')};"
        " return next.textContent.includes('Operant conditioning') && Number(words.querySelector('.home-stat-num').textContent) > 0"
        " && sounds.querySelectorAll('.home-sound').length === 2 && [...sounds.querySelectorAll('.btn')].some(b => b.textContent === 'Practise')"
        " && focus.textContent.includes('Past tense of go') && !!focus.querySelector('.wrong'); })()"),
          p.js("[...document.querySelectorAll('.home-card')].map(c => c.textContent.slice(0, 80))"))
    p.shot(out / "10a-home-light.png")
    p.js(f"[...{home_card('Due reviews')}.querySelectorAll('.home-grades .btn')].find(b => b.textContent === 'Good').click()")
    memory_topic = ids["topics"][2]

    def due_later():
        t = next(t for t in httpx.get(f"{BASE}/api/courses/{ids['course']}/topics").json() if t["id"] == memory_topic)
        return datetime.fromisoformat(t["due_at"]) > datetime.now(timezone.utc)
    check("grading a due topic schedules it and the card says so", p.wait(
        f"({home_card('Due reviews')}?.textContent || '').includes('Nothing to review right now')") and wait_server(due_later)
          and p.wait("(document.querySelector('.toast')?.textContent || '').includes('next review')"))
    weakest = next(s for s in httpx.get(f"{BASE}/api/pronunciation/progress").json()["sounds"] if s["accuracy"] < 0.8 and s["drill"])
    drill = next(d for d in httpx.get(f"{BASE}/api/pronunciation/drills").json() if d["id"] == weakest["drill"])
    p.js(f"[...{home_card('Pronunciation focus')}.querySelectorAll('.btn')].find(b => b.textContent === 'Practise').click()")
    check("Practise opens the pronunciation drill for that sound", page_ok(p, "pronunciation") and p.wait(
        f"document.querySelector('#pron-practice .card-title')?.textContent === {json.dumps(drill['title'])}"
        " && !!document.querySelector('#pron-practice .pron-say-text')") and not p.js("!!document.querySelector('.toast-error')"),
          p.js("location.hash + ' ' + document.querySelector('#pron-practice .card-title')?.textContent"))
    p.click(".sb-item[data-route=home]")
    page_ok(p, "home")
    p.click_text(".home-continue .btn", "Continue with the next step")
    check("Continue opens the chat with the next step in the box", p.wait(
        visible("#view-chat") + " && document.querySelector('#input').value.includes('Explain operant conditioning in your own words.')", 10),
          p.js("document.querySelector('#input').value"))
    p.js("(() => { const i = document.querySelector('#input'); i.value = ''; i.dispatchEvent(new Event('input')); })()")


def check_courses(p: Page, out: Path, ids: dict) -> None:
    course, first, second = ids["course"], ids["topics"][0], ids["topics"][1]
    topic = lambda tid: next(t for t in httpx.get(f"{BASE}/api/courses/{course}/topics").json() if t["id"] == tid)
    row = lambda tid: f".courses-topic[data-id='{tid}']"
    p.click(".sb-item[data-route=courses]")
    page_ok(p, "courses")
    check("Courses: the list and the selected course", p.wait(
        "document.querySelector('.courses-item[aria-current=true] .courses-item-name')?.textContent === 'Psychology'"))
    check("Courses lists the topics, numbered, with their status", p.wait("document.querySelectorAll('.courses-topic').length === 3")
          and p.js("[...document.querySelectorAll('.courses-topic')].map(li => li.querySelector('.courses-topic-num').textContent + ' '"
                   " + li.querySelector('.courses-topic-title').textContent).join('|')")
          == "1 Classical conditioning|2 Operant conditioning|3 Memory"
          and p.js("[...document.querySelectorAll('.courses-topic .badge')].every(b => ['new', 'learning', 'reviewing'].includes(b.textContent))"),
          p.js("[...document.querySelectorAll('.courses-topic')].map(li => li.textContent).join('|')"))

    # the name, edited in place
    p.js("(() => { const n = document.querySelector('.courses-name'); n.focus(); n.value = 'Psychology 101'; n.dispatchEvent(new Event('change')); })()")
    check("the course name is edited in place, saved, and shows Saved", p.wait("!document.querySelector('.courses-saved').hidden")
          and wait_server(lambda: httpx.get(f"{BASE}/api/courses").json()[0]["name"] == "Psychology 101")
          and p.wait("document.querySelector('.courses-item[aria-current=true] .courses-item-name').textContent === 'Psychology 101'"))
    p.js("(() => { const n = document.querySelector('.courses-name'); n.value = '  '; n.dispatchEvent(new Event('change')); })()")
    check("an empty name is refused inline and the old one kept", p.wait("!document.querySelector('.courses-head .courses-error').hidden")
          and p.js("document.querySelector('.courses-name').value") == "Psychology 101")
    p.js("(() => { const n = document.querySelector('.courses-name'); n.value = 'Psychology'; n.dispatchEvent(new Event('change')); })()")
    wait_server(lambda: httpx.get(f"{BASE}/api/courses").json()[0]["name"] == "Psychology")

    # material: the file picker, then a drop (a duplicate and a new file); results inline, per file
    tmp = Path(ids["tmp"])
    notes_text = "# Habituation\n\n" + MATERIAL
    (tmp / "lecture notes.md").write_bytes(notes_text.encode())
    (tmp / "slides.pptx").write_bytes(b"PK not really slides")
    set_files(p, ".courses-file", [tmp / "lecture notes.md", tmp / "slides.pptx"])
    check("chosen files upload one by one, each with its result", p.wait(
        "document.querySelectorAll('.courses-upload.is-done').length === 1 && document.querySelectorAll('.courses-upload.is-error').length === 1", 15),
          p.js("[...document.querySelectorAll('.courses-upload')].map(r => r.className + ': ' + r.textContent).join(' | ')"))
    check("an unsupported file is explained inline, not uploaded", "Not a PDF, TXT or MD file" in p.js(
        "document.querySelector('.courses-upload.is-error').textContent"))
    check("the documents table shows the new file", p.wait(
        "[...document.querySelectorAll('.courses-docs tbody tr')].some(r => r.textContent.includes('lecture notes.md'))"))
    over = drop_files(p, ".courses-drop", {"lecture notes.md": notes_text, "glossary.txt": "Habituation: a response fades when a harmless stimulus repeats."})
    check("files dragged over the drop zone highlight it", over)
    check("dropped files upload: the new one is added, the duplicate's error is inline", p.wait(
        "document.querySelectorAll('.courses-upload.is-done').length === 2 && document.querySelectorAll('.courses-upload.is-error').length === 2"
        " && [...document.querySelectorAll('.courses-upload.is-error')].some(r => r.textContent.includes('already in this course'))", 15),
          p.js("[...document.querySelectorAll('.courses-upload')].map(r => r.textContent).join(' | ')"))
    check("the table and the course list follow the uploads", p.wait(
        "document.querySelectorAll('.courses-docs tbody tr').length === 3"
        " && document.querySelector('.courses-item[aria-current=true] .courses-item-meta').textContent.startsWith('3 files')"))
    p.shot(out / "11a-courses-upload-light.png")
    p.js("[...document.querySelectorAll('.courses-docs tbody tr')].find(r => r.textContent.includes('glossary.txt')).querySelector('.icon-btn-danger').click()")
    p.wait("!!document.querySelector('dialog.dlg[open]')")
    p.click("dialog.dlg .btn-danger")
    check("deleting a file asks first, then removes it", p.wait("document.querySelectorAll('.courses-docs tbody tr').length === 2")
          and len(httpx.get(f"{BASE}/api/courses/{course}/documents").json()) == 2)
    p.click_text(".courses-uploads .btn", "Clear the list")
    check("the upload list can be cleared", p.wait("!document.querySelector('.courses-upload')"))

    # topics: move, rename in place, material, suggestions, delete
    p.click(f"{row(first)} [data-act=down]")
    check("Move down reorders the topics and the focus follows the topic", p.wait(
        f"document.querySelectorAll('.courses-topic')[1]?.dataset.id === '{first}'"
        f" && document.activeElement?.closest('.courses-topic')?.dataset.id === '{first}'"))
    p.click(f"{row(first)} [data-act=up]")
    check("Move up puts it back", p.wait(f"document.querySelectorAll('.courses-topic')[0]?.dataset.id === '{first}'")
          and p.js(f"document.querySelector(\"{row(first)} [data-act=up]\").disabled"))
    p.click(f"{row(second)} [data-act=rename]")
    check("Rename turns the title into a field", p.wait("document.activeElement?.classList.contains('courses-topic-edit')"))
    p.js("document.activeElement.select()")
    p.call("Input.insertText", text="Operant conditioning (Skinner)")
    p.key("Enter", "Enter", 13, text="\r")
    check("the new title is saved and shown", p.wait(
        f"document.querySelector(\"{row(second)} .courses-topic-title\")?.textContent === 'Operant conditioning (Skinner)'")
          and wait_server(lambda: topic(second)["title"] == "Operant conditioning (Skinner)"))
    p.click(f"{row(second)} [data-act=rename]")
    p.wait("document.activeElement?.classList.contains('courses-topic-edit')")
    p.call("Input.insertText", text=" draft")
    p.escape()
    check("Esc cancels a rename", p.wait(
        f"document.querySelector(\"{row(second)} .courses-topic-title\")?.textContent === 'Operant conditioning (Skinner)'")
          and topic(second)["title"] == "Operant conditioning (Skinner)")
    httpx.patch(f"{BASE}/api/topics/{second}", json={"title": "Operant conditioning"})   # as the other checks know it
    p.click(f"{row(first)} [data-act=material]")
    check("Material expands the topic's files", p.wait(visible(f"#courses-mat-{first}"))
          and p.js(f"document.querySelector(\"{row(first)} [data-act=material]\").getAttribute('aria-expanded')") == "true")
    p.click(f"#courses-mat-{first} input[type=checkbox]")
    check("ticking a file links it to the topic", wait_server(lambda: len(topic(first)["document_ids"]) == 1)
          and p.wait(f"document.querySelector(\"{row(first)} .courses-topic-docs\")?.textContent.includes('chapter 1.txt')"))
    p.click(f"#courses-mat-{first} input[type=checkbox]")
    check("unticking it unlinks it", wait_server(lambda: not topic(first)["document_ids"]))
    loading = p.js("(() => { const b = [...document.querySelectorAll('.courses-topics-card .btn')].find(b => b.textContent === 'Suggest topics');"
                   " b.click(); return b.getAttribute('aria-busy') === 'true' && b.disabled && b.textContent.includes('Reading'); })()")
    check("Suggest topics shows a loading state, then the suggestions", loading and p.wait(
        "[...document.querySelectorAll('.courses-suggest .check')].some(l => l.textContent === 'Habituation')", 10))
    p.shot(out / "11b-courses-suggest-light.png")
    p.click_text(".courses-suggest .btn", "Add selected")
    check("the selected suggestions are added", p.wait("document.querySelectorAll('.courses-topic').length === 4"))
    habituation = "[...document.querySelectorAll('.courses-topic')].find(li => li.textContent.includes('Habituation'))"
    p.js(f"{habituation}.querySelector('[data-act=delete]').click()")
    check("Delete topic asks in a dialog", p.wait("!!document.querySelector('dialog.dlg[open]')"))
    p.click_text("dialog.dlg .btn", "Cancel")
    check("Cancel keeps the topic", p.wait("!document.querySelector('dialog.dlg')") and p.js("document.querySelectorAll('.courses-topic').length") == 4)
    p.js(f"{habituation}.querySelector('[data-act=delete]').click()")
    p.wait("!!document.querySelector('dialog.dlg[open]')")
    p.click("dialog.dlg .btn-danger")
    check("Delete (confirmed) removes the topic", p.wait("document.querySelectorAll('.courses-topic').length === 3")
          and len(httpx.get(f"{BASE}/api/courses/{course}/topics").json()) == 3)

    # a new course, then deleting it
    p.click(".courses-new")
    p.wait("!!document.querySelector('dialog.dlg[open] .courses-new-form')")
    p.click("dialog.dlg .btn-primary")
    check("New course: an empty name is explained inline", p.wait("!document.querySelector('dialog.dlg .courses-error').hidden"))
    p.type("dialog.dlg .input", "Biology")
    p.click("dialog.dlg .btn-primary")
    check("a new course is created and selected", p.wait(
        "!document.querySelector('dialog.dlg') && document.querySelector('.courses-item[aria-current=true] .courses-item-name')?.textContent === 'Biology'"))
    check("an empty course says what to do in each part", p.js(
        "document.querySelectorAll('.courses-detail .empty-line, .courses-topics-empty').length === 2"))
    p.click_text(".courses-head .btn", "Delete course")
    check("Delete course asks in a dialog", p.wait("!!document.querySelector('dialog.dlg[open]')"))
    p.click("dialog.dlg .btn-danger")
    check("Delete course (confirmed) removes it", p.wait(
        "document.querySelectorAll('.courses-item').length === 1 && document.querySelector('.courses-item[aria-current=true] .courses-item-name')?.textContent === 'Psychology'")
          and len(httpx.get(f"{BASE}/api/courses").json()) == 1)


def check_mistakes_memory(p: Page, out: Path) -> None:
    p.click(".sb-item[data-route=mistakes]")
    page_ok(p, "mistakes")
    check("Mistakes: rule cards with examples", p.js("!!document.querySelector('.mist-card .mist-examples .wrong')"))
    p.click_text(".mist-card .btn", "Mark resolved")
    check("Mark resolved moves the rule to Resolved", p.wait(
        "!!document.querySelector('.mist-card.is-resolved') && [...document.querySelectorAll('.mist-section-title')].some(t => t.textContent === 'Resolved')")
          and all(m["resolved"] for m in httpx.get(f"{BASE}/api/mistakes").json()))
    p.shot(out / "14a-mistakes-resolved-light.png")
    p.click_text(".mist-card .btn", "Reopen")
    check("Reopen brings it back", p.wait("!document.querySelector('.mist-card.is-resolved')")
          and not any(m["resolved"] for m in httpx.get(f"{BASE}/api/mistakes").json()))
    p.click(".mist-card .icon-btn-danger")
    p.wait("!!document.querySelector('dialog.dlg[open]')")
    p.click_text("dialog.dlg .btn", "Cancel")
    check("Delete asks first; Cancel keeps the rule", p.wait("!document.querySelector('dialog.dlg')")
          and len(httpx.get(f"{BASE}/api/mistakes").json()) == 1)

    p.click(".sb-item[data-route=memory]")
    page_ok(p, "memory")
    before = p.js("document.querySelectorAll('.mem-item').length")
    p.click(".mem-add .btn-primary")
    check("Memory: an empty memory is explained inline", p.wait("!document.querySelector('.mem-add .mem-error').hidden"))
    p.type(".mem-add .input", "Likes examples from football.")
    p.click(".mem-add .btn-primary")
    check("a memory is added", p.wait(f"document.querySelectorAll('.mem-item').length === {before + 1}"))
    mem = lambda: next(m for m in httpx.get(f"{BASE}/api/memories").json() if m["content"].startswith("Likes examples"))
    mid = mem()["id"]
    item = f".mem-item[data-id='{mid}']"
    p.click(f"{item} .switch")
    check("the switch turns a memory off (kept, not used)", wait_server(lambda: not mem()["active"])
          and p.wait(f"document.querySelector(\"{item}\").classList.contains('is-off')"))
    p.click(f"{item} .switch")
    check("and on again", wait_server(lambda: mem()["active"]) and p.wait(f"!document.querySelector(\"{item}\").classList.contains('is-off')"))
    p.click(f"{item} [data-act=edit]")
    check("Edit opens the memory in place", p.wait("document.activeElement?.classList.contains('mem-edit-text')"))
    p.js("document.activeElement.select()")
    p.call("Input.insertText", text="Likes examples from football and chess.")
    p.key("Enter", "Enter", 13, text="\r")
    check("the edit is saved", wait_server(lambda: mem()["content"] == "Likes examples from football and chess.")
          and p.wait(f"document.querySelector(\"{item} .mem-content\")?.textContent === 'Likes examples from football and chess.'"))
    sessions = httpx.get(f"{BASE}/api/sessions").json()
    check("the session history lists the same chats as the sidebar, with summaries", p.js(
        "document.querySelectorAll('.mem-session').length") == len(sessions) and p.js(
        "[...document.querySelectorAll('.mem-summary')].some(s => s.textContent.includes('We talked about classical conditioning'))"))
    p.shot(out / "15a-memory-light.png")
    p.click(f"{item} [data-act=delete]")
    p.wait("!!document.querySelector('dialog.dlg[open]')")
    p.click("dialog.dlg .btn-danger")
    check("Delete (confirmed) forgets the memory", p.wait(f"!document.querySelector(\"{item}\")")
          and not any(m["id"] == mid for m in httpx.get(f"{BASE}/api/memories").json()))


def setting_input(label: str) -> str:
    """The control of a setting, found by its visible label."""
    return (f"(() => {{ const f = [...document.querySelectorAll('dialog.dlg-settings .set-field, dialog.dlg-settings .set-switch')]"
            f".find(f => f.querySelector('.set-label').textContent === {json.dumps(label)});"
            f" return f.querySelector('input:not([type=radio]), textarea, select') || f.querySelector('[role=radiogroup]'); }})()")


def check_settings(p: Page, out: Path) -> None:
    """Settings: tabs, every field saved on its own (with Saved), an invalid value inline, the voice preview, Data."""
    profile = lambda: httpx.get(f"{BASE}/api/profile").json()
    check("Settings has three tabs and opens on General", p.js(
        "[...document.querySelectorAll('dialog.dlg-settings [role=tab]')].map(t => t.textContent + ':' + t.getAttribute('aria-selected')).join()")
          == "General:true,Voice:false,Data:false")
    p.js(f"(() => {{ const i = {setting_input('Name')}; i.value = 'Abdullah K'; i.dispatchEvent(new Event('change')); }})()")
    check("a field is saved on its own and shows Saved", p.wait("document.querySelector('#sb-name').textContent === 'Abdullah K'")
          and wait_server(lambda: profile()["name"] == "Abdullah K")
          and p.js(f"!{setting_input('Name')}.closest('.set-field').querySelector('.set-saved').hidden"))
    p.js(f"(() => {{ const i = {setting_input('Name')}; i.value = 'x'.repeat(70); i.dispatchEvent(new Event('change')); }})()")
    check("an invalid value is explained under its field and not saved", p.wait(
        f"(() => {{ const i = {setting_input('Name')}; const e = i.closest('.set-field').querySelector('.set-error');"
        " return !e.hidden && e.textContent.includes('60 characters') && i.getAttribute('aria-invalid') === 'true'; })()")
          and profile()["name"] == "Abdullah K")
    p.js(f"(() => {{ const i = {setting_input('Name')}; i.value = 'Abdullah K'; i.dispatchEvent(new Event('change')); }})()")
    check("putting the value back clears the error", p.wait(f"{setting_input('Name')}.closest('.set-field').querySelector('.set-error').hidden"))
    p.js("document.querySelector('dialog.dlg-settings input[name=set-level][value=C1]').click()")
    check("the English level saves on click", wait_server(lambda: profile()["english_level"] == "C1"))
    p.js("document.querySelector('dialog.dlg-settings input[name=theme][value=dark]').click()")
    check("the Dark theme applies at once", p.wait(
        "document.documentElement.dataset.theme === 'dark' && getComputedStyle(document.body).backgroundColor === 'rgb(17, 17, 19)'"))
    p.shot(out / "21-settings-dark.png")
    readable(p, "dark: Settings › General")

    p.js("document.querySelector('#set-tab-general').focus()")
    p.key("ArrowDown", "ArrowDown", 40)
    check("arrow keys move between the tabs", p.wait(
        "document.activeElement?.id === 'set-tab-voice' && !document.querySelector('#set-panel-voice').hidden"
        " && document.querySelector('#set-panel-general').hidden"))
    p.js("__log.length = 0")
    p.click("dialog.dlg-settings .set-preview")
    check("Preview speaks with the chosen voice and speed (stubbed /api/voice/speak)", p.wait(
        "__log.some(l => l === 'POST /api/voice/speak')", 5) and p.wait(
        "(() => { const b = document.querySelector('dialog.dlg-settings .set-preview'); return !b.disabled && b.textContent.includes('Preview'); })()", 5)
          and p.js("!document.querySelector('#set-panel-voice .set-error:not([hidden])')"),
          p.js("[document.querySelector('dialog.dlg-settings .set-preview').textContent, document.querySelector('#set-panel-voice .set-error:not([hidden])')?.textContent]"))
    p.js(f"(() => {{ const r = {setting_input('Speed')}; r.value = '1.1'; r.dispatchEvent(new Event('input')); r.dispatchEvent(new Event('change')); }})()")
    check("the speed slider shows its value and saves", wait_server(lambda: profile()["speech_speed"] == "1.10")
          and p.js("document.querySelector('dialog.dlg-settings .range-value').textContent") == "1.10×")
    p.js(f"{setting_input('Careful grammar answers')}.click()")
    check("Careful grammar answers is a switch that saves", wait_server(lambda: profile()["careful_grammar"] == "on"))
    p.js("document.querySelector('dialog.dlg-settings input[name=set-patience][value=long]').click()")
    check("the pause before the tutor answers saves", wait_server(lambda: profile()["turn_patience"] == "long"))
    p.js(f"{setting_input('Read replies aloud')}.click()")
    check("Read replies aloud is kept in this browser", p.js("localStorage.getItem('readAloud')") == "1")
    p.js(f"{setting_input('Read replies aloud')}.click()")
    p.shot(out / "27-settings-voice-dark.png")
    readable(p, "dark: Settings › Voice")
    p.js(f"{setting_input('Careful grammar answers')}.click()")
    p.js("document.querySelector('dialog.dlg-settings input[name=set-patience][value=normal]').click()")
    wait_server(lambda: profile()["careful_grammar"] == "off" and profile()["turn_patience"] == "normal")

    p.click("#set-tab-data")
    check("Data shows the model and the materials folder", p.wait(
        "document.querySelector('#set-panel-data .set-table')?.textContent.includes('tutor · installed')"
        " && (document.querySelector('#set-panel-data .set-path-text')?.textContent || '').includes('materials')"),
          p.js("document.querySelector('#set-panel-data').textContent"))
    p.click_text("#set-panel-data .btn", "Back up now")
    check("Back up now shows where the copy was saved", p.wait(
        "[...document.querySelectorAll('#set-panel-data .set-path-text')].some(c => /backups[\\\\/]tutor-\\d{8}-\\d{6}\\.sqlite3$/.test(c.textContent))", 10),
          p.js("document.querySelector('#set-panel-data').textContent"))
    p.shot(out / "28-settings-data-dark.png")
    readable(p, "dark: Settings › Data")
    p.click("#set-tab-general")
    p.js(f"(() => {{ const t = {setting_input('Goals')}; t.focus(); t.value = 'Pass IELTS 7.'; }})()")   # no change event yet
    p.escape()
    check("Esc closes Settings and focus returns to the footer button",
          p.wait("!document.querySelector('dialog.dlg-settings')") and p.js("document.activeElement?.id === 'sb-user'"))
    check("a field still being typed in is saved when the dialog closes", wait_server(lambda: profile()["goals"] == "Pass IELTS 7."))


# ---------------------------------------------------------------------------------------------- vocabulary
VOCAB_COUNT = "document.querySelector('.vocab-review .vocab-count')?.textContent"
VOCAB_ROWS = "[...document.querySelectorAll('.vocab-table tbody tr')].filter(r => !r.hidden).length"


def run_vocabulary(p: Page, out: Path) -> None:
    """The review card with the keyboard (Space, 1-4) and the buttons, a second batch, the search and delete."""
    p.click(".sb-item[data-route=vocabulary]")
    page_ok(p, "vocabulary")
    check("vocabulary: the review card shows its progress", p.wait(f"{VOCAB_COUNT} === '1 of 12'")
          and p.js("document.querySelector('.vocab-review [role=progressbar]').getAttribute('aria-valuemax')") == "12",
          p.js(VOCAB_COUNT))
    p.js("document.activeElement.blur()")
    p.key(" ", "Space", 32, text=" ")
    check("vocabulary: Space shows the meaning and the grades", p.wait(
        "!!document.querySelector('.vocab-review .vocab-meaning') && document.querySelectorAll('.vocab-review .vocab-grade').length === 4"))
    p.shot(out / "18-vocabulary-review-light.png")
    p.key("3", "Digit3", 51, text="3")
    check("vocabulary: 3 grades it Good and the next word comes", p.wait(
        f"{VOCAB_COUNT} === '2 of 12' && !document.querySelector('.vocab-review .vocab-meaning')"))
    p.click(".vocab-review .vocab-reveal")
    p.wait("!!document.querySelector('.vocab-review .vocab-grade')")
    p.click(".vocab-review .vocab-grade[data-grade=hard]")
    check("vocabulary: the buttons work as well as the keys", p.wait(f"{VOCAB_COUNT} === '3 of 12'"))
    p.type(".vocab-search", "plastic")
    check("vocabulary: the search filters the table", p.wait(f"{VOCAB_ROWS} === 1"), p.js(VOCAB_ROWS))
    p.js("(() => { const s = document.querySelector('.vocab-search'); s.value = ''; s.dispatchEvent(new Event('input')); })()")
    words_before = p.js(VOCAB_ROWS)
    p.click(".vocab-table .vocab-delete")
    p.wait("!!document.querySelector('dialog.dlg[open]')")
    p.click_text("dialog.dlg .btn", "Cancel")
    check("vocabulary: Cancel keeps the word", p.wait("!document.querySelector('dialog.dlg')") and p.js(VOCAB_ROWS) == words_before)
    p.click(".vocab-table .vocab-delete")
    p.wait("!!document.querySelector('dialog.dlg[open]')")
    p.click("dialog.dlg .btn-danger")
    check("vocabulary: Delete (confirmed) removes the word", p.wait(f"{VOCAB_ROWS} === {words_before - 1}"))

    # More words due than the server sends at once: after the first batch the next one comes, not "done"
    for i in range(25):
        httpx.post(f"{BASE}/api/vocab", json={"word": f"batchword{i}", "meaning": f"meaning {i}"}).raise_for_status()
    due = httpx.get(f"{BASE}/api/plan").json()["due_vocab"]
    p.click(".sb-item[data-route=home]")
    page_ok(p, "home")
    p.click(".sb-item[data-route=vocabulary]")
    page_ok(p, "vocabulary")
    graded = p.wait(f"{VOCAB_COUNT} === '1 of 20'")
    for _ in range(20):
        p.js("document.activeElement.blur()")
        p.key(" ", "Space", 32, text=" ")
        graded = p.wait("!!document.querySelector('.vocab-review .vocab-meaning')", 4) and graded
        p.key("4", "Digit4", 52, text="4")
        graded = p.wait("!document.querySelector('.vocab-review .vocab-meaning')", 6) and graded
    check("vocabulary: the second batch follows the first", graded and p.wait(f"{VOCAB_COUNT} === '1 of {min(20, due - 20)}'"),
          p.js(VOCAB_COUNT))


# ---------------------------------------------------------------------------------------------- pronunciation
PRESSED_DRILL = "document.querySelector('.pron-drills .chip[aria-pressed=true]')?.textContent"


def run_pronunciation(p: Page, out: Path) -> None:
    """Opening a drill from elsewhere, recording (stubbed microphone) and the result of the stubbed check."""
    p.js("location.hash = '#pronunciation/th'")
    check("pronunciation: #pronunciation/<drill> opens the page on that drill", page_ok(p, "pronunciation") and p.wait(
        f"{PRESSED_DRILL} === '/θ/ and /ð/' && document.querySelector('#pron-practice .card-title')?.textContent === '/θ/ and /ð/'"),
        p.js(PRESSED_DRILL))
    p.js("document.dispatchEvent(new CustomEvent('tutor:navigate', { detail: 'pronunciation/f-v' }))")
    check("pronunciation: the route pronunciation/<drill> (Home's Practise) opens that drill",
          p.wait(f"{PRESSED_DRILL} === '/f/ and /v/'") and p.js("location.hash") == "#pronunciation", p.js("location.hash"))
    p.js("document.dispatchEvent(new CustomEvent('tutor:drill', { detail: 'ch-sh' }))")
    check("pronunciation: the drill event opens that drill", p.wait(f"{PRESSED_DRILL} === '/tʃ/ and /ʃ/'"))
    p.click_text(".pron-drills .chip", "/p/ and /b/")
    check("pronunciation: a drill opens with its word pairs", p.wait("document.querySelectorAll('#pron-practice .pron-pairs .chip').length > 2"))
    check("pronunciation: the practice panel shows the text, its sounds and Record", p.wait(
        "document.querySelector('#pron-practice .pron-say-text')?.textContent === 'pin'"
        " && document.querySelector('#pron-practice .pron-say-ipa').textContent.includes('p') && !!document.querySelector('#pron-rec')"))
    p.shot(out / "16-pronunciation-drill-light.png")

    def record():
        p.click("#pron-rec")
        ok = p.wait("document.querySelector('#pron-rec').classList.contains('is-recording')"
                    " && document.querySelector('#pron-status').textContent.includes('Listening')", 5)
        time.sleep(0.8)
        p.click("#pron-rec")
        return ok

    check("pronunciation: Record listens", record())
    check("pronunciation: the check colours the word", p.wait("!!document.querySelector('.pron-result .pron-word.s-wrong')", 8),
          p.js("document.querySelector('#pron-practice').textContent.slice(0, 300)"))
    check("pronunciation: its sounds say what was heard", p.js(
        "document.querySelector('.pron-phones').textContent.includes('heard /b/')"))
    check("pronunciation: \"sounded closer to\" from the server", p.js(
        "document.querySelector('.pron-notice')?.textContent") == 'It sounded closer to "bin".')
    check("pronunciation: the score and My recording", p.js(
        "document.querySelector('.pron-score').getAttribute('aria-label') === 'Score: 62 of 100'"
        " && [...document.querySelectorAll('.pron-say-actions .btn')].some(b => b.textContent === 'My recording')"))
    p.click("#pron-practice .pron-sentences .chip")
    p.wait("document.querySelector('#pron-practice .pron-say-text')?.textContent.split(' ').length > 2")
    record()
    p.wait("!!document.querySelector('.pron-result .pron-word.s-unsure')", 8)
    p.click(".pron-result .pron-word.s-unsure")
    check("pronunciation: another word shows its own sounds, an added vowel where it was said", p.wait(
        "document.querySelector('.pron-word.s-unsure').getAttribute('aria-pressed') === 'true'"
        " && /\\+\\/ə\\/ added.*heard \\/z\\//.test(document.querySelector('.pron-phones').textContent)"),
        p.js("document.querySelector('.pron-phones')?.textContent"))
    p.shot(out / "17-pronunciation-result-light.png")


# ---------------------------------------------------------------------------------------------- voice mode
VM_STATE = "document.querySelector('#voice-mode').dataset.state"
VM_WHERE = "document.querySelector('#view-chat').dataset.voice"
VM_SENT = "__ws[{}].sent.filter(s => typeof s === 'string').map(s => JSON.parse(s))"
# The words voice mode shows (what can be read on the screen: screen-reader-only text left out), and what its caption
# says to screen readers (the state's name too)
VM_TEXT = """(() => { const out = [];
  const walk = el => { for (const n of el.childNodes) {
    if (n.nodeType === 3 && n.textContent.trim()) out.push(n.textContent.trim());
    else if (n.nodeType === 1 && !n.classList.contains('sr-only')) walk(n); } };
  walk(document.querySelector('#voice-mode')); return out.join(' | '); })()"""
VM_SPOKEN = "document.querySelector('#vm-caption').textContent"
# A design token's colour as computed (to compare with an element's)
TOKEN = """(name => { const i = document.createElement('i'); i.style.color = `var(${name})`; document.body.append(i);
  const c = getComputedStyle(i).color; i.remove(); return c; })"""
# The orb as it looks: opacity, scale (1 + 0.25 × level), filter, its own animation and its clouds' (speed, play state)
ORB_LOOK = """(() => { const o = document.querySelector('#live-orb'), s = getComputedStyle(o);
  const clouds = o.getAnimations({ subtree: true }).filter(a => a.animationName?.startsWith('vm-cloud'));
  return { opacity: Number(s.opacity), scale: s.scale === 'none' ? 1 : parseFloat(s.scale), filter: s.filter,
           animation: s.animationName, cloudRates: clouds.map(a => a.playbackRate), cloudStates: clouds.map(a => a.playState),
           level: parseFloat(o.style.getPropertyValue('--level') || '0') }; })()"""
# The composer as voice mode leaves it: what it shows, in order, and what its buttons say and look like
COMPOSER = """(() => { const q = s => document.querySelector(s), cs = s => getComputedStyle(q(s)), token = """ + TOKEN + """;
  return {
  shown: !q('#composer').hidden && !q('#input').disabled,
  order: [...q('#composer').children].filter(e => !e.hidden).map(e => e.id),
  placeholder: q('#input').placeholder, hint: q('#composer-hint').textContent,
  hintShown: !q('#composer-hint').closest('[hidden]') && q('#composer-hint').getBoundingClientRect().height > 0,
  mic: [q('#btn-mic').getAttribute('aria-label'), q('#btn-mic').getAttribute('aria-pressed'), q('#btn-mic use').getAttribute('href')],
  mute: [q('#btn-mute').getAttribute('aria-label'), q('#btn-mute').getAttribute('aria-pressed'), q('#btn-mute use').getAttribute('href')],
  muteFilled: cs('#btn-mute').backgroundColor === token('--bad'),
  end: [q('#btn-voice-end').getAttribute('aria-label'), q('#btn-voice-end').title],
  round: ['#btn-mute', '#btn-voice-end'].every(s => cs(s).borderRadius === '50%' && q(s).offsetWidth === q(s).offsetHeight),
  endInverted: cs('#btn-voice-end').backgroundColor === token('--text') && cs('#btn-voice-end').color === token('--bg'),
  voice: [q('#btn-voice').getAttribute('aria-label'), q('#btn-voice').title, q('#btn-voice use').getAttribute('href')] }; })()"""
# Where the orb is and what it is made of: its size (layout, without the breath or the voice's scale), its centre
# against the chat's and the composer's and (in an empty chat) the empty message area's, the gap down to the
# composer, nothing around it (no strip, background, border or ring), the sphere and its two cloud layers, and the
# messages scrolling behind it, the last one clear of it once at the bottom
ORB = """(() => { const q = s => document.querySelector(s), r = s => q(s).getBoundingClientRect();
  const orb = q('#live-orb'), o = r('#live-orb'), c = r('#composer'), v = r('#view-chat'), m = r('#messages');
  const vm = getComputedStyle(q('#voice-mode')), st = getComputedStyle(orb);
  const a = getComputedStyle(orb, '::before'), b = getComputedStyle(orb, '::after');
  const box = q('#messages'); box.scrollTop = box.scrollHeight;
  const last = [...box.children].filter(e => e.getBoundingClientRect().height > 0).at(-1)?.getBoundingClientRect();
  return { size: orb.offsetWidth, dx: Math.round(o.left + o.width / 2 - (v.left + v.width / 2)),
    dxComposer: Math.round(o.left + o.width / 2 - (c.left + c.width / 2)),
    dy: Math.round(o.top + o.height / 2 - (m.bottom + c.top) / 2), gap: Math.round(c.top - o.bottom),
    bare: vm.backgroundColor === 'rgba(0, 0, 0, 0)' && vm.borderTopWidth === '0px' && st.borderTopWidth === '0px'
          && st.outlineStyle === 'none' && !orb.children.length,
    sphere: st.borderRadius === '50%' && st.overflow === 'hidden' && /radial-gradient/.test(st.backgroundImage)
            && !/url\\(/.test(st.backgroundImage + a.backgroundImage + b.backgroundImage) && /rgba?\\(/.test(st.boxShadow),
    clouds: [a.mixBlendMode, b.mixBlendMode, a.animationName, b.animationName, a.animationDuration, b.animationDuration,
             /blur/.test(a.filter) && /blur/.test(b.filter) && /radial-gradient/.test(a.backgroundImage + b.backgroundImage)],
    behind: m.bottom >= o.bottom - 1, lastClear: !last || last.bottom <= o.top - 4 }; })()"""
# The caption: its size and colour, and that it sits under the orb
CAPTION = """(() => { const c = document.querySelector('#vm-caption'), s = getComputedStyle(c), token = """ + TOKEN + """;
  return { size: s.fontSize, red: s.color === token('--bad'), muted: s.color === token('--text-2'),
           under: c.getBoundingClientRect().top >= document.querySelector('#live-orb').getBoundingClientRect().bottom - 1 }; })()"""
CLOUDS = ["soft-light", "soft-light", "vm-cloud-a", "vm-cloud-b", "20s", "14s", True]
TURN = "[...document.querySelectorAll('#messages .msg.user .body')].filter(b => b.textContent === {}).length"
def tutor_audio(ws: int, turn: int) -> str:
    """Half a second of the tutor's voice (a 220 Hz tone) for a turn, as the live endpoint sends it: turn, rate, PCM."""
    return ("(() => { const rate = 24000, n = rate / 2, buf = new ArrayBuffer(8 + n * 2), v = new DataView(buf);"
            f" v.setUint32(0, {turn}, true); v.setUint32(4, rate, true);"
            " for (let i = 0; i < n; i++) v.setInt16(8 + i * 2, Math.round(Math.sin(i / rate * 2 * Math.PI * 220) * 16000), true);"
            f" __ws[{ws}].onmessage({{ data: buf }}); }})()")


def orb_settles(p: Page, expr: str, timeout: float = 3) -> dict:
    """Waits until the orb's look (ORB_LOOK, as `o`) passes a test (its fades take 0.4 s); returns the look."""
    p.wait(f"(o => {expr})({ORB_LOOK})", timeout)
    return p.js(ORB_LOOK)


def run_voice_mode(p: Page, out: Path) -> None:
    """Voice mode (DESIGN.md sections 2 and 3) inside the chat, the server's messages played through the stubbed socket:
    one soft sphere (128 px in an empty chat, 44 px floating above the composer), its states by motion only, a caption
    only for Connecting…, Muted and errors; ChatGPT's composer ("Type", mute, ×, send only with text, no hint line);
    spoken and typed turns as ordinary messages; mute, ×, Esc, Ctrl+Shift+Space; 390 px; the light theme; reduced motion."""
    p.js("document.querySelector('#btn-voice').focus()")   # as a mouse click does; el.click() does not move focus
    p.click("#btn-voice")
    check("voice mode: the composer's voice mode button opens it inside the chat: no overlay, the app stays usable", p.wait(
        visible("#voice-mode") + " && document.querySelector('#view-chat').contains(document.querySelector('#voice-mode'))"
        " && !document.querySelector('#app').inert && !document.querySelector('[aria-modal=true], .vm-transcript, #btn-live')"
        " && getComputedStyle(document.querySelector('#voice-mode')).position !== 'fixed'", 10))
    check("voice mode: it connects to the live endpoint of a new session", p.wait(
        "__ws.length === 1 && __ws[0].readyState === 1 && /\\/api\\/live\\?session_id=\\d+$/.test(__ws[0].url)", 10),
        p.js("__ws.map(w => w.url)"))
    look = orb_settles(p, "Math.abs(o.opacity - .35) < .01")
    check("voice mode: connecting until the server says otherwise: a faint orb that breathes, captioned Connecting… (12 px, under it)",
          p.js(VM_STATE) == "connecting" and p.js(VM_TEXT) == "Connecting…" and abs(look["opacity"] - .35) < .01
          and look["animation"] == "vm-breath" and p.js(CAPTION) == {"size": "12px", "red": False, "muted": True, "under": True},
          [p.js(VM_TEXT), look, p.js(CAPTION)])
    composer = p.js(COMPOSER)
    check("voice mode: the composer is ChatGPT's: 'Type', a round mute (mic-off) and a round inverted × at the right,"
          " no send while the box is empty, no push-to-talk, no voice mode button, no hint line",
          composer["shown"] and composer["order"] == ["input", "btn-mute", "btn-voice-end"]
          and composer["placeholder"] == "Type" and not composer["hintShown"]
          and composer["mute"] == ["Mute the microphone", "false", "#i-mic-off"] and not composer["muteFilled"]
          and composer["end"] == ["End voice mode", "End voice mode (Esc)"] and composer["round"] and composer["endInverted"],
          composer)
    check("voice mode: the focus moves from the voice mode button to ×, which took its place",
          p.js("document.activeElement?.id") == "btn-voice-end", p.js("document.activeElement?.id"))
    orb = p.js(ORB)
    check("voice mode: in an empty chat the orb (128 px) sits in the middle of the message area, the welcome hidden",
          p.js(VM_WHERE) == "centred" and orb["size"] == 128 and abs(orb["dx"]) <= 2 and abs(orb["dy"]) <= 2
          and not p.js(visible("#messages .chat-empty")) and p.js("!!document.querySelector('#messages .chat-empty')"), orb)
    check("voice mode: the orb is one soft sphere: CSS gradients only, two blurred drifting cloud layers (soft-light,"
          " 20 s and 14 s), a soft glow; no ring, no border, no background around it",
          orb["sphere"] and orb["clouds"] == CLOUDS and orb["bare"], orb)
    p.js("__ws[0].serverSays({ type: 'state', state: 'listening' })")
    look = orb_settles(p, "o.opacity === 1")
    check("voice mode: listening: the orb breathes, nothing written (screen readers hear 'Listening')",
          p.js(VM_STATE) == "listening" and p.js(VM_TEXT) == "" and p.js(VM_SPOKEN) == "Listening"
          and look["animation"] == "vm-breath" and look["opacity"] == 1 and look["cloudRates"] == [1, 1],
          [p.js(VM_TEXT), p.js(VM_SPOKEN), look])
    p.shot(out / "25-voice-empty-dark.png")
    p.js("__ws[0].serverSays({ type: 'state', state: 'user_speaking' })")
    look = orb_settles(p, "o.level > 0.05 && o.scale > 1.01 && o.filter === 'brightness(1.08)'", 5)
    check("voice mode: the learner speaking: the orb swells with their voice (1 + 0.25 × level) and brightens, nothing written",
          p.js(VM_STATE) == "user_speaking" and p.js(VM_TEXT) == "" and look["level"] > 0.05
          and abs(look["scale"] - (1 + 0.25 * look["level"])) < 0.03 and look["filter"] == "brightness(1.08)", look)
    p.shot(out / "25f-voice-you-speaking-dark.png")
    readable(p, "dark: voice mode in an empty chat")

    # A spoken turn: the learner's words and the streamed reply are the chat's own messages, drawn once
    p.js("__ws[0].serverSays({ type: 'user_final', turn: 1, text: 'What does extinction means?' });"
         "__ws[0].serverSays({ type: 'state', state: 'thinking' })")
    check("voice mode: what the learner said is a user bubble, the reply to come a message with the caret", p.wait(
        f"{TURN.format(json.dumps('What does extinction means?'))} === 1"
        " && !!document.querySelector('#messages .msg.assistant.pending .msg-caret')"))
    look = orb_settles(p, "Math.abs(o.opacity - .78) < .01 && o.scale === 1")
    check("voice mode: thinking: the clouds drift faster and the orb dims a little, nothing written",
          p.js(VM_TEXT) == "" and abs(look["opacity"] - .78) < .01 and look["cloudRates"] == [3.5, 3.5]
          and look["scale"] == 1, look)
    check("voice mode: nothing is shown twice (no transcript of its own)", p.js(
        "[...document.querySelectorAll('#view-chat *')].filter(e => !e.children.length && e.textContent.includes('What does extinction means?')).length === 1"
        " && !document.querySelector('#voice-mode').textContent.includes('extinction')"))
    p.js("__ws[0].serverSays({ type: 'state', state: 'speaking' });"
         "__ws[0].serverSays({ type: 'token', turn: 1, text: 'Extinction is when **the response** fades' })")
    check("voice mode: the reply streams in as a typed one does (markdown, the caret at its end)", p.wait(
        f"{LAST_REPLY}.querySelector('.body').textContent === 'Extinction is when the response fades'"
        f" && !!{LAST_REPLY}.querySelector('.body strong') && !!{LAST_REPLY}.querySelector('.body .msg-caret')"),
        p.js(f"{LAST_REPLY}.querySelector('.body').textContent"))
    p.js(tutor_audio(0, 1))
    look = orb_settles(p, "o.level > 0.3 && o.scale > 1.05 && o.opacity === 1", 5)
    check("voice mode: speaking: the orb's size follows the tutor's voice, the clouds back to their pace, nothing written",
          p.js(VM_TEXT) == "" and look["level"] > 0.3 and look["scale"] > 1.05 and look["cloudRates"] == [1, 1]
          and look["opacity"] == 1, look)
    p.js("__ws[0].serverSays({ type: 'token', turn: 1, text: ' [S1].' });"
         "__ws[0].serverSays({ type: 'reply_done', turn: 1, sources: [], unknown_citations: [] });"
         "__ws[0].serverSays({ type: 'metrics', turn: 1, endpoint_ms: 640, first_token_ms: 1020, first_audio_ms: 2100 })")
    check("voice mode: the finished reply loses its caret, gets its action row and is announced; the English check is pending",
          p.wait("!document.querySelector('#messages .msg.assistant.pending') && !document.querySelector('#messages .msg-caret')"
                 f" && !!{LAST_REPLY}.querySelector('.msg-copy') && !!document.querySelector('.msg.user .fb-checking')"
                 " && document.querySelector('#chat-announcer').textContent.startsWith('Tutor: Extinction is when')"),
          p.js("document.querySelector('#chat-announcer').textContent"))
    check("voice mode: timings never reach the screen (the metrics go to the log only)", p.js(
        "!/after you stopped|began \\d|\\d\\.\\d s\\b/.test(document.querySelector('#view-chat').innerText)")
          and p.js(VM_TEXT) == "", p.js(VM_TEXT))
    p.js("__ws[0].serverSays({ type: 'feedback', turn: 1, feedback: { corrections: [{ wrong: 'does extinction means', right: 'does extinction mean',"
         " rule: 'base_verb_after_does', explanation: 'After does, the verb has no -s.' }], better_phrasing: 'What does extinction mean?',"
         " saved_words: [], saved_memories: [] } })")
    check("voice mode: a spoken turn's corrections are open under it, without a click", p.wait(
        "document.querySelector('.msg.user .fb-fix')?.getAttribute('aria-expanded') === 'true' && " + visible(".msg.user .fb-panel")
        + " && document.querySelector('.msg.user .fb-panel').textContent.includes('After does, the verb has no -s.')"))
    p.wait(visible("#voice-mode") + f" && {VM_WHERE} === 'docked'")
    time.sleep(0.4)   # the orb's fade-in
    orb = p.js(ORB)
    check("voice mode: with messages, the orb (44 px) floats centred just above the composer: no strip, no background,"
          " no border; the messages scroll behind it and the last one stays clear of it",
          p.js(VM_WHERE) == "docked" and orb["size"] == 44 and abs(orb["dx"]) <= 2 and abs(orb["dxComposer"]) <= 2
          and 8 <= orb["gap"] <= 40 and orb["bare"] and orb["sphere"] and orb["clouds"] == CLOUDS and orb["behind"]
          and orb["lastClear"], orb)
    before = p.js("document.querySelector('#live-orb').offsetTop + document.querySelector('#voice-mode').getBoundingClientRect().top")
    p.js("document.querySelector('#messages').scrollTop = 0")
    check("voice mode: the orb stays in place while the messages scroll",
          p.js("document.querySelector('#live-orb').offsetTop + document.querySelector('#voice-mode').getBoundingClientRect().top") == before
          and p.js(visible("#voice-mode")))
    p.js("document.querySelector('#messages').scrollTop = 1e6")
    p.shot(out / "25a-voice-docked-dark.png")
    readable(p, "dark: voice mode with messages")
    p.js("__ws[0].serverSays({ type: 'error', message: 'The voice could not be made.' })")
    check("voice mode: a server error shows as a toast and as the red caption under the orb (12 px)", p.wait(
        "(document.querySelector('.toast-error')?.textContent || '').includes('The voice could not be made.')")
          and p.js(VM_TEXT) == "The voice could not be made."
          and p.js(CAPTION) == {"size": "12px", "red": True, "muted": False, "under": True},
          [p.js(VM_TEXT), p.js(CAPTION)])
    p.shot(out / "25b-voice-error-dark.png")
    readable(p, "dark: voice mode with an error")
    p.js("document.querySelectorAll('.toast').forEach(t => t.remove())")
    p.js("__ws[0].serverSays({ type: 'user_final', turn: 2, text: 'And then' }); __ws[0].serverSays({ type: 'interrupt', turn: 2 })")
    check("voice mode: words the learner kept talking over leave the chat (and the red caption goes with the new turn)", p.wait(
        f"{TURN.format(json.dumps('And then'))} === 0 && document.querySelectorAll('#messages .msg').length === 2"
        f" && {VM_WHERE} === 'docked'") and p.js(VM_TEXT) == "", p.js(VM_TEXT))
    p.js("__ws[0].serverSays({ type: 'user_final', turn: 3, text: 'And then what happens?' });"
         "__ws[0].serverSays({ type: 'token', turn: 3, text: 'Then the response' });"
         "__ws[0].serverSays({ type: 'interrupt', turn: 3 })")
    check("voice mode: a reply cut short by the learner keeps what was said, with …", p.wait(
        f"{LAST_REPLY}.querySelector('.body').textContent === 'Then the response …'"
        " && !document.querySelector('#messages .msg.assistant.pending') && !document.querySelector('#messages .msg-caret')"))

    # Typing in voice mode: send appears with the text; Enter sends it over the live connection; the server's
    # user_final draws it
    p.js("__log.length = 0")
    sent_before = p.js(VM_SENT.format(0) + ".length")
    p.type("#input", "What about spontaneous recovery?")
    composer = p.js(COMPOSER)
    check("voice mode: send appears (enabled) once the box has text, before mute and ×",
          composer["order"] == ["input", "btn-send", "btn-mute", "btn-voice-end"]
          and not p.js("document.querySelector('#btn-send').disabled"), composer["order"])
    p.shot(out / "25c-voice-typing-dark.png")
    p.key("Enter", "Enter", 13, text="\r")
    check("voice mode: Enter sends the typed text over the live connection (not as a typed chat message), empties the box"
          " and send goes away again",
          p.wait(f"{VM_SENT.format(0)}.length === {sent_before + 1}")
          and p.js(VM_SENT.format(0) + ".at(-1)") == {"type": "text", "text": "What about spontaneous recovery?"}
          and p.js("document.querySelector('#input').value") == "" and p.js("document.querySelector('#btn-send').hidden")
          and p.js(COMPOSER)["order"] == ["input", "btn-mute", "btn-voice-end"]
          and not p.js("__log.some(l => l.includes('/messages'))"), p.js(VM_SENT.format(0)))
    p.js("__ws[0].serverSays({ type: 'user_final', turn: 4, text: 'What about spontaneous recovery?' });"
         "__ws[0].serverSays({ type: 'token', turn: 4, text: 'It is when the response comes back after a pause.' });"
         "__ws[0].serverSays({ type: 'reply_done', turn: 4, sources: [], unknown_citations: [] })")
    check("voice mode: the typed text comes back as a user bubble, once, and its reply follows", p.wait(
        f"{TURN.format(json.dumps('What about spontaneous recovery?'))} === 1"
        f" && {LAST_REPLY}.querySelector('.body').textContent === 'It is when the response comes back after a pause.'"
        " && !document.querySelector('#messages .msg.assistant.pending')"))

    # Mute: the mute button, and Ctrl+Space; no push-to-talk recording in voice mode
    streams = p.js("__streams.length")
    p.click("#btn-mute")
    check("voice mode: the mute button is pressed and filled, and tells the server once; no recording starts", p.wait(
        "document.querySelector('#btn-mute').getAttribute('aria-pressed') === 'true' && " + COMPOSER + ".muteFilled")
          and p.js("document.querySelector('#btn-mute use').getAttribute('href')") == "#i-mic-off"
          and p.js(VM_SENT.format(0) + ".at(-1)") == {"type": "mute", "muted": True}
          and p.js(VM_SENT.format(0) + ".filter(m => m.type === 'mute').length") == 1
          and not p.js("document.querySelector('#btn-mic').classList.contains('recording')") and p.js("__streams.length") == streams)
    p.js("__ws[0].serverSays({ type: 'state', state: 'muted' })")
    look = orb_settles(p, "Math.abs(o.opacity - .5) < .01")
    check("voice mode: muted: the orb fades to half and keeps still (no breath, the clouds paused), captioned Muted",
          p.js(VM_STATE) == "muted" and p.js(VM_TEXT) == "Muted" and abs(look["opacity"] - .5) < .01
          and look["animation"] == "none" and look["cloudStates"] == ["paused", "paused"] and look["scale"] == 1,
          [p.js(VM_TEXT), look])
    p.js("document.querySelector('#messages').scrollTop = 1e6")
    p.shot(out / "27-voice-muted-dark.png")
    readable(p, "dark: voice mode muted")
    p.js("document.activeElement.blur()")
    p.key(" ", "Space", 32, modifiers=2)
    check("voice mode: Ctrl+Space unmutes (and does not record)", p.wait(
        "document.querySelector('#btn-mute').getAttribute('aria-pressed') === 'false' && !" + COMPOSER + ".muteFilled")
          and p.js(VM_SENT.format(0) + ".at(-1)") == {"type": "mute", "muted": False}
          and not p.js("document.querySelector('#btn-mic').classList.contains('recording')") and p.js("__streams.length") == streams,
          p.js("[document.querySelector('#btn-mute').getAttribute('aria-pressed'), document.activeElement?.id, __streams.length, "
               + VM_SENT.format(0) + ".slice(-3)]"))
    p.js("__ws[0].serverSays({ type: 'state', state: 'listening' })")

    # × ends it: the microphone is released, the turns stay, the composer is as before
    p.js("document.querySelector('#btn-voice-end').focus()")
    p.click("#btn-voice-end")
    composer = p.js(COMPOSER)
    check("voice mode: × ends it, releases the microphone; the turns stay and the composer is back (hint line too), the focus on its voice button",
          p.wait("document.querySelector('#voice-mode').hidden && !document.querySelector('#view-chat').dataset.voice"
                 " && __ws[0].readyState === 3 && __streams.at(-1).getTracks().every(t => t.readyState === 'ended')")
          and p.js("document.activeElement?.id") == "btn-voice"
          and p.js("document.querySelectorAll('#messages .msg').length") == 6
          and composer["order"] == ["btn-mic", "btn-voice", "input", "btn-send"] and composer["placeholder"] == "Message the tutor…"
          and composer["mic"] == ["Talk", "false", "#i-mic"] and composer["hint"].startswith("Enter to send") and composer["hintShown"]
          and composer["voice"] == ["Voice mode", "Voice mode · Ctrl+Shift+Space", "#i-waveform"]
          and p.js("document.querySelector('#btn-send').disabled"),
          [p.js("document.activeElement?.id"), composer])

    # Ctrl+Shift+Space toggles it; Esc ends it from the message box; outside it Esc still cancels a recording
    p.js("document.activeElement.blur()")
    p.key(" ", "Space", 32, modifiers=10)
    check("voice mode: Ctrl+Shift+Space opens it, floating above the composer as the chat has messages (and does not start push-to-talk)", p.wait(
        visible("#voice-mode") + f" && __ws.length === 2 && __ws[1].readyState === 1 && {VM_WHERE} === 'docked'", 10)
          and not p.js("document.querySelector('#btn-mic').classList.contains('recording')"))
    # The light theme, wide: a dark sphere with a soft highlight
    p.js("localStorage.setItem('theme', 'light'); document.documentElement.dataset.theme = 'light'")
    p.js("__ws[1].serverSays({ type: 'state', state: 'listening' }); document.querySelector('#messages').scrollTop = 1e6")
    time.sleep(0.4)
    p.shot(out / "25d-voice-docked-light.png")
    readable(p, "light: voice mode with messages")
    p.js("localStorage.setItem('theme', 'dark'); document.documentElement.dataset.theme = 'dark'")
    p.key(" ", "Space", 32, modifiers=10)
    check("voice mode: Ctrl+Shift+Space again ends it", p.wait("document.querySelector('#voice-mode').hidden && __ws[1].readyState === 3"))
    p.key(" ", "Space", 32, modifiers=10)
    p.wait(visible("#voice-mode") + " && __ws.length === 3 && __ws[2].readyState === 1", 10)
    p.type("#input", "half a thought")
    p.escape()
    check("voice mode: Esc ends it, also from the message box (what was typed stays there)", p.wait(
        "document.querySelector('#voice-mode').hidden && __ws[2].readyState === 3")
          and p.js("document.querySelector('#input').value") == "half a thought")
    p.js("(() => { const i = document.querySelector('#input'); i.value = ''; i.dispatchEvent(new Event('input')); i.blur(); })()")
    p.click("#btn-mic")
    recording = p.wait("document.querySelector('#btn-mic').classList.contains('recording')", 5)
    p.escape()
    check("voice mode: once it has ended the mic is push-to-talk again, and Esc cancels the recording", recording and p.wait(
        "!document.querySelector('#btn-mic').classList.contains('recording')") and p.js("__ws.length") == 3)

    # 390 px (light theme): the orb above the composer and the composer fit; then a new, empty chat with the orb in its middle
    p.size(*NARROW, mobile=True)
    p.js("localStorage.setItem('theme', 'light'); document.documentElement.dataset.theme = 'light'")
    p.click("#btn-voice")
    opened = p.wait(visible("#voice-mode") + " && __ws.length === 4 && __ws[3].readyState === 1", 10)
    p.js("__ws[3].serverSays({ type: 'state', state: 'speaking' });"
         "__ws[3].serverSays({ type: 'user_final', turn: 1, text: 'How do I say thought?' });"
         "__ws[3].serverSays({ type: 'token', turn: 1, text: 'Put your tongue between your teeth and blow gently: th, th, thought.' })")
    time.sleep(0.4)
    fit = p.js(NO_OVERFLOW)
    orb = p.js(ORB)
    composer = p.js(COMPOSER)
    check("voice mode at 390 px: the orb (44 px) floats centred above the composer, the composer as ChatGPT's, everything fits",
          opened and fit["ok"] and p.js(VM_WHERE) == "docked" and orb["size"] == 44 and abs(orb["dx"]) <= 2
          and 8 <= orb["gap"] <= 40 and orb["bare"] and orb["lastClear"] and p.js(visible("#composer"))
          and composer["order"] == ["input", "btn-mute", "btn-voice-end"] and not composer["hintShown"]
          and p.js(visible("#btn-voice-end")) and p.js(visible("#btn-mute")) and p.js(VM_TEXT) == "",
          {"fit": fit, "orb": orb, "composer": composer})
    p.shot(out / "28-mobile-voice-light.png")
    readable(p, "narrow: voice mode with messages")
    p.js("__ws[3].serverSays({ type: 'error', message: 'The tutor could not hear you. Check the microphone in Windows settings.' })")
    p.wait("!!document.querySelector('.toast-error')")
    p.js("document.querySelectorAll('.toast').forEach(t => t.remove())")
    orb = p.js(ORB)
    check("voice mode at 390 px: a long error caption wraps under the orb and still fits", p.js(NO_OVERFLOW)["ok"]
          and p.js(CAPTION)["red"] and p.js(CAPTION)["under"] and orb["gap"] >= 8, [p.js(CAPTION), orb])
    p.shot(out / "28b-mobile-voice-error-light.png")
    p.click("#view-chat [data-action=drawer]")
    p.wait("document.querySelector('#app').classList.contains('sb-open')")
    p.click("#sb-new-chat")
    check("voice mode: leaving the chat (New chat) ends it", p.wait(
        "document.querySelector('#voice-mode').hidden && __ws[3].readyState === 3 && !!document.querySelector('#messages .chat-empty')", 10))
    p.wait("!document.querySelector('#app').classList.contains('sb-open') && document.querySelectorAll('.chat-suggest:not(.is-loading)').length >= 2")
    p.click("#btn-voice")
    opened = p.wait(visible("#voice-mode") + " && __ws.length === 5 && __ws[4].readyState === 1", 10)
    p.js("__ws[4].serverSays({ type: 'state', state: 'listening' })")
    time.sleep(0.4)
    fit = p.js(NO_OVERFLOW)
    orb = p.js(ORB)
    check("voice mode at 390 px: in an empty chat the orb (128 px) is in the middle of the message area, and it fits",
          opened and fit["ok"] and p.js(VM_WHERE) == "centred" and orb["size"] == 128 and abs(orb["dx"]) <= 2
          and abs(orb["dy"]) <= 2 and not p.js(visible("#messages .chat-empty")) and p.js(VM_TEXT) == "", {"fit": fit, "orb": orb})
    p.shot(out / "28a-mobile-voice-empty-light.png")
    readable(p, "narrow: voice mode in an empty chat")
    p.escape()
    check("voice mode: ended in an empty chat, the welcome comes back", p.wait(
        "document.querySelector('#voice-mode').hidden && " + visible("#messages .chat-empty")))

    # Wide, light theme, an empty chat; then reduced motion: a still sphere, the caption alone tells the state
    p.size(*WIDE)
    p.click("#btn-voice")
    opened = p.wait(visible("#voice-mode") + " && __ws.length === 6 && __ws[5].readyState === 1", 10)
    p.js("__ws[5].serverSays({ type: 'state', state: 'listening' })")
    time.sleep(0.4)
    orb = p.js(ORB)
    check("light: voice mode in an empty chat: the orb (128 px) in the middle",
          opened and p.js(VM_WHERE) == "centred" and orb["size"] == 128 and abs(orb["dx"]) <= 2 and abs(orb["dy"]) <= 2, orb)
    p.shot(out / "25e-voice-empty-light.png")
    readable(p, "light: voice mode in an empty chat")
    p.call("Emulation.setEmulatedMedia", features=[{"name": "prefers-color-scheme", "value": "light"},
                                                   {"name": "prefers-reduced-motion", "value": "reduce"}])
    p.js("__ws[5].serverSays({ type: 'state', state: 'user_speaking' })")
    time.sleep(0.5)
    still = p.js("""(() => { const o = document.querySelector('#live-orb'), s = getComputedStyle(o);
      return [s.animationName, getComputedStyle(o, '::before').animationName, getComputedStyle(o, '::after').animationName, s.scale,
              parseFloat(o.style.getPropertyValue('--level')) > 0.05]; })()""")
    p.js("__ws[5].serverSays({ type: 'state', state: 'muted' })")
    check("reduced motion: the sphere keeps still (no breath, no clouds drifting, no swelling with the voice); the caption still says Muted",
          still == ["none", "none", "none", "none", True] and p.js(VM_TEXT) == "Muted", [still, p.js(VM_TEXT)])
    p.call("Emulation.setEmulatedMedia", features=[{"name": "prefers-color-scheme", "value": "light"}])
    p.escape()
    p.wait("document.querySelector('#voice-mode').hidden && " + visible("#messages .chat-empty"))
    p.js("localStorage.setItem('theme', 'dark'); document.documentElement.dataset.theme = 'dark'")


# ---------------------------------------------------------------------------------------------- polish
# Where the keyboard focus is, and whether it shows: an accent outline (2 px or more), or for the message box the
# composer's border taking the focus colour
FOCUS_NOW = """(() => {
  for (const a of document.getAnimations()) { try { a.finish(); } catch { /* an endless one (a spinner) */ } }   // the focus styles' fades
  const el = document.activeElement, s = getComputedStyle(el);
  const colour = v => { const i = document.createElement('i'); i.style.color = `var(${v})`; document.body.append(i);
                        const c = getComputedStyle(i).color; i.remove(); return c; };
  const zone = el.closest('#sidebar') ? 'sidebar' : el.closest('.chat-top') ? 'topbar' : el.closest('#messages') ? 'messages'
             : el.closest('.chat-bottom') ? 'composer' : 'other';
  const box = document.querySelector('#composer'), accent = colour('--accent');
  const ring = el.id === 'input'
    ? getComputedStyle(box).borderTopColor !== colour('--border-strong') && getComputedStyle(box).borderTopColor !== colour('--border')
    : (s.outlineStyle !== 'none' && parseFloat(s.outlineWidth) >= 2 && s.outlineColor === accent)
      || (s.borderTopColor === accent && s.boxShadow !== 'none');   // a text field: accent border and soft ring
  const name = el.id ? '#' + el.id : (el.className && typeof el.className === 'string' ? '.' + el.className.trim().split(/\\s+/).join('.') : el.tagName);
  return { zone, ring, name };
})()"""


def tab_walk(p: Page, last: str = "input", limit: int = 120) -> list[dict]:
    """Tabs from the top of the sidebar to the element with id `last`; returns where the focus went, step by step."""
    p.js("document.querySelector('.sb-brand').focus()")
    p.key("Tab", "Tab", 9)
    p.key("Tab", "Tab", 9, modifiers=8)   # back to the first stop, by keyboard (so it shows its focus as a keyboard stop)
    stops = [p.js(FOCUS_NOW)]
    for _ in range(limit):
        p.key("Tab", "Tab", 9)
        stops.append(p.js(FOCUS_NOW))
        if stops[-1]["name"] == f"#{last}":
            break
    return stops


def check_polish(p: Page, out: Path, ids: dict, current_id: int) -> None:
    """Phase 3: the toast stack, the keyboard shortcuts, the sidebar's empty state and long titles, a long topic in the
    context pill, the Pronunciation focus rows, a short window, and the chat's tab order with visible focus."""
    course = ids["course"]
    p.size(*WIDE)
    p.js("localStorage.setItem('theme', 'light'); document.documentElement.dataset.theme = 'light'")
    p.js("location.hash = '#chat'")
    p.wait(visible("#view-chat"))

    # ------------------------------------------------------------------ toasts: a column of three at most
    toasts = "[...document.querySelectorAll('#toasts .toast:not(.is-leaving)')].map(t => t.querySelector('.toast-text').textContent)"
    p.js("document.querySelectorAll('#toasts .toast').forEach(t => t.remove())")
    p.call("Input.dispatchMouseEvent", type="mouseMoved", x=5, y=5)
    p.js("import('/static/js/dom.js').then(m => { window.__toast = m.toast; })")
    p.wait("!!window.__toast")
    started = time.time()
    p.js("__toast('First note'); __toast('Second note'); __toast('Third note'); __toast('A fourth one, an error', 'error')")
    time.sleep(0.5)   # past the slide-in
    layout = """(() => { const t = [...document.querySelectorAll('#toasts .toast')]; const r = t.map(x => x.getBoundingClientRect());
      return { texts: t.map(x => x.querySelector('.toast-text').textContent), closes: t.every(x => !!x.querySelector('.toast-close')),
               gaps: r.slice(1).map((b, i) => Math.round(b.top - r[i].bottom)), centres: r.map(b => Math.round(b.left + b.width / 2)),
               main: Math.round(document.querySelector('main').getBoundingClientRect().left + document.querySelector('main').clientWidth / 2),
               bottom: Math.round(innerHeight - r.at(-1).bottom) }; })()"""
    stack = p.js(layout)
    check("toasts stack in a column: three at most, the newest at the bottom, 8 px apart, each with its close button",
          stack["texts"] == ["Second note", "Third note", "A fourth one, an error"] and stack["gaps"] == [8, 8]
          and stack["closes"] and max(stack["centres"]) - min(stack["centres"]) <= 1 and stack["bottom"] >= 24, stack)
    check("the toasts are centred on the main area, under the content they speak about",
          all(abs(c - stack["main"]) <= 2 for c in stack["centres"]), stack)
    p.js("[...document.querySelectorAll('#toasts .toast')][1].querySelector('.toast-close').click()")
    check("a toast's close button removes that toast only", p.wait(f"{toasts}.join('|') === 'Second note|A fourth one, an error'", 2))
    p.js("__toast('Held note')")
    time.sleep(0.25)
    hover(p, "document.querySelector('#toasts .toast:last-child')")   # its timer waits while the pointer is on it
    time.sleep(max(0.0, started + 4.8 - time.time()))
    held = p.js(toasts)
    p.call("Input.dispatchMouseEvent", type="mouseMoved", x=5, y=5)
    check("each toast has its own timer: a note goes after 4 s, an error stays longer, a toast under the pointer waits",
          held == ["A fourth one, an error", "Held note"] and p.wait("!document.querySelector('#toasts .toast')", 9), held)
    p.js("__toast('Saved your words'); __toast('Course \"Biology\" created. Add its material next.'); __toast('Could not reach the tutor', 'error')")
    time.sleep(0.5)
    p.shot(out / "45-toast-stack-light.png")
    p.js("document.querySelectorAll('#toasts .toast').forEach(t => t.remove())")

    # ------------------------------------------------------------------ the keyboard shortcuts
    p.js("document.activeElement.blur()")
    p.key("/", "Slash", 191, modifiers=2)
    check("Ctrl+/ opens the keyboard shortcuts", p.wait("!!document.querySelector('dialog.dlg-keys[open]')")
          and p.js("document.querySelector('dialog.dlg-keys .dlg-title').textContent") == "Keyboard shortcuts")
    combos = p.js("[...document.querySelectorAll('dialog.dlg-keys .keys-combo')].map(d => d.textContent)") or []
    need = ["Enter", "Shift+Enter", "Ctrl+Space", "Ctrl+Shift+Space", "Esc", "/", "[", "Ctrl+K", "Ctrl+,", "Ctrl+/", "Space", "1–4"]
    check("the list has Enter, Shift+Enter, Ctrl+Space, Ctrl+Shift+Space, Esc, /, [, Ctrl+K, Ctrl+, Ctrl+/, Space and 1–4",
          all(c in combos for c in need), combos)
    p.shot(out / "46-shortcuts-light.png")
    readable(p, "the shortcuts")
    p.key("/", "Slash", 191, modifiers=2)
    check("Ctrl+/ again closes it", p.wait("!document.querySelector('dialog.dlg-keys')"))
    p.key(",", "Comma", 188, modifiers=2)
    p.wait("!!document.querySelector('dialog.dlg-settings[open]') && !!document.querySelector('#set-shortcuts')")
    p.js("document.querySelector('.set-panels').scrollTop = 99999")
    p.shot(out / "46a-settings-keyboard-light.png")
    p.js("document.querySelector('#set-shortcuts').focus()")
    p.click("#set-shortcuts")
    check("Settings › General has a Shortcuts link that opens the list over Settings", p.wait(
        "!!document.querySelector('dialog.dlg-keys[open]') && !!document.querySelector('dialog.dlg-settings[open]')"))
    p.escape()
    check("Esc closes the list only; Settings stays, the focus back on the link", p.wait("!document.querySelector('dialog.dlg-keys')")
          and p.js("!!document.querySelector('dialog.dlg-settings[open]')") and p.js("document.activeElement?.id") == "set-shortcuts")
    p.escape()
    p.wait("!document.querySelector('dialog.dlg-settings')")
    p.js("localStorage.setItem('theme', 'dark'); document.documentElement.dataset.theme = 'dark'")
    p.key("/", "Slash", 191, modifiers=2)
    p.wait("!!document.querySelector('dialog.dlg-keys[open]')")
    p.shot(out / "47-shortcuts-dark.png")
    readable(p, "dark: the shortcuts")
    p.escape()
    p.wait("!document.querySelector('dialog.dlg-keys')")
    p.js("localStorage.setItem('theme', 'light'); document.documentElement.dataset.theme = 'light'")
    p.size(*NARROW, mobile=True)
    p.key("/", "Slash", 191, modifiers=2)
    check("narrow: the shortcuts fill the screen and fit 390 px", p.wait(   # once it has popped in
        "(() => { const d = document.querySelector('dialog.dlg-keys[open]'); return !!d && d.getBoundingClientRect().width === innerWidth"
        " && [...d.querySelectorAll('*')].every(e => e.getBoundingClientRect().right <= innerWidth + 1); })()"),
          p.js("(() => { const d = document.querySelector('dialog.dlg-keys'); return [d?.getBoundingClientRect().width, innerWidth,"
               " [...(d?.querySelectorAll('*') || [])].filter(e => e.getBoundingClientRect().right > innerWidth + 1).map(e => e.className)]; })()"))
    p.shot(out / "48-mobile-shortcuts-light.png")
    p.escape()
    p.wait("!document.querySelector('dialog.dlg-keys')")
    p.size(*WIDE)

    # ------------------------------------------------------------------ the sidebar: no chats, a long title, ⋯ by keyboard
    p.js("import('/static/js/chat.js').then(m => { window.__chatMod = m; })")
    p.wait("!!window.__chatMod")
    p.js("(() => { window.__sessions = __chatMod.chat.sessions; __chatMod.chat.sessions = [];"
         " const s = document.querySelector('#sb-search'); s.value = ''; s.dispatchEvent(new Event('input')); })()")
    empty = p.js("(() => { const e = document.querySelector('#sb-chats .sb-empty'); if (!e) return null; const s = getComputedStyle(e);"
                 " const h = e.getBoundingClientRect().height - parseFloat(s.paddingTop) - parseFloat(s.paddingBottom);"
                 " return { text: e.textContent, lines: Math.round(h / parseFloat(s.lineHeight)) }; })()")
    check("no chats: one line says where they will be", empty and empty["text"] == "Your chats will appear here" and empty["lines"] == 1, empty)
    p.shot(out / "49-sidebar-empty-light.png")
    p.js("__chatMod.chat.sessions = __sessions; document.querySelector('#sb-search').dispatchEvent(new Event('input'))")
    row = f".sb-chat[data-id='{current_id}']"
    long_title = "A very long title about classical conditioning, the past tense of irregular verbs and much more besides"
    httpx.patch(f"{BASE}/api/sessions/{current_id}", json={"title": long_title}).raise_for_status()
    p.js("__chatMod.refreshSessions()")
    p.wait(f"document.querySelector(\"{row} .sb-chat-text\")?.textContent === {json.dumps(long_title)}")
    cut = p.js(f"(() => {{ const t = document.querySelector(\"{row} .sb-chat-text\"); const r = t.closest('.sb-chat').getBoundingClientRect();"
               " return { ellipsis: getComputedStyle(t).textOverflow === 'ellipsis' && t.scrollWidth > t.clientWidth,"
               " inside: r.right <= document.querySelector('#sidebar').getBoundingClientRect().right,"
               " oneLine: t.getBoundingClientRect().height < 24 }; })()")
    check("a long chat title stays on one line, cut with an ellipsis", all(cut.values()), cut)
    p.js(f"document.querySelector(\"{row} .sb-chat-open\").focus()")
    p.key("Tab", "Tab", 9)
    reached = p.wait(f"document.activeElement?.matches(\"{row} .sb-chat-more\") && getComputedStyle(document.activeElement).opacity === '1'", 2)
    p.key("Enter", "Enter", 13, text="\r")
    opened = p.wait("!!document.querySelector('.popover-menu') && !!document.activeElement?.classList.contains('menu-item')", 2)
    p.escape()
    check("Tab reaches a chat's ⋯, which shows and opens its menu with Enter (Esc returns to it)", reached and opened
          and p.wait(f"!document.querySelector('.popover-menu') && document.activeElement?.matches(\"{row} .sb-chat-more\")", 2))
    httpx.patch(f"{BASE}/api/sessions/{current_id}", json={"title": "Pavlov and the past tense"}).raise_for_status()
    p.js("__chatMod.refreshSessions()")

    # ------------------------------------------------------------------ a long topic in the context pill
    long_topic = "Classical conditioning and the many ways a neutral stimulus comes to trigger a learned response"
    topic = httpx.post(f"{BASE}/api/courses/{course}/topics", json={"title": long_topic}).json()
    p.click("#sb-new-chat")
    p.wait("!!document.querySelector('#messages .chat-empty')")
    picker_choose(p, course, topic["id"])
    p.wait(f"document.querySelector('#chat-context .chat-context-topic')?.textContent === {json.dumps(long_topic)}")
    pill = """(() => { const b = document.querySelector('#chat-context'), t = b.querySelector('.chat-context-topic');
      const c = b.querySelector('.chat-context-course'), r = b.getBoundingClientRect();
      return { ellipsis: getComputedStyle(t).textOverflow === 'ellipsis' && t.scrollWidth > t.clientWidth,
               course: !c.offsetWidth || c.scrollWidth <= c.clientWidth, oneLine: r.height <= 40,
               fits: r.right <= document.querySelector('.chat-top-actions').getBoundingClientRect().left + 1 }; })()"""
    wide = p.js(pill)
    check("a long topic in the pill is cut with an ellipsis; the course name stays whole", all(wide.values()), wide)
    p.shot(out / "50-long-topic-light.png")
    p.size(*NARROW, mobile=True)
    narrow = p.js(pill)
    check("narrow: the pill shows the topic only, cut with an ellipsis, and fits", all(narrow.values()), narrow)
    p.shot(out / "51-mobile-long-topic-light.png")
    p.size(*WIDE)
    httpx.delete(f"{BASE}/api/topics/{topic['id']}").raise_for_status()
    picker_choose(p, course, ids["topics"][0])
    p.wait("document.querySelector('#chat-context .chat-context-topic')?.textContent === 'Classical conditioning'")

    # ------------------------------------------------------------------ the weakest sounds line up (Home and Pronunciation)
    rows = {"home": ".home-sound", "pronunciation": ".pron-sound"}
    for size in (WIDE, NARROW):
        p.size(*size, mobile=size == NARROW)
        for route, sel in rows.items():
            p.js(f"location.hash = '#{route}'")
            page_ok(p, route)
            p.wait(f"document.querySelectorAll('{sel}').length >= 2")
            line = p.js(f"""(() => {{ const r = [...document.querySelectorAll('{sel}')];
              const bars = r.map(x => x.querySelector('.progress').getBoundingClientRect());
              const btns = r.map(x => x.querySelector('.btn')).filter(Boolean);
              return {{ rows: r.length, buttons: btns.length, lefts: [...new Set(bars.map(b => Math.round(b.left)))],
                        rights: [...new Set(bars.map(b => Math.round(b.right)))],
                        oneLine: btns.every(b => b.getBoundingClientRect().height <= 34) }}; }})()""")
            check(f"{route} at {size[0]} px: the sound bars start and end together, Practise stays on one line",
                  line["rows"] >= 2 and 0 < line["buttons"] < line["rows"] and len(line["lefts"]) == 1
                  and len(line["rights"]) == 1 and line["oneLine"], line)
    p.size(*WIDE)
    p.js("location.hash = '#home'")
    page_ok(p, "home")
    p.shot(out / "52-home-sounds-light.png")

    # ------------------------------------------------------------------ the on/off switches keep their track
    sizes = "[...document.querySelectorAll('{} .switch')].map(s => {{ const r = s.getBoundingClientRect(); return [Math.round(r.width), Math.round(r.height)]; }})"
    p.js("location.hash = '#memory'")
    page_ok(p, "memory")
    on_memory = p.js(sizes.format(".mem-list"))
    p.key(",", "Comma", 188, modifiers=2)
    p.wait("!!document.querySelector('dialog.dlg-settings[open]')")
    p.click("#set-tab-voice")
    p.wait("!document.querySelector('#set-panel-voice').hidden")
    in_settings = p.js(sizes.format("#set-panel-voice"))
    p.escape()
    p.wait("!document.querySelector('dialog.dlg-settings')")
    check("the on/off switches are 36 × 20 tracks, on Memory and in Settings › Voice",
          on_memory and len(in_settings) == 3 and all(s == [36, 20] for s in on_memory + in_settings), [on_memory, in_settings])

    # ------------------------------------------------------------------ a short window: the chats keep some room
    p.size(WIDE[0], 520)
    room = p.js("(() => { const b = document.querySelector('#sidebar .sb-body'), s = document.querySelector('#sidebar');"
                " return { body: Math.round(b.getBoundingClientRect().height), scrolls: s.scrollHeight > s.clientHeight }; })()")
    check("a short window: the chat list keeps room for a few chats and the sidebar scrolls", room["body"] >= 110 and room["scrolls"], room)
    p.shot(out / "53-short-window-light.png")
    p.size(*WIDE)

    # ------------------------------------------------------------------ the chat's tab order, focus visible in both themes
    p.js("location.hash = '#chat'")
    p.wait(visible("#view-chat"))
    p.click(".sb-chat.is-open .sb-chat-open")
    p.wait("document.querySelectorAll('#messages .msg.assistant .msg-actions button').length >= 1 && !document.querySelector('#composer').hidden")
    for t in ("light", "dark"):
        p.js(f"localStorage.setItem('theme', '{t}'); document.documentElement.dataset.theme = '{t}'")
        stops = tab_walk(p)
        order = ["sidebar", "topbar", "messages", "composer"]
        zones = [s["zone"] for s in stops]
        names = [s["name"] for s in stops]
        ranks = [order.index(z) if z in order else -1 for z in zones]
        check(f"{t}: Tab goes sidebar → top bar → messages (and their action rows) → composer",
              names[-1] == "#input" and -1 not in ranks and ranks == sorted(ranks)
              and all(any(x in n for n in names) for x in (".sb-chat-more", "#chat-context", "#btn-read-aloud",
                                                           "#btn-chat-menu", "#messages", "#btn-mic", "#btn-voice"))
              and any(s["zone"] == "messages" and ".msg-copy" in s["name"] for s in stops),
              list(zip(zones, names)))
        check(f"{t}: every stop shows its focus", all(s["ring"] for s in stops), [s["name"] for s in stops if not s["ring"]])
        if t == "dark":
            p.shot(out / "54-focus-composer-dark.png")
    p.js("localStorage.setItem('theme', 'system')")


def run_checks(p: Page, out: Path, ids: dict) -> None:
    errors_seen = 0

    def no_new_errors(label):
        nonlocal errors_seen
        errs = p.errors()
        check(f"{label}: no JavaScript errors", len(errs) == errors_seen, errs[errors_seen:])
        errors_seen = len(errs)

    p.size(*WIDE)
    # The screenshots must not depend on this computer's own light or dark setting
    p.call("Emulation.setEmulatedMedia", features=[{"name": "prefers-color-scheme", "value": "light"}])
    # ------------------------------------------------------------------ load and welcome
    p.call("Page.navigate", url=BASE + "/")
    check("the app loads and shows the chat", p.wait(visible("#view-chat") + " && location.hash === '#chat'", 15))
    check("welcome state when no chat is open", p.wait("!!document.querySelector('#messages .chat-empty')"))
    check("sidebar lists the past chats grouped by day",
          p.wait("document.querySelectorAll('#sb-chats .sb-chat').length === 2")
          and p.js("[...document.querySelectorAll('.sb-group-label')].map(e => e.textContent).join(',')") == "Yesterday,Older",
          p.js("document.querySelector('#sb-chats').textContent"))
    check("a chat on a topic is titled by the topic", p.js(
        "[...document.querySelectorAll('.sb-chat-text')].map(e => e.textContent).includes('Memory')"))
    check("a free chat is titled by its first message", p.js(
        "[...document.querySelectorAll('.sb-chat-text')].some(e => e.textContent.startsWith('Can you help me with my email'))"))
    check("model status pill", p.wait("document.querySelector('#model-status').textContent.includes('On GPU 100% · 6.0 GB')"),
          p.js("document.querySelector('#model-status').textContent"))
    check("learner's name and avatar in the footer", p.wait(
        "document.querySelector('#sb-name').textContent === 'Abdullah' && document.querySelector('#sb-avatar').textContent === 'A'"))
    check("due-words badge on Vocabulary", p.wait("!document.querySelector('#badge-vocabulary').hidden"),
          p.js("document.querySelector('#badge-vocabulary').textContent"))
    check("send is disabled while the box is empty", p.js("document.querySelector('#btn-send').disabled"))
    check_welcome(p)
    p.shot(out / "01-welcome-light.png")
    readable(p, "the welcome")

    # ------------------------------------------------------------------ choose the course and topic (the picker)
    check_context_picker(p, out, ids)

    # ------------------------------------------------------------------ a streamed reply and the English check
    p.type("#input", "I goed to school yesterday.")
    check("send is enabled with text", p.js("!document.querySelector('#btn-send').disabled"))
    p.key("Enter", "Enter", 13, text="\r")
    streaming = p.wait("!!document.querySelector('.msg.assistant.pending') && document.querySelector('#btn-send').classList.contains('is-stop')", 6)
    check("the reply streams, and Send becomes Stop", streaming)
    tokens = p.wait("(document.querySelector('.msg.assistant.pending .body')?.textContent || '').length > 10", 6)
    check("tokens appear while streaming", tokens)
    check("the streaming reply ends in a caret and has its own Stop", p.js(
        "!!document.querySelector('.msg.assistant.pending .body .msg-caret') && !!document.querySelector('.msg.assistant.pending .msg-stop')"))
    done = p.wait("!document.querySelector('.msg.assistant.pending') && !document.querySelector('#btn-send').classList.contains('is-stop')", 15)
    check("the reply completes and Stop turns back into Send", done)
    check("while the English check runs, a chip says so", p.wait("!!document.querySelector('.msg.user .fb-checking')", 2)
          or p.js("!!document.querySelector('.msg.user .fb-fix')"))
    check("markdown rendered (bold, list)", p.js(
        "!!document.querySelector('.msg.assistant .body strong') && !!document.querySelector('.msg.assistant .body ul')"))
    check("the English check shows the correction", p.wait(
        "(document.querySelector('.msg.user .feedback')?.textContent || '').includes('1 correction')", 8),
        p.js("document.querySelector('.msg.user .feedback')?.textContent"))
    check("the user's text is right-aligned in a bubble, its direction from its own text", p.js(
        "(() => { const b = document.querySelector('.msg.user .body'); return b.getAttribute('dir') === 'auto'"
        " && getComputedStyle(b).whiteSpace === 'pre-wrap' && b.getBoundingClientRect().right > document.querySelector('.msg.assistant .body').getBoundingClientRect().right - 2; })()"))
    check("the completed reply is announced to screen readers",
          p.js("document.querySelector('#chat-announcer').textContent.startsWith('Tutor: Good question')"))
    check("the new chat is in the sidebar, open and current", p.wait(
        "(() => { const r = document.querySelector('.sb-chat.is-active'); return !!r && r.classList.contains('is-open')"
        " && r.querySelector('.sb-chat-text').textContent === 'Classical conditioning'; })()"))
    current_id = p.js("Number(document.querySelector('.sb-chat.is-active').dataset.id)")
    check_reply_details(p, out)
    p.shot(out / "02-chat-reply-light.png")
    readable(p, "a chat with a reply and its English check")
    no_new_errors("chat")

    # ------------------------------------------------------------------ stop a reply (and Jump to latest)
    check_stop(p, out, current_id)
    no_new_errors("stop")

    # ------------------------------------------------------------------ a reply that fails, and Retry
    check_retry(p, out, current_id)
    no_new_errors("retry")

    # ------------------------------------------------------------------ push-to-talk (stubbed microphone and Whisper)
    p.click("#btn-mic")
    check("the mic records and the hint shows it", p.wait(
        "document.querySelector('#btn-mic').classList.contains('recording')"
        " && document.querySelector('#composer-hint').textContent.includes('Listening')", 5))
    time.sleep(0.7)
    p.click("#btn-mic")
    check("the transcript lands in the box (send after speaking is off)", p.wait(
        "document.querySelector('#input').value === 'I goed to the library'"
        " && !document.querySelector('#btn-mic').classList.contains('recording')", 8))
    p.js("document.querySelector('#input').value = ''; document.querySelector('#input').dispatchEvent(new Event('input'))")
    p.js("document.activeElement.blur(); __log.length = 0")
    p.key(" ", "Space", 32, modifiers=2)
    started = p.wait("document.querySelector('#btn-mic').classList.contains('recording')", 5)
    time.sleep(0.6)
    p.escape()
    check("Ctrl+Space talks and Esc cancels without transcribing", started and p.wait(
        "!document.querySelector('#btn-mic').classList.contains('recording')") and not p.js(
        "__log.some(l => l.includes('/api/voice/transcribe'))"))
    no_new_errors("push-to-talk")

    # ------------------------------------------------------------------ open a past chat, rename, continue, delete
    p.click(f".sb-chat[data-id='{ids['memory_chat']}'] .sb-chat-open")
    opened = p.wait("document.querySelectorAll('#messages .msg').length === 4 && !!document.querySelector('#messages .ended')")
    check("a past chat opens read-only with its summary", opened
          and p.js("document.querySelector('#composer').hidden")
          and "Studied the stages of memory." in p.js("document.querySelector('#messages .ended').textContent"))
    check("the past chat is highlighted in the sidebar",
          p.js(f"document.querySelector('.sb-chat.is-active')?.dataset.id === '{ids['memory_chat']}'"))
    check_past_chat(p, ids)
    p.shot(out / "03-past-chat-light.png")

    p.click(f".sb-chat[data-id='{current_id}'] .sb-chat-more")
    check("the row menu opens", p.wait("!!document.querySelector('.popover-menu')"))
    p.shot(out / "04-row-menu-light.png")
    p.click_text(".popover-menu .menu-item", "Rename")
    check("Rename edits the title in place", p.wait("document.activeElement?.classList.contains('sb-chat-rename')"))
    p.js("document.activeElement.select()")
    p.call("Input.insertText", text="Pavlov and the past tense")
    p.key("Enter", "Enter", 13, text="\r")
    check("the new title is saved", p.wait(
        f"document.querySelector(\".sb-chat[data-id='{current_id}'] .sb-chat-text\")?.textContent === 'Pavlov and the past tense'")
          and httpx.get(f"{BASE}/api/sessions/{current_id}").json()["session"]["title"] == "Pavlov and the past tense")

    p.click(f".sb-chat[data-id='{ids['free_chat']}'] .sb-chat-more")
    p.wait("!!document.querySelector('.popover-menu')")
    p.click_text(".popover-menu .menu-item", "Continue this chat")
    continued = p.wait(f"document.querySelector('.sb-chat.is-open')?.dataset.id === '{ids['free_chat']}'"
                       " && !document.querySelector('#composer').hidden && document.querySelector('#busy').hidden", 15)
    check("Continue this chat reopens it with the composer", continued)
    ended = httpx.get(f"{BASE}/api/sessions/{current_id}").json()["session"]
    check("the chat that was open is ended and summarised", ended["ended_at"] and ended["summary"], ended.get("summary"))

    p.click(f".sb-chat[data-id='{ids['memory_chat']}'] .sb-chat-more")
    p.wait("!!document.querySelector('.popover-menu')")
    p.click_text(".popover-menu .menu-item", "Delete")
    check("Delete asks in a dialog", p.wait("!!document.querySelector('dialog.dlg[open]')"))
    check("the dialog's safe choice has the focus", p.js("document.activeElement?.textContent === 'Cancel'"))
    p.shot(out / "05-confirm-delete-light.png")
    p.escape()
    check("Esc cancels the dialog and nothing is deleted", p.wait("!document.querySelector('dialog.dlg')")
          and httpx.get(f"{BASE}/api/sessions/{ids['memory_chat']}").status_code == 200)
    check("focus returns to the menu's button", p.js(
        "document.activeElement?.classList.contains('sb-chat-more') || document.activeElement?.tagName === 'BODY'"))
    p.click(f".sb-chat[data-id='{ids['memory_chat']}'] .sb-chat-more")
    p.wait("!!document.querySelector('.popover-menu')")
    p.click_text(".popover-menu .menu-item", "Delete")
    p.wait("!!document.querySelector('dialog.dlg[open]')")
    p.click("dialog.dlg .btn-danger")
    check("the chat is deleted", p.wait(f"!document.querySelector(\".sb-chat[data-id='{ids['memory_chat']}']\")")
          and httpx.get(f"{BASE}/api/sessions/{ids['memory_chat']}").status_code == 404)
    check("the search field filters the chats", p.js(
        "(() => { const s = document.querySelector('#sb-search'); s.value = 'pavlov'; s.dispatchEvent(new Event('input'));"
        " const n = document.querySelectorAll('.sb-chat').length; s.value = ''; s.dispatchEvent(new Event('input'));"
        " return n === 1 && document.querySelectorAll('.sb-chat').length === 2; })()"))
    no_new_errors("sidebar chats")

    # ------------------------------------------------------------------ the chat menu: end the open chat
    p.click("#btn-chat-menu")
    p.wait("!!document.querySelector('.popover-menu')")
    p.click_text(".popover-menu .menu-item", "End session")
    check("End session shows the summary and hides the composer", p.wait(
        "!!document.querySelector('#messages .ended') && document.querySelector('#composer').hidden", 15))
    p.click_text("#messages .ended .btn", "New chat")
    check("New chat (from the ended card) shows the welcome", p.wait(
        "!!document.querySelector('#messages .chat-empty') && !document.querySelector('#composer').hidden"))

    # ------------------------------------------------------------------ every page
    # One topic is due for review now, for Home's grade buttons (the seed's past chat scheduled it for later)
    with closing(sqlite3.connect(ids["db"], timeout=10)) as db, db:
        db.execute("UPDATE topics SET due_at = ? WHERE id = ?",
                   ((datetime.now(timezone.utc) - timedelta(hours=1)).isoformat(timespec="seconds"), ids["topics"][2]))
    for i, route in enumerate(PAGES):
        p.click(f".sb-item[data-route={route}]")
        ok = page_ok(p, route)
        check(f"page {route} renders", ok and p.js(
            f"document.querySelector('.sb-item[data-route={route}]').getAttribute('aria-current') === 'page'"))
        p.shot(out / f"1{i}-{route}-light.png")
        readable(p, f"page {route}")
    check_home(p, out, ids)
    check_courses(p, out, ids)
    check_mistakes_memory(p, out)
    no_new_errors("home, courses, mistakes and memory")
    run_vocabulary(p, out)
    run_pronunciation(p, out)
    no_new_errors("pages")

    # ------------------------------------------------------------------ hash routing
    p.js("location.hash = '#today'")
    check("an old address (#today) opens Home", page_ok(p, "home"))
    p.js("location.hash = '#constructor'")
    time.sleep(0.6)
    check("an unknown address is ignored", p.js("location.hash") == "#home" and p.js("!document.querySelector('#view-page').hidden"))
    p.js("location.hash = '#profile'")
    check("#profile opens the settings dialog", p.wait("!!document.querySelector('dialog.dlg-settings[open]')"))
    p.escape()
    p.wait("!document.querySelector('dialog.dlg-settings')")

    # ------------------------------------------------------------------ settings and the theme
    p.click(".sb-item[data-route=home]")
    page_ok(p, "home")
    p.js("document.querySelector('#sb-user').focus()")   # as a mouse click does; el.click() does not move focus
    p.click("#sb-user")
    check("Settings opens from the sidebar footer", p.wait("!!document.querySelector('dialog.dlg-settings[open]')"))
    check("Settings has the learner's profile", p.wait(
        "[...document.querySelectorAll('dialog.dlg-settings .input')].some(i => i.value === 'Abdullah')"))
    check("Settings lists the voices (stubbed)", p.wait(
        "[...document.querySelectorAll('dialog.dlg-settings option')].some(o => o.textContent.startsWith('Heart'))"))
    p.shot(out / "20-settings-light.png")
    readable(p, "Settings › General")
    check_settings(p, out)   # per-field saving, an invalid value, the theme, the voice preview, Data; closes with Esc
    p.call("Page.reload")
    check("the theme is applied before the first paint after a reload", p.wait(
        "document.readyState !== 'loading' && document.documentElement.dataset.theme === 'dark'", 10))
    p.wait(visible("#view-page") + " && !!document.querySelector('.page-title')", 10)
    p.shot(out / "22-home-dark.png")
    readable(p, "dark: page home")
    for route in ("courses", "vocabulary", "pronunciation", "mistakes", "memory"):
        p.click(f".sb-item[data-route={route}]")
        page_ok(p, route)
        p.shot(out / f"23-{route}-dark.png")
        readable(p, f"dark: page {route}")
    p.js("location.hash = '#chat'")
    p.wait(visible("#view-chat"))
    p.click(f".sb-chat[data-id='{current_id}'] .sb-chat-open")
    p.wait("document.querySelectorAll('#messages .msg').length >= 4")
    p.shot(out / "24-chat-dark.png")
    readable(p, "dark: a past chat")
    # "System" follows the operating system's setting
    p.js("localStorage.setItem('theme', 'system'); delete document.documentElement.dataset.theme")
    p.call("Emulation.setEmulatedMedia", features=[{"name": "prefers-color-scheme", "value": "dark"}])
    check("System theme follows a dark system", p.js("getComputedStyle(document.body).backgroundColor") == "rgb(17, 17, 19)")
    p.call("Emulation.setEmulatedMedia", features=[{"name": "prefers-color-scheme", "value": "light"}])
    check("System theme follows a light system", p.js("getComputedStyle(document.body).backgroundColor") == "rgb(255, 255, 255)")
    p.js("localStorage.setItem('theme', 'dark'); document.documentElement.dataset.theme = 'dark'")
    no_new_errors("settings and theme")

    # ------------------------------------------------------------------ voice mode (stubbed WebSocket and microphone)
    p.click_text("#messages .ended .btn", "New chat")
    # The new chat has settled once it puts the focus in the message box
    p.wait("!!document.querySelector('#messages .chat-empty') && document.activeElement?.id === 'input'")
    run_voice_mode(p, out)
    no_new_errors("voice mode")

    # ------------------------------------------------------------------ keyboard
    p.js("document.activeElement.blur()")
    p.key("[", "BracketLeft", 219, text="[")
    check("[ collapses the sidebar to a rail", p.wait(
        "document.querySelector('#app').classList.contains('sb-collapsed') && document.querySelector('#sidebar').getBoundingClientRect().width === 56"))
    p.shot(out / "26-rail-dark.png")
    p.key("[", "BracketLeft", 219, text="[")
    check("[ expands it again", p.wait("!document.querySelector('#app').classList.contains('sb-collapsed')"))
    p.key(",", "Comma", 188, modifiers=2)
    check("Ctrl+, opens Settings", p.wait("!!document.querySelector('dialog.dlg-settings[open]')"))
    p.escape()
    p.wait("!document.querySelector('dialog.dlg-settings')")
    p.key("/", "Slash", 191, text="/")
    check("/ focuses the composer", p.js("document.activeElement?.id === 'input'"))
    p.js("document.activeElement.blur()")

    # ------------------------------------------------------------------ 390 px
    p.js("localStorage.setItem('theme', 'light'); document.documentElement.dataset.theme = 'light'")
    p.size(*NARROW, mobile=True)
    check("narrow: the sidebar is off-canvas", p.js(
        "document.querySelector('#sidebar').getBoundingClientRect().right <= 0 && document.querySelector('#sidebar').inert"))
    fit = p.js(NO_OVERFLOW)
    check("narrow: the chat fits 390 px", fit["ok"], fit)
    p.shot(out / "30-mobile-chat-light.png")
    p.click("#view-chat [data-action=drawer]")
    check("narrow: the drawer opens", p.wait(
        "document.querySelector('#app').classList.contains('sb-open') && document.querySelector('#sidebar').getBoundingClientRect().left >= 0"
        " && document.querySelector('#view-chat [data-action=drawer]').getAttribute('aria-expanded') === 'true'"))
    p.shot(out / "31-mobile-drawer-light.png")
    p.escape()
    check("narrow: Esc closes the drawer", p.wait("!document.querySelector('#app').classList.contains('sb-open')"))
    for route in PAGES:
        p.click("#view-chat:not([hidden]) [data-action=drawer], #view-page:not([hidden]) [data-action=drawer]")
        p.wait("document.querySelector('#app').classList.contains('sb-open')")
        p.click(f".sb-item[data-route={route}]")
        ok = page_ok(p, route) and p.wait("!document.querySelector('#app').classList.contains('sb-open')")
        fit = p.js(NO_OVERFLOW)
        check(f"narrow: page {route} fits 390 px", ok and fit["ok"], fit)
        p.shot(out / f"3{PAGES.index(route) + 2}-mobile-{route}-light.png")
        readable(p, f"narrow: page {route}")
    p.click("#view-page [data-action=drawer]")
    p.wait("document.querySelector('#app').classList.contains('sb-open')")
    p.click("#sb-user")
    check("narrow: Settings is full-screen", p.wait(
        "(() => { const d = document.querySelector('dialog.dlg-settings[open]'); return !!d && d.getBoundingClientRect().width === innerWidth; })()"))
    check("narrow: the settings tabs sit in a row above the panel", p.js(
        "(() => { const t = document.querySelector('.set-tabs'), b = document.querySelector('.set-panels');"
        " return getComputedStyle(t).flexDirection === 'row' && t.getBoundingClientRect().bottom <= b.getBoundingClientRect().top + 1; })()"))
    p.shot(out / "38-mobile-settings-light.png")
    for tab in ("voice", "data", "general"):
        p.click(f"#set-tab-{tab}")
        fits = p.wait(f"!document.querySelector('#set-panel-{tab}').hidden") and p.js(
            "[...document.querySelectorAll('dialog.dlg-settings *')].every(e => { const r = e.getBoundingClientRect();"
            " return !r.width || r.right <= innerWidth + 1; })")
        check(f"narrow: the {tab} settings fit 390 px", fits)
        if tab == "voice":
            p.shot(out / "38a-mobile-settings-voice-light.png")
    p.escape()
    p.wait("!document.querySelector('dialog.dlg-settings')")
    p.js("localStorage.setItem('theme', 'dark'); document.documentElement.dataset.theme = 'dark'")
    p.js("location.hash = '#chat'")
    p.wait(visible("#view-chat"))
    p.click(f".sb-chat[data-id='{current_id}'] .sb-chat-open")
    p.wait("document.querySelectorAll('#messages .msg').length >= 4")
    fit = p.js(NO_OVERFLOW)
    check("narrow: a chat with messages fits 390 px", fit["ok"], fit)
    p.shot(out / "39-mobile-chat-dark.png")
    p.js("location.hash = '#vocabulary'")
    page_ok(p, "vocabulary")
    p.shot(out / "40-mobile-vocabulary-dark.png")
    p.js("localStorage.setItem('theme', 'system')")
    no_new_errors("narrow screens")

    # ------------------------------------------------------------------ chat: switching topic, Rename, cards, 390 px
    check_chat_more(p, out, ids, current_id)
    no_new_errors("chat, more")

    # ------------------------------------------------------------------ toasts, shortcuts, sidebar, pill, sounds, tab order
    check_polish(p, out, ids, current_id)
    no_new_errors("polish")

    errs = p.errors()
    check("no JavaScript errors or unhandled rejections in the whole run", not errs, errs)


def main(out: str) -> int:
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    tmp = Path(tempfile.mkdtemp(prefix="tutor-ui-"))
    ollama = QuietServer(("127.0.0.1", free_port()), FakeOllama)
    threading.Thread(target=ollama.serve_forever, daemon=True).start()
    ids = seed(tmp / "data")
    env = {**os.environ, "TUTOR_PORT": str(PORT), "TUTOR_DATA_DIR": str(tmp / "data"),
           "TUTOR_MATERIALS_DIR": str(tmp / "materials"), "TUTOR_LOGS_DIR": str(tmp / "logs"),
           "TUTOR_OLLAMA_URL": f"http://127.0.0.1:{ollama.server_address[1]}",
           "TUTOR_ARABIC_URL": f"http://127.0.0.1:{free_port()}", "PYTHONIOENCODING": "utf-8"}
    procs = []
    try:
        procs.append(subprocess.Popen([sys.executable, "-m", "tutor"], cwd=ROOT, env=env, stdout=subprocess.DEVNULL,
                                      stderr=open(tmp / "server.err.log", "w", encoding="utf-8")))
        for _ in range(120):
            try:
                httpx.get(f"{BASE}/api/profile", timeout=2)
                break
            except httpx.HTTPError:
                time.sleep(0.25)
        else:
            raise RuntimeError("the test server did not start; see " + str(tmp / "server.err.log"))
        cdp = free_port()
        procs.append(subprocess.Popen([CHROME, "--headless=new", f"--remote-debugging-port={cdp}", "--no-first-run",
                                       "--no-default-browser-check", "--autoplay-policy=no-user-gesture-required",
                                       f"--window-size={WIDE[0]},{WIDE[1]}", f"--user-data-dir={tmp / 'chrome'}",
                                       "about:blank"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
        target = None
        for _ in range(80):
            try:
                target = next(t for t in httpx.get(f"http://127.0.0.1:{cdp}/json/list").json() if t["type"] == "page")
                break
            except (httpx.HTTPError, StopIteration):
                time.sleep(0.25)
        p = Page(target["webSocketDebuggerUrl"])
        p.call("Page.enable")
        p.call("Runtime.enable")
        p.call("Log.enable")
        p.call("Page.addScriptToEvaluateOnNewDocument", source=STUBS)
        try:
            run_checks(p, out, ids)
        except Exception as e:   # a step that could not run at all is a failure, not a crash without a report
            check(f"the run completed ({type(e).__name__})", False, e)
            try:
                p.shot(out / "99-failure.png")
            except Exception:
                pass
    finally:
        for pr in reversed(procs):
            pr.terminate()
        for pr in procs:
            try:
                pr.wait(timeout=20)
            except subprocess.TimeoutExpired:
                pr.kill()
        ollama.shutdown()
        err_log = (tmp / "server.err.log").read_text(encoding="utf-8", errors="replace") if (tmp / "server.err.log").exists() else ""
        shutil.rmtree(tmp, ignore_errors=True)
    if "Traceback" in err_log:
        check("the server logged no tracebacks", False, err_log[-2000:])
    failed = [name for name, ok, _ in results if not ok]
    print(f"\n{len(results) - len(failed)} passed, {len(failed)} failed. Screenshots in {out}")
    for name in failed:
        print("  FAILED:", name)
    return 1 if failed else 0


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print(__doc__)
        sys.exit(2)
    sys.exit(main(sys.argv[1]))
