"""Runs the app's real observer (the step that extracts corrections, vocabulary and memories)
against the real model, on messages with known answers. Uses a throwaway database.
Run with:  uv run python tests/observer_check.py [--compare-thinking]   (Ollama must be running)
"""

import asyncio
import json
import sys
import tempfile
import time
from pathlib import Path

from tutor import config
from tutor import tutor as tutor_module
from tutor.llm import LLM
from tutor.store import Store
from tutor.tutor import Tutor

CASES = [
    {
        "message": "Today I finished lecture 3 of Yale's Introduction to Psychology, it was about the brain. "
                   "I learned the word 'plasticity', it means the brain can change. Also please give me examples "
                   "from football, I love it. After the lecture I goes to the gym.",
        "reply": "Great work! Plasticity means the brain can change its connections with experience.",
        "expect": "correction 'I goes', vocabulary 'plasticity', memory about football",
    },
    {
        "message": "I am agree with the theory of Piaget.",
        "reply": "Piaget described stages of cognitive development.",
        "expect": "one correction: 'am agree' -> 'agree'. Nothing about 'Jean Piaget'",
    },
    {
        "message": "She don't like the cognitive psychology because it is very difficult for understand.",
        "reply": "Cognitive psychology studies mental processes like memory and attention.",
        "expect": "don't -> doesn't; the cognitive psychology -> cognitive psychology; for understand -> to understand",
    },
    {
        "message": "The informations in this lecture was very usefull.",
        "reply": "I'm glad it helped!",
        "expect": "informations -> information; usefull -> useful; clean JSON, no thinking aloud",
    },
    {
        "message": "I've been studying memory models for two weeks, and I find them fascinating.",
        "reply": "Memory models are a great area to study.",
        "expect": "no corrections, no rewrite",
    },
    {
        "message": "ما الفرق بين الإشراط الكلاسيكي والإجرائي؟ I want to understand it good.",
        "reply": "Classical conditioning links two stimuli; operant conditioning links behaviour to its consequences.",
        "expect": "correction 'understand it good' -> 'understand it well'; Arabic ignored",
    },
]


def _short(fb: dict) -> str:
    lines = [f"    fix: {c['wrong']!r} -> {c['right']!r}  [{c['rule']}]" for c in fb.get("corrections", [])]
    if fb.get("better_phrasing"):
        lines.append(f"    better: {fb['better_phrasing']}")
    lines += [f"    word: {w['word']}" for w in fb.get("saved_words", [])]
    lines += [f"    memory: {m['content']}" for m in fb.get("saved_memories", [])]
    if fb.get("error"):
        lines.append(f"    ERROR: {fb['error']}")
    return "\n".join(lines) or "    (nothing)"


async def run_variant(think: bool, tmp: Path) -> list[dict]:
    tutor_module.OBSERVER_THINKS = think
    store = Store(tmp / f"check-{think}.sqlite3")
    llm = LLM(config.OLLAMA_URL, config.MODEL, config.NUM_CTX)
    tutor = Tutor(store, llm)
    session = store.create_session(None, None)
    results = []
    print(f"\n{'=' * 70}\nObserver with thinking {'ON' if think else 'OFF'}\n{'=' * 70}")
    for case in CASES:
        t0 = time.perf_counter()
        fb = await tutor.observe(session, case["message"], case["reply"])
        elapsed = time.perf_counter() - t0
        results.append({**case, "think": think, "seconds": round(elapsed, 1), "feedback": fb})
        print(f"\n> {case['message'][:90]}\n  expected: {case['expect']}  ({elapsed:.1f}s)\n{_short(fb)}")
    await llm.close()
    store.conn.close()
    return results


async def main():
    # Thinking mode is slow (about 9 minutes for all cases), so it is only compared on request
    variants = (False, True) if "--compare-thinking" in sys.argv else (tutor_module.OBSERVER_THINKS,)
    results = []
    with tempfile.TemporaryDirectory(dir=config.ROOT / "cache") as tmp:
        for think in variants:
            results += await run_variant(think, Path(tmp))
    out = config.ROOT / "logs" / f"observer-check-{time.strftime('%Y%m%d-%H%M%S')}.json"
    out.write_text(json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\nSaved: {out}")


if __name__ == "__main__":
    asyncio.run(main())
