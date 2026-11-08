"""End-to-end smoke test of the real app with the real model, on throwaway data.

Starts the server on port 8799 with a temporary data folder, walks through a full study flow over HTTP,
renders the page in headless Chrome, then stops the server and deletes the temporary data.
Run with:  uv run python tests/smoke_app.py   (Ollama must be running)
"""

import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
PORT = 8799
BASE = f"http://127.0.0.1:{PORT}"
TMP = ROOT / "cache" / "smoke"
CHROME = Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe")

MATERIAL = """Chapter 3: Learning

Classical conditioning
Classical conditioning is a type of learning in which a neutral stimulus comes to produce a response after it has been
repeatedly paired with a stimulus that already produces that response. It was first described by the Russian
physiologist Ivan Pavlov, who studied digestion in dogs. Pavlov noticed that the dogs began to salivate before the
food arrived, for example when they heard the footsteps of the assistant who fed them.

In Pavlov's terms, the food is the unconditioned stimulus (US), because it produces salivation without any learning.
Salivation to the food is the unconditioned response (UR). A sound such as a metronome is at first a neutral stimulus.
After the sound has been paired with food many times, the sound alone produces salivation. The sound is now a
conditioned stimulus (CS), and salivation to the sound is a conditioned response (CR).

If the conditioned stimulus is presented again and again without the food, the conditioned response becomes weaker
and finally disappears. This is called extinction. After a pause, the response can return for a short time, which is
called spontaneous recovery. Stimulus generalization happens when stimuli similar to the conditioned stimulus also
produce the response.

Operant conditioning
Operant conditioning is learning from the consequences of behaviour. Edward Thorndike placed cats in puzzle boxes and
observed that behaviours followed by a satisfying result became more likely. He called this the law of effect.
B. F. Skinner extended this work with an operant chamber, often called a Skinner box, in which rats pressed a lever
and pigeons pecked a key to receive food.

Reinforcement makes a behaviour more likely. Positive reinforcement adds something pleasant, such as food or praise.
Negative reinforcement removes something unpleasant, such as turning off a loud noise. Punishment makes a behaviour
less likely. The key difference from classical conditioning is that in operant conditioning the learner's own
behaviour produces the consequence.
"""


def events(client: httpx.Client, session_id: int, text: str) -> list[dict]:
    out = []
    with client.stream("POST", f"/api/sessions/{session_id}/messages", json={"text": text}, timeout=300) as r:
        r.raise_for_status()
        for line in r.iter_lines():
            if line.startswith("data: "):
                out.append(json.loads(line[6:]))
    return out


def show_turn(label: str, evs: list[dict], seconds: float) -> list[str]:
    """Print one turn; return its errors."""
    reply = "".join(e["text"] for e in evs if e["type"] == "token")
    done = next((e for e in evs if e["type"] == "reply_done"), {})
    fb = next((e["feedback"] for e in evs if e["type"] == "feedback"), {})
    errors = [e["message"] for e in evs if e["type"] == "error"]
    sources = done.get("sources", [])
    print(f"\n--- {label} ({seconds:.1f}s total) ---")
    print(reply.strip())
    print("cited:", [s["label"] for s in sources if s["cited"]],
          "| retrieved:", len(sources), "| unknown:", done.get("unknown_citations"))
    if sources and not any(s["cited"] for s in sources):
        print(f"WARNING: {len(sources)} passages were retrieved but the reply cites none of them")
    print("feedback:", json.dumps(fb, ensure_ascii=False))
    if not done:
        errors.append("the reply never finished")
    if errors:
        print("ERRORS:", errors)
    return [f"{label}: {e}" for e in errors]


def main() -> int:
    shutil.rmtree(TMP, ignore_errors=True)
    (TMP / "data").mkdir(parents=True)
    env = {**os.environ, "TUTOR_PORT": str(PORT), "TUTOR_DATA_DIR": str(TMP / "data"),
           "TUTOR_MATERIALS_DIR": str(TMP / "materials"), "PYTHONIOENCODING": "utf-8"}
    server = subprocess.Popen([sys.executable, "-m", "tutor"], cwd=ROOT, env=env,
                              stdout=subprocess.DEVNULL, stderr=open(TMP / "server.err.log", "w"))
    errors: list[str] = []
    checks: dict[str, bool] = {}
    try:
        client = httpx.Client(base_url=BASE, timeout=300)
        for _ in range(40):
            try:
                client.get("/api/profile")
                break
            except httpx.HTTPError:
                time.sleep(0.5)

        client.put("/api/profile", json={"name": "Test Learner", "english_level": "B1",
                                         "interests": "football"}).raise_for_status()
        course = client.post("/api/courses", json={"name": "Intro Psychology"}).json()
        up = client.post(f"/api/courses/{course['id']}/documents",
                         files=[("files", ("chapter3.txt", MATERIAL.encode(), "text/plain"))]).json()
        print("upload:", [(r["filename"], r["ok"], r.get("document", {}).get("passages")) for r in up])
        errors += [f"upload {r['filename']}: {r.get('error')}" for r in up if not r["ok"]]

        t0 = time.perf_counter()
        suggested = client.post(f"/api/courses/{course['id']}/topics/suggest").json()["topics"]
        print(f"suggested topics ({time.perf_counter() - t0:.1f}s):", suggested)
        topic = client.post(f"/api/courses/{course['id']}/topics", json={"title": "Classical conditioning"}).json()
        session = client.post("/api/sessions", json={"course_id": course["id"], "topic_id": topic["id"]}).json()["session"]

        t0 = time.perf_counter()
        evs = events(client, session["id"], "Can you explain me what is a conditioned stimulus? I want examples from football please.")
        errors += show_turn("English question with mistakes", evs, time.perf_counter() - t0)

        t0 = time.perf_counter()
        evs = events(client, session["id"], "ما الفرق بين الانطفاء والاسترجاع التلقائي؟")
        errors += show_turn("Arabic question", evs, time.perf_counter() - t0)

        t0 = time.perf_counter()
        ended = client.post(f"/api/sessions/{session['id']}/end").json()
        print(f"\n--- end of session ({time.perf_counter() - t0:.1f}s) ---")
        print("summary:", ended["session"]["summary"])
        print("next step:", ended["session"]["next_step"])
        print("new memories:", [m["content"] for m in ended["new_memories"]])
        if ended.get("error"):
            errors.append(f"end of session: {ended['error']}")

        print("\nmemories:", [m["content"] for m in client.get("/api/memories").json()])
        print("vocab:", [v["word"] for v in client.get("/api/vocab").json()])
        print("mistakes:", [(m["rule"], m["count"]) for m in client.get("/api/mistakes").json()])
        plan = client.get("/api/plan").json()
        print("plan: next topics", [t["title"] for t in plan["next_topics"]], "| due vocab", plan["due_vocab"])
        status = client.get("/api/status").json()
        print("model status:", status)

        if CHROME.exists():
            dom = subprocess.run(
                [str(CHROME), "--headless=new", "--disable-gpu", f"--user-data-dir={TMP / 'chrome'}",
                 "--virtual-time-budget=8000", "--dump-dom", BASE + "/#home"],
                capture_output=True, text=True, encoding="utf-8", timeout=90).stdout
            # The greeting is by time of day ("Good evening, Test Learner"); the last chat is the Continue card
            checks = {"Home page rendered by the app's scripts": ", Test Learner" in dom,
                      "plan shows last session": "Continue with the next step" in dom or "View that chat" in dom,
                      "model status shown": "On GPU 100%" in dom or "Model ready" in dom}
            print("\nheadless page check:", checks)
        else:
            print("\nheadless page check skipped: Chrome not found")
        failed = [name for name, ok in checks.items() if not ok] + errors
        print("\nSMOKE TEST", "FAILED: " + "; ".join(failed) if failed else "PASSED")
        return 0 if all(checks.values()) and not errors else 1
    finally:
        server.terminate()
        server.wait(timeout=10)
        time.sleep(1)
        shutil.rmtree(TMP, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
