"""Measures live conversation on the real stack (Whisper, Qwen, Kokoro) with a simulated learner.

The learner's voice is Kokoro's "am_michael", streamed to the server in real time (one 32 ms frame every
32 ms), exactly like the page's microphone. Runs a throwaway server on port 8799 with temporary data.
Stop the main tutor server first: two copies of the voice models would not fit in VRAM together.
Run with:  uv run python tests/live_check.py
"""

import asyncio
import io
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import httpx
import numpy as np
import websockets
from faster_whisper import decode_audio

ROOT = Path(__file__).resolve().parent.parent
PORT = 8799
BASE = f"http://127.0.0.1:{PORT}"
TMP = ROOT / "cache" / "live-check"
FRAME = 512


def speech(client: httpx.Client, text: str) -> np.ndarray:
    wav = client.post("/api/voice/speak", json={"text": text, "voice": "am_michael", "speed": 1.0}).content
    return (decode_audio(io.BytesIO(wav), sampling_rate=16000) * 32767).astype("<i2")


def silence(seconds: float) -> np.ndarray:
    return np.zeros(int(16000 * seconds), dtype="<i2")


class Learner:
    """Streams audio like a microphone, plays nothing, and reports playback timing like the page does."""

    def __init__(self, ws):
        self.ws = ws
        self.events: list[tuple[float, dict]] = []
        self.audio_chunks: list[float] = []
        self.playing_until = 0.0
        self.playing = False

    async def stream(self, audio: np.ndarray) -> float:
        """Send audio in real time; returns the time the last frame was sent."""
        start = time.perf_counter()
        for i, offset in enumerate(range(0, len(audio) - FRAME + 1, FRAME)):
            await self.ws.send(audio[offset:offset + FRAME].tobytes())
            delay = start + (i + 1) * 0.032 - time.perf_counter()
            if delay > 0:
                await asyncio.sleep(delay)
        return time.perf_counter()

    async def receive(self) -> None:
        async for msg in self.ws:
            now = time.perf_counter()
            if isinstance(msg, bytes):
                rate = int.from_bytes(msg[4:8], "little")
                duration = (len(msg) - 8) / 2 / rate
                self.audio_chunks.append(now)
                self.playing_until = max(self.playing_until, now) + duration
                if not self.playing:
                    self.playing = True
                    await self.ws.send(json.dumps({"type": "playback", "playing": True}))
            else:
                self.events.append((now, json.loads(msg)))

    async def playback_clock(self) -> None:
        while True:
            await asyncio.sleep(0.05)
            if self.playing and time.perf_counter() >= self.playing_until:
                self.playing = False
                await self.ws.send(json.dumps({"type": "playback", "playing": False}))

    def first(self, kind: str, after: float) -> tuple[float, dict] | None:
        return next(((t, e) for t, e in self.events if e["type"] == kind and t >= after), None)


async def wait_for(learner: Learner, kind: str, after: float, timeout: float = 30) -> tuple[float, dict]:
    end = time.perf_counter() + timeout
    while time.perf_counter() < end:
        found = learner.first(kind, after)
        if found:
            return found
        await asyncio.sleep(0.02)
    raise TimeoutError(f"no '{kind}' event")


async def keep_quiet(learner: Learner, seconds: float) -> None:
    await learner.stream(silence(seconds))


async def scenario(session_id: int, clips: dict) -> dict:
    results = {}
    async with websockets.connect(f"ws://127.0.0.1:{PORT}/api/live?session_id={session_id}",
                                  origin=f"http://127.0.0.1:{PORT}", max_size=None) as ws:
        learner = Learner(ws)
        tasks = [asyncio.create_task(learner.receive()), asyncio.create_task(learner.playback_clock())]
        await learner.stream(silence(0.5))

        # 1. A normal question
        spoke_at = await learner.stream(clips["question"])
        quiet = asyncio.create_task(keep_quiet(learner, 6))
        t_user, user = await wait_for(learner, "user_final", spoke_at)
        t_metrics, metrics = await wait_for(learner, "metrics", spoke_at)
        first_audio = next(t for t in learner.audio_chunks if t >= spoke_at)
        results["question"] = {"heard": user["text"], "server_metrics": metrics,
                               "first_audio_after_speech_s": round(first_audio - spoke_at, 2)}
        await quiet
        while learner.playing:   # let the tutor finish speaking
            await learner.stream(silence(0.5))

        # 2. A sentence with a thinking pause in the middle: must arrive as one turn
        start = time.perf_counter()
        await learner.stream(clips["part1"])
        await learner.stream(silence(0.9))
        spoke_at = await learner.stream(clips["part2"])
        quiet = asyncio.create_task(keep_quiet(learner, 6))
        _, user = await wait_for(learner, "user_final", start)
        results["pause_mid_sentence"] = {"heard": user["text"],
                                         "one_turn": len([e for t, e in learner.events
                                                          if e["type"] == "user_final" and t >= start]) == 1}
        await quiet

        # 3. Interrupting the tutor while it speaks
        while not learner.playing:
            await learner.stream(silence(0.2))
        await learner.stream(silence(0.6))
        barge_at = time.perf_counter()
        await learner.stream(clips["interrupt"])
        t_int, _ = await wait_for(learner, "interrupt", barge_at)
        results["interrupt"] = {"stopped_tutor_after_s": round(t_int - barge_at, 2)}
        await learner.stream(silence(2.5))
        _, user = await wait_for(learner, "user_final", barge_at)
        results["interrupt"]["heard"] = user["text"]

        for task in tasks:
            task.cancel()
    return results


def main() -> int:
    shutil.rmtree(TMP, ignore_errors=True)
    (TMP / "data").mkdir(parents=True)
    env = {**os.environ, "TUTOR_PORT": str(PORT), "TUTOR_DATA_DIR": str(TMP / "data"),
           "TUTOR_MATERIALS_DIR": str(TMP / "materials")}
    server = subprocess.Popen([sys.executable, "-m", "tutor"], cwd=ROOT, env=env,
                              stdout=subprocess.DEVNULL, stderr=open(TMP / "server.err.log", "w"))
    try:
        client = httpx.Client(base_url=BASE, timeout=300)
        for _ in range(60):
            try:
                client.get("/api/profile")
                break
            except httpx.HTTPError:
                time.sleep(0.5)
        client.put("/api/profile", json={"name": "Test", "english_level": "B1"})
        print("warming up voice:", client.post("/api/voice/warmup").json())
        course = client.post("/api/courses", json={"name": "Intro Psychology"}).json()
        topic = client.post(f"/api/courses/{course['id']}/topics", json={"title": "Classical conditioning"}).json()
        session = client.post("/api/sessions", json={"course_id": course["id"], "topic_id": topic["id"]}).json()["session"]
        # Load the language model before measuring
        with client.stream("POST", f"/api/sessions/{session['id']}/messages", json={"text": "Hi!"}) as r:
            for _ in r.iter_lines():
                pass

        clips = {
            "question": speech(client, "Can you explain what a conditioned stimulus is?"),
            "part1": speech(client, "I think that"),
            "part2": speech(client, "the bell becomes a signal for the food."),
            "interrupt": speech(client, "Sorry, can you give me a football example instead?"),
        }
        results = asyncio.run(scenario(session["id"], clips))
        print(json.dumps(results, indent=2, ensure_ascii=False))
        (ROOT / "logs" / f"live-check-{time.strftime('%Y%m%d-%H%M%S')}.json").write_text(
            json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8")
        return 0
    finally:
        server.terminate()
        server.wait(timeout=15)
        time.sleep(1)
        shutil.rmtree(TMP, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
