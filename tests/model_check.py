"""Checks the tutor model on this machine before building on it.

Covers: GPU placement, generation speed, English error correction, and psychology teaching
(including false premises). Prints results and saves them to logs/. (The app saves memories through
JSON-schema replies, not tool calls; tests/observer_check.py measures those.)
Run with:  uv run python tests/model_check.py   (after scripts/start-ollama.ps1)
"""

import json
import time
from datetime import datetime
from pathlib import Path

import httpx

OLLAMA = "http://127.0.0.1:11434"
MODEL = "tutor"
ROOT = Path(__file__).resolve().parent.parent

client = httpx.Client(base_url=OLLAMA, timeout=600)
results: dict = {"model": MODEL, "started": datetime.now().isoformat(timespec="seconds")}


def chat(messages, think=False):
    body = {"model": MODEL, "messages": messages, "stream": False, "think": think}
    r = client.post("/api/chat", json=body)
    r.raise_for_status()
    return r.json()


def section(title):
    print(f"\n{'=' * 70}\n{title}\n{'=' * 70}")


# --- 1. GPU placement -------------------------------------------------------
section("1. GPU placement")
t0 = time.perf_counter()
chat([{"role": "user", "content": "Say OK."}])
load_s = time.perf_counter() - t0
ps = client.get("/api/ps").json()["models"]
m = next(x for x in ps if x["name"].startswith(MODEL))
gpu_pct = 100 * m["size_vram"] / m["size"]
results["placement"] = {
    "total_gb": round(m["size"] / 1e9, 2),
    "vram_gb": round(m["size_vram"] / 1e9, 2),
    "gpu_percent": round(gpu_pct, 1),
    "context_length": m.get("context_length"),
    "first_load_seconds": round(load_s, 1),
}
print(json.dumps(results["placement"], indent=2))

# --- 2. Speed (streamed, to measure time to first token) --------------------
section("2. Speed")
body = {
    "model": MODEL,
    "stream": True,
    "think": False,
    "messages": [{"role": "user", "content": "In about 250 words, explain how human memory works, "
                  "covering encoding, storage and retrieval."}],
}
t0 = time.perf_counter()
first_token_s = None
final = None
with client.stream("POST", "/api/chat", json=body) as r:
    for line in r.iter_lines():
        if not line:
            continue
        chunk = json.loads(line)
        if first_token_s is None and chunk.get("message", {}).get("content"):
            first_token_s = time.perf_counter() - t0
        if chunk.get("done"):
            final = chunk
results["speed"] = {
    "time_to_first_token_s": round(first_token_s, 2),
    "generation_tokens_per_s": round(final["eval_count"] / (final["eval_duration"] / 1e9), 1),
    "prompt_tokens_per_s": round(final["prompt_eval_count"] / (final["prompt_eval_duration"] / 1e9), 1),
    "generated_tokens": final["eval_count"],
}
print(json.dumps(results["speed"], indent=2))

# --- 3. English correction --------------------------------------------------
section("3. English correction")
CORRECTION_SYSTEM = (
    "You are an English tutor for an Arabic-speaking learner (B1-B2). "
    "The learner's message may contain mistakes. Reply ONLY with a list of corrections, "
    "one per line, as: wrong -> right (short reason). "
    "If the sentence is fully correct, reply exactly: No mistakes. "
    "Never correct things that are already correct."
)
SENTENCES = [
    "Yesterday I go to the university and I study psychology since two years.",
    "She don't like the cognitive psychology because it is very difficult for understand.",
    "I am agree with the theory of Piaget.",
    "Can you explain me what is classical conditioning?",
    "The informations in this lecture was very usefull.",
    # Correct sentence: checks the model does not over-correct
    "I've been studying memory models for two weeks, and I find them fascinating.",
]
results["english"] = []
for s in SENTENCES:
    reply = chat([{"role": "system", "content": CORRECTION_SYSTEM},
                  {"role": "user", "content": s}])["message"]["content"].strip()
    results["english"].append({"input": s, "reply": reply})
    print(f"\n> {s}\n{reply}")

# --- 4. Psychology teaching -------------------------------------------------
section("4. Psychology teaching")
TEACH_SYSTEM = (
    "You are a patient psychology tutor teaching in English to a B1-B2 learner. "
    "Use simple, clear English. If the learner says something factually wrong, correct it kindly."
)
PROMPTS = [
    "Explain cognitive dissonance with an everyday example, then ask me one question to check I understood.",
    # False premise: Pavlov used dogs and salivation; lever pressing is Skinner's operant conditioning
    "In his famous experiment, Pavlov trained rats to press a lever to get food, right?",
    # Contested finding: checks whether the model mentions the criticism
    "The Stanford prison experiment proved that anyone becomes cruel in the right situation, right?",
]
results["psychology"] = []
for p in PROMPTS:
    reply = chat([{"role": "system", "content": TEACH_SYSTEM},
                  {"role": "user", "content": p}])["message"]["content"].strip()
    results["psychology"].append({"input": p, "reply": reply})
    print(f"\n> {p}\n{reply}")

# --- Save -------------------------------------------------------------------
out = ROOT / "logs" / f"model-check-{datetime.now():%Y%m%d-%H%M%S}.json"
out.write_text(json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8")
print(f"\nSaved: {out}")
