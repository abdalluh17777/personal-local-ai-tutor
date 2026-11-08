"""Live voice conversation over a WebSocket: continuous listening, automatic turn-taking and barge-in.

Audio in:  the page streams 16 kHz mono PCM16 in frames of 512 samples (32 ms).
Audio out: each spoken sentence is one binary message: turn id (uint32), sample rate (uint32), PCM16 samples.
Everything else is a JSON text message.

Turn-taking: Silero VAD marks speech frame by frame. After a short silence the utterance is transcribed
straight away (while the learner is still silent), and the transcript decides how long to keep waiting:
a sentence that sounds finished ends the turn, one that trails off ("and...", "because...") gets more time.
Speaking while the tutor talks interrupts it.

Control messages from the page: {"type": "playback", "playing": bool}, {"type": "mute", "muted": bool}, and
{"type": "text", "text": str}: a message typed in voice mode, answered (and spoken) like a finished utterance.
"""

import asyncio
import json
import logging
import os
import re
import struct
import time
from collections import deque
from dataclasses import dataclass

import numpy as np
from starlette.websockets import WebSocket, WebSocketDisconnect

from . import config
from .llm import ModelError
from .store import Store, normalize
from .tutor import Tutor
from .voice import Voice, VoiceError, clean_markup

log = logging.getLogger("tutor.live")

FRAME = 512          # samples per frame at 16 kHz
FRAME_MS = 32

# (pause before checking whether the learner has finished, pause after which the turn ends anyway).
# The first value can stay short because unfinished-sounding sentences automatically get the second.
PATIENCE_MS = {"short": (450, 1200), "normal": (600, 1800), "long": (900, 2600)}

CONTINUATION_WORDS = {
    "and", "but", "or", "so", "because", "if", "that", "which", "who", "when", "while", "than",
    "the", "a", "an", "to", "of", "in", "on", "at", "with", "for", "from", "about", "my", "your", "their",
    "is", "are", "was", "were", "um", "umm", "uh", "uhm", "er", "erm", "hmm", "like",
    "لأن", "لكن", "يعني", "بس",
    "إذا", "اللي", "في", "على", "من",
}
CONTINUATION_ENDINGS = ("i mean", "you know", "kind of", "sort of", "such as")


def looks_complete(text: str) -> bool:
    """Does a transcript sound like a finished turn? Learners often pause mid-sentence to think."""
    t = text.strip()
    if not t or t.endswith(("...", "…", ",", "-")):
        return False
    if t.endswith(("?", "!", "؟")):
        # Whisper only writes these for finished questions and exclamations ("...what X is?")
        return True
    core = t.rstrip(" .!?؟").lower()
    words = core.split()
    if not words:
        return False
    return words[-1].strip("'\"") not in CONTINUATION_WORDS and not core.endswith(CONTINUATION_ENDINGS)


def speakable(sentence: str) -> bool:
    """Has English or Arabic words to say (Arabic parts go to the Arabic voice)."""
    return bool(re.search(r"[A-Za-z؀-ۿ]", clean_markup(sentence)))


class SentenceSplitter:
    """Cuts streamed reply text into sentences, so speech starts before the reply is complete."""

    _END = re.compile(r"[.!?…][\"')\]]*\s+|\n\s*")
    _ABBREVIATION = re.compile(r"\b(?:Dr|Mr|Mrs|vs|e\.g|i\.e)\.$", re.I)   # "Dr. Pavlov" is not two sentences

    def __init__(self):
        self.buf = ""
        self.first = True

    def push(self, text: str) -> list[str]:
        self.buf += text
        out = []
        while True:
            # Latency: the first piece may be a short opener ("Good question!") or end at a comma
            min_len = 12 if self.first else 24
            cut = next((m.end() for m in self._END.finditer(self.buf)
                        if (m.end() >= min_len or "\n" in m.group())
                        and ("\n" in m.group() or not self._ABBREVIATION.search(self.buf, 0, m.start() + 1))), -1)
            if cut < 0 and self.first:
                comma = self.buf.find(", ", 25)
                cut = comma + 2 if comma >= 0 else -1
            if cut < 0:
                break
            sentence, self.buf = self.buf[:cut], self.buf[cut:]
            if speakable(sentence):
                out.append(sentence)
                self.first = False
        return out

    def flush(self) -> list[str]:
        sentence, self.buf = self.buf, ""
        return [sentence] if speakable(sentence) else []


class StreamingVAD:
    """Silero VAD (the model bundled with faster-whisper), one 32 ms frame at a time. ~0.1 ms per frame on the CPU."""

    CONTEXT = 64

    def __init__(self):
        import onnxruntime as ort
        from faster_whisper.utils import get_assets_path

        opts = ort.SessionOptions()
        opts.inter_op_num_threads = opts.intra_op_num_threads = 1
        opts.log_severity_level = 3
        self.session = ort.InferenceSession(os.path.join(get_assets_path(), "silero_vad_v6.onnx"),
                                            sess_options=opts, providers=["CPUExecutionProvider"])
        self.h = np.zeros((1, 1, 128), np.float32)
        self.c = np.zeros((1, 1, 128), np.float32)
        self.context = np.zeros(self.CONTEXT, np.float32)

    def __call__(self, frame: np.ndarray) -> float:
        x = np.concatenate([self.context, frame])[None, :]
        out, self.h, self.c = self.session.run(None, {"input": x, "h": self.h, "c": self.c})
        self.context = frame[-self.CONTEXT:]
        return float(np.asarray(out).reshape(-1)[0])


@dataclass
class TurnSettings:
    start_prob: float = 0.5
    end_prob: float = 0.35
    start_frames: int = 3          # ~100 ms of speech starts the learner's turn
    barge_in_prob: float = 0.7
    barge_in_frames: int = 8       # ~250 ms of clear speech interrupts the tutor (resists its own echo)
    pause_ms: int = 600
    max_pause_ms: int = 1800
    early_ms: int = 300            # transcription starts this early, so it is ready when the pause is long enough
    preroll_frames: int = 10       # ~320 ms kept from before speech was detected, so no syllable is lost
    max_turn_s: float = 60.0


class LiveSession:
    def __init__(self, ws: WebSocket, store: Store, tutor: Tutor, voice: Voice, session_id: int, vad=None):
        self.ws, self.store, self.tutor, self.voice = ws, store, tutor, voice
        self.session_id = session_id
        profile = store.get_profile()
        pause, max_pause = PATIENCE_MS.get(profile["turn_patience"], PATIENCE_MS["normal"])
        # Measured: transcription takes ~0.4 s, so starting it 0.3 s before the decision point hides most of it
        self.cfg = TurnSettings(pause_ms=pause, max_pause_ms=max_pause, early_ms=max(250, pause - 300))
        self.voice_name = profile["voice"]
        self.speed = float(profile["speech_speed"])
        session = store.get_session(session_id)
        self.hint = ". ".join(x for x in (session["course_name"], session["topic_title"]) if x)
        self.vad = vad or StreamingVAD()

        self.state = ""
        self.muted = False
        self.in_speech = False
        self.speech_run = 0
        self.silence_ms = 0
        self.epoch = 0                 # bumps whenever speech resumes, invalidating a pending transcript
        self.preroll: deque = deque(maxlen=self.cfg.preroll_frames)
        self.utterance: list[np.ndarray] = []
        self.candidate: tuple[int, str] | None = None
        self.candidate_task: asyncio.Task | None = None
        self.candidate_ms = 0.0

        self.turn_id = 0
        self.turn_task: asyncio.Task | None = None
        self.client_playing = False
        self.playback_ended_at = 0.0
        self.pending_prefix = ""       # words of a turn the learner kept talking over before any reply
        self.last_reply = ""
        self.background: set[asyncio.Task] = set()
        self.closed = False

    # --- plumbing -----------------------------------------------------------
    async def send(self, message: dict) -> None:
        if self.closed:
            return
        try:
            await self.ws.send_text(json.dumps(message, ensure_ascii=False))
        except (WebSocketDisconnect, RuntimeError):
            self.closed = True

    async def set_state(self, state: str) -> None:
        if state != self.state:
            self.state = state
            await self.send({"type": "state", "state": state})

    @property
    def tutor_busy(self) -> bool:
        return (self.turn_task is not None and not self.turn_task.done()) or self.client_playing

    def idle_state(self) -> str:
        """The state to show when the learner is not speaking: muted wins over everything else."""
        if self.muted:
            return "muted"
        return "speaking" if self.client_playing else "listening"

    async def run(self) -> None:
        await self.send({"type": "ready", "patience_ms": [self.cfg.pause_ms, self.cfg.max_pause_ms]})
        await self.set_state("listening")
        try:
            while True:
                msg = await self.ws.receive()
                if msg["type"] == "websocket.disconnect":
                    break
                if msg.get("bytes") is not None:
                    await self.on_audio(msg["bytes"])
                elif msg.get("text"):
                    await self.on_control(json.loads(msg["text"]))
        except WebSocketDisconnect:
            pass
        finally:
            self.closed = True
            for task in (self.turn_task, self.candidate_task):
                if task and not task.done():
                    task.cancel()
            # Background English checks keep running: they only write to the database

    async def on_control(self, msg: dict) -> None:
        if msg.get("type") == "playback":
            self.client_playing = bool(msg.get("playing"))
            if not self.client_playing:
                self.playback_ended_at = time.perf_counter()
                if not self.in_speech and not (self.turn_task and not self.turn_task.done()):
                    await self.set_state(self.idle_state())
        elif msg.get("type") == "mute":
            self.muted = bool(msg.get("muted"))
            if self.muted:
                self.reset_speech()
            await self.set_state(self.idle_state())
        elif msg.get("type") == "text":
            await self.on_text(str(msg.get("text") or ""))

    async def on_text(self, text: str) -> None:
        """A message typed in voice mode: a finished utterance that needed no listening (muted or not)."""
        text = text.strip()
        if not text:
            return
        if self.tutor_busy:
            await self.interrupt()
        # Words being spoken right now give way to the typed message: they would otherwise end as a second turn
        # running alongside this one
        self.reset_speech()
        if self.pending_prefix:   # words of a turn cut off before any reply, as for a spoken turn
            text = f"{self.pending_prefix} {text}"
            self.pending_prefix = ""
        self.turn_id += 1
        self.turn_task = asyncio.create_task(self.run_turn(self.turn_id, text, time.perf_counter(), typed=True))

    # --- listening ----------------------------------------------------------
    def reset_speech(self) -> None:
        self.in_speech = False
        self.speech_run = 0
        self.silence_ms = 0
        self.utterance = []
        self.candidate = None
        self.preroll.clear()

    async def on_audio(self, data: bytes) -> None:
        if self.muted or len(data) != FRAME * 2:
            return
        frame = np.frombuffer(data, dtype="<i2").astype(np.float32) / 32768.0
        prob = self.vad(frame)

        if not self.in_speech:
            self.preroll.append(frame)
            busy = self.tutor_busy
            need_prob = self.cfg.barge_in_prob if busy else self.cfg.start_prob
            need_frames = self.cfg.barge_in_frames if busy else self.cfg.start_frames
            self.speech_run = self.speech_run + 1 if prob >= need_prob else 0
            if self.speech_run >= need_frames:
                await self.start_speech(busy)
            return

        self.utterance.append(frame)
        if prob >= self.cfg.end_prob:
            if self.silence_ms:
                self.silence_ms = 0
                self.epoch += 1          # the learner went on talking: a pending transcript is stale
                self.candidate = None
        else:
            self.silence_ms += FRAME_MS
        await self.check_end()

    async def start_speech(self, interrupting: bool) -> None:
        if interrupting:
            await self.interrupt()
        self.in_speech = True
        self.utterance = list(self.preroll)
        self.preroll.clear()
        self.silence_ms = 0
        self.speech_run = 0
        self.candidate = None
        self.epoch += 1
        await self.set_state("user_speaking")

    async def check_end(self) -> None:
        if not self.in_speech:   # the turn already ended (this can be reached from two tasks)
            return
        cfg = self.cfg
        if len(self.utterance) * FRAME_MS / 1000 >= cfg.max_turn_s:
            await self.end_turn()
            return
        if self.silence_ms >= cfg.early_ms and self.candidate is None and self.candidate_task is None:
            audio = np.concatenate(self.utterance)
            self.candidate_task = asyncio.create_task(self.transcribe_candidate(audio, self.epoch))
        if self.candidate is not None:
            epoch, text = self.candidate
            if epoch != self.epoch:
                self.candidate = None
            elif self.silence_ms < cfg.pause_ms:
                return   # transcript is ready early, but this is still a normal pause between words
            elif not text:
                if self.silence_ms >= cfg.pause_ms:   # noise, a cough, a breath: not a turn
                    self.reset_speech()
                    await self.set_state(self.idle_state())
            elif looks_complete(text) or self.silence_ms >= cfg.max_pause_ms:
                await self.end_turn()
        elif self.silence_ms >= cfg.max_pause_ms + 1500 and self.candidate_task is None:
            await self.end_turn()

    async def transcribe_candidate(self, audio: np.ndarray, epoch: int) -> None:
        t0 = time.perf_counter()
        try:
            result = await asyncio.to_thread(self.voice.stt.transcribe_array, audio, self.hint)
            text = result["text"].strip()
        except VoiceError as e:
            await self.send({"type": "error", "message": str(e)})
            text = ""
        finally:
            self.candidate_task = None
        self.candidate_ms = (time.perf_counter() - t0) * 1000
        if epoch == self.epoch and self.in_speech:
            self.candidate = (epoch, text)
            await self.check_end()   # decide now rather than on the next audio frame

    def is_echo(self, text: str) -> bool:
        """The tutor's own voice picked up by the microphone (speakers without headphones)."""
        recent = self.client_playing or time.perf_counter() - self.playback_ended_at < 3
        words = normalize(text).split()
        reply = set(normalize(self.last_reply).split())
        return recent and len(words) >= 3 and sum(w in reply for w in words) / len(words) >= 0.8

    async def end_turn(self) -> None:
        speech_end = time.perf_counter() - self.silence_ms / 1000
        candidate = self.candidate
        audio = np.concatenate(self.utterance) if self.utterance else np.zeros(0, np.float32)
        epoch = self.epoch
        self.reset_speech()
        if candidate and candidate[0] == epoch:
            text = candidate[1]
        else:
            try:
                text = (await asyncio.to_thread(self.voice.stt.transcribe_array, audio, self.hint))["text"].strip()
            except VoiceError as e:
                await self.send({"type": "error", "message": str(e)})
                text = ""
        if not text or self.is_echo(text):
            await self.set_state(self.idle_state())
            return
        if self.pending_prefix:
            text = f"{self.pending_prefix} {text}"
            self.pending_prefix = ""
        self.turn_id += 1
        self.turn_task = asyncio.create_task(self.run_turn(self.turn_id, text, speech_end))

    # --- answering ----------------------------------------------------------
    async def interrupt(self) -> None:
        if self.turn_task and not self.turn_task.done():
            self.turn_task.cancel()
            try:
                await self.turn_task
            except asyncio.CancelledError:
                pass
        self.client_playing = False
        await self.send({"type": "interrupt", "turn": self.turn_id})

    async def run_turn(self, turn: int, text: str, speech_end: float, typed: bool = False) -> None:
        """typed: a message typed in voice mode, timed from when it arrived (no end of speech to find, nothing to
        transcribe)."""
        t_commit = time.perf_counter()
        metrics = {"turn": turn, "endpoint_ms": 0 if typed else round((t_commit - speech_end) * 1000),
                   "stt_ms": 0 if typed else round(self.candidate_ms)}
        await self.send({"type": "user_final", "turn": turn, "text": text})
        await self.set_state("thinking")

        sentences: asyncio.Queue = asyncio.Queue()
        tts_task = asyncio.create_task(self.speak_sentences(turn, sentences, metrics, speech_end))
        splitter = SentenceSplitter()
        reply: list[str] = []
        gen = self.tutor.chat(self.session_id, text, voice=True)
        handed_off = cancelled = False
        try:
            async for event in gen:
                if event["type"] == "token":
                    if not reply:
                        metrics["first_token_ms"] = round((time.perf_counter() - speech_end) * 1000)
                    reply.append(event["text"])
                    self.last_reply = "".join(reply)   # what may come back as echo while it is spoken
                    for s in splitter.push(event["text"]):
                        sentences.put_nowait(s)
                elif event["type"] == "reply_done":
                    for s in splitter.flush():
                        sentences.put_nowait(s)
                    sentences.put_nowait(None)
                    await self.send({**event, "turn": turn})
                    # The English check and memory update continue in the background, so a new turn never cancels them
                    task = asyncio.create_task(self.finish_in_background(gen, turn))
                    self.background.add(task)
                    task.add_done_callback(self.background.discard)
                    handed_off = True
                    break
                await self.send({**event, "turn": turn})
            await tts_task
        except asyncio.CancelledError:
            cancelled = True
            tts_task.cancel()
            if not reply:
                self.pending_prefix = text   # interrupted before answering: the learner had not finished
            raise
        except Exception as e:   # the model failed, or a bug: say so, and the conversation goes on
            tts_task.cancel()
            if not isinstance(e, (ModelError, ValueError)):
                log.exception("live turn failed")
            await self.send({"type": "error", "message": str(e) or type(e).__name__})
        finally:
            try:
                if not handed_off:
                    await gen.aclose()   # keeps a partial reply, or drops an unanswered question
            finally:
                # Interrupted: the learner is speaking, and start_speech() sets the state
                if not cancelled:
                    await self.send({"type": "metrics", **metrics})
                    self.log_metrics(metrics)
                    if not self.in_speech:
                        await self.set_state(self.idle_state())

    async def speak_sentences(self, turn: int, sentences: asyncio.Queue, metrics: dict, speech_end: float) -> None:
        reported = False
        while (sentence := await sentences.get()) is not None:
            try:
                samples, rate = await asyncio.to_thread(self.voice.tts.synthesize, sentence, self.voice_name, self.speed)
            except VoiceError as e:
                # The reply goes on (it is also shown as text); the learner is told once per turn, not per sentence
                if not reported:
                    reported = True
                    await self.send({"type": "error", "message": str(e)})
                continue
            if "first_audio_ms" not in metrics:
                metrics["first_audio_ms"] = round((time.perf_counter() - speech_end) * 1000)
                self.client_playing = True     # the page starts playing as soon as audio arrives
                await self.set_state("speaking")
            pcm = (np.clip(samples, -1.0, 1.0) * 32767).astype("<i2").tobytes()
            if self.closed:
                return
            try:
                await self.ws.send_bytes(struct.pack("<II", turn, rate) + pcm)
            except (WebSocketDisconnect, RuntimeError):
                self.closed = True
                return

    async def finish_in_background(self, gen, turn: int) -> None:
        try:
            async for event in gen:
                await self.send({**event, "turn": turn})
        except Exception:   # the English check is best-effort; the conversation must go on
            log.exception("background turn processing failed")

    def log_metrics(self, metrics: dict) -> None:
        try:
            config.LOGS_DIR.mkdir(parents=True, exist_ok=True)
            with open(config.LOGS_DIR / "live-metrics.jsonl", "a", encoding="utf-8") as f:
                f.write(json.dumps({"at": time.strftime("%Y-%m-%d %H:%M:%S"), **metrics}) + "\n")
        except OSError:
            pass
