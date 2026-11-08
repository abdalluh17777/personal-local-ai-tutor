"""Live conversation: turn-taking decisions, sentence streaming, and the WebSocket flow end to end
(with a scripted VAD, transcripts, model and voice, so no GPU is needed)."""

import json
import queue

import numpy as np
import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from tutor import config
from tutor.live import FRAME, LiveSession, SentenceSplitter, looks_complete
from tutor.server import create_app
from tutor.store import Store
from tutor.voice import VoiceError

from fakes import FakeLLM, FakeVoice, LoudnessVAD

ORIGIN = {"origin": f"http://127.0.0.1:{config.PORT}"}
SPEECH = (np.full(FRAME, 0.5) * 32767).astype("<i2").tobytes()
SILENCE = np.zeros(FRAME, dtype="<i2").tobytes()


def test_unfinished_sentences_get_more_time():
    assert looks_complete("What is classical conditioning?")
    assert looks_complete("Can you explain what a conditioned stimulus is?")   # measured bug: ends in "is"
    assert looks_complete("I went to the gym.")
    assert not looks_complete("I think that.")          # Whisper adds the full stop; the thought is unfinished
    assert not looks_complete("I want to talk about, um")
    assert not looks_complete("It is important because")
    assert not looks_complete("...")
    assert not looks_complete("")


def test_sentences_stream_out_as_soon_as_they_end():
    s = SentenceSplitter()
    out = []
    for token in "Good question! Pavlov used dogs. The bell became a signal for food".split(" "):
        out += s.push(token + " ")
    out += s.flush()
    # A short opener is spoken on its own so the voice starts sooner; later pieces are full sentences
    assert [x.strip() for x in out] == ["Good question!", "Pavlov used dogs. The bell became a signal for food"]


def test_a_long_first_sentence_starts_at_its_first_comma():
    s = SentenceSplitter()
    out = s.push("When a neutral stimulus is paired with food many times, the dog starts to salivate ")
    assert out == ["When a neutral stimulus is paired with food many times, "]


def test_sentences_are_not_cut_after_abbreviations():
    s = SentenceSplitter()
    out = []
    # every abbreviation comes after the 24 characters a later sentence needs, where it would have been cut
    text = ("Good question! Pavlov used hungry dogs, e.g. puppies, i.e. not fed. Then Lee asked both Mr. Smith "
            "and Mrs. Jones about a bell vs. a light, as Dr. Hull did. It worked.")
    for token in text.split(" "):
        out += s.push(token + " ")
    out += s.flush()
    assert [x.strip() for x in out] == [
        "Good question!", "Pavlov used hungry dogs, e.g. puppies, i.e. not fed.",
        "Then Lee asked both Mr. Smith and Mrs. Jones about a bell vs. a light, as Dr. Hull did.", "It worked."]


@pytest.fixture
def live_app(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "LOGS_DIR", tmp_path / "logs")   # never the real logs\live-metrics.jsonl
    store = Store(tmp_path / "tutor.sqlite3")
    course = store.create_course("Intro Psychology")
    topic = store.add_topic(course["id"], "Classical conditioning")
    session = store.create_session(course["id"], topic["id"])
    return store, session


def _connect(store, session, transcripts, llm=None):
    voice = FakeVoice(transcripts=transcripts)
    # Starlette's test client always sends Host "testserver" on WebSockets
    hosts = {f"127.0.0.1:{config.PORT}", "testserver"}
    app = create_app(store, llm or FakeLLM(reply="Great question. Pavlov paired a bell with food."),
                     allowed_hosts=hosts, voice=voice)
    app.state.live_vad_factory = LoudnessVAD
    client = TestClient(app, base_url=f"http://127.0.0.1:{config.PORT}")
    return client, voice


def _receive_until(ws, wanted: str, limit=400):
    events, audio = [], 0
    for _ in range(limit):
        msg = ws.receive()
        if msg.get("bytes"):
            audio += 1
            continue
        event = json.loads(msg["text"])
        events.append(event)
        if event["type"] == wanted:
            return events, audio
    raise AssertionError(f"never received {wanted}: {[e['type'] for e in events]}")


def test_a_spoken_turn_is_detected_answered_and_spoken(live_app, tmp_path):
    store, session = live_app
    client, voice = _connect(store, session, ["What is classical conditioning?"])
    with client, client.websocket_connect(f"/api/live?session_id={session['id']}", headers=ORIGIN) as ws:
        _receive_until(ws, "state")
        for frame in [SILENCE] * 5 + [SPEECH] * 30 + [SILENCE] * 30:   # ~1 s of speech, then ~1 s of silence
            ws.send_bytes(frame)
        events, audio = _receive_until(ws, "metrics")
    kinds = [e["type"] for e in events]
    assert "user_final" in kinds and "reply_done" in kinds and audio >= 1
    assert next(e for e in events if e["type"] == "user_final")["text"] == "What is classical conditioning?"
    states = [e["state"] for e in events if e["type"] == "state"]
    assert states[:2] == ["user_speaking", "thinking"] and "speaking" in states
    assert voice.hints[0] == "Intro Psychology. Classical conditioning"
    roles = [m["role"] for m in store.messages(session["id"])]
    assert roles == ["user", "assistant"]
    logged = (tmp_path / "logs" / "live-metrics.jsonl").read_text(encoding="utf-8").splitlines()
    assert json.loads(logged[-1])["turn"] == 1


def test_a_trailing_off_sentence_waits_and_noise_is_not_a_turn(live_app):
    store, session = live_app
    # First utterance sounds unfinished; the second is empty (a cough)
    client, voice = _connect(store, session, ["I think that", "", "conditioning is learning by association."])
    with client, client.websocket_connect(f"/api/live?session_id={session['id']}", headers=ORIGIN) as ws:
        _receive_until(ws, "state")
        for frame in [SPEECH] * 20 + [SILENCE] * 25:     # 0.8 s pause: transcript is unfinished, keep waiting
            ws.send_bytes(frame)
        for frame in [SILENCE] * 40:                      # past the 1.8 s limit: the turn ends anyway
            ws.send_bytes(frame)
        events, _ = _receive_until(ws, "user_final")
        assert events[-1]["text"] == "I think that"
        _receive_until(ws, "metrics")                     # the tutor answered...
        ws.send_text(json.dumps({"type": "playback", "playing": False}))   # ...and the page finished playing it
        events, _ = _receive_until(ws, "state")
        assert events[-1]["state"] == "listening"
        for frame in [SPEECH] * 20 + [SILENCE] * 25:     # a cough: Whisper hears no words
            ws.send_bytes(frame)
        events, _ = _receive_until(ws, "state")
        assert events[-1]["state"] == "user_speaking"
        events, _ = _receive_until(ws, "state")
    assert events[-1]["state"] == "listening" and "user_final" not in [e["type"] for e in events]
    assert voice.transcripts == ["conditioning is learning by association."]   # the empty transcript was used


def test_the_tutors_own_echo_is_ignored(live_app):
    store, session = live_app
    reply = "Great question. Pavlov paired a bell with food."
    # The second "utterance" is the tutor's own reply, picked up by the microphone from the speakers
    client, voice = _connect(store, session, ["What is classical conditioning?", reply], llm=FakeLLM(reply=reply))
    with client, client.websocket_connect(f"/api/live?session_id={session['id']}", headers=ORIGIN) as ws:
        _receive_until(ws, "state")
        for frame in [SPEECH] * 20 + [SILENCE] * 30:
            ws.send_bytes(frame)
        _receive_until(ws, "metrics")                     # the tutor answered
        ws.send_text(json.dumps({"type": "playback", "playing": True}))    # the page plays the reply...
        for frame in [SPEECH] * 20:                       # ...and the microphone hears it, like speech over the tutor
            ws.send_bytes(frame)
        _receive_until(ws, "interrupt")
        ws.send_text(json.dumps({"type": "playback", "playing": False}))   # the page stops playing, as on any interrupt
        events, _ = _receive_until(ws, "state")
        assert events[-1]["state"] == "user_speaking"
        for frame in [SILENCE] * 30:
            ws.send_bytes(frame)
        events, _ = _receive_until(ws, "state")           # "thinking", after a user_final, if it were taken as a turn
    assert events[-1]["state"] == "listening" and "user_final" not in [e["type"] for e in events]
    assert voice.transcripts == []                       # the echo was transcribed, then dropped
    assert [m["role"] for m in store.messages(session["id"])] == ["user", "assistant"]


def test_talking_over_the_tutor_interrupts_it(live_app):
    store, session = live_app
    slow = FakeLLM(reply=" ".join(["This is a long explanation."] * 30), word_delay=0.01)
    client, _ = _connect(store, session, ["Tell me about Pavlov.", "Wait, stop please."], llm=slow)
    with client, client.websocket_connect(f"/api/live?session_id={session['id']}", headers=ORIGIN) as ws:
        _receive_until(ws, "state")
        for frame in [SPEECH] * 20 + [SILENCE] * 30:
            ws.send_bytes(frame)
        _receive_until(ws, "user_final")
        while not ws.receive().get("bytes"):              # wait until the tutor is speaking
            pass
        for frame in [SPEECH] * 12:                       # ~0.4 s of clear speech over the tutor
            ws.send_bytes(frame)
        events, _ = _receive_until(ws, "interrupt")
    assert events[-1]["type"] == "interrupt"


def test_muted_stays_muted_while_and_after_the_tutor_speaks(live_app, monkeypatch):
    # The state right after each control message is handled, read on the server (a state that does not change
    # sends no event, so the page's events alone cannot show it)
    handled = queue.Queue()
    original = LiveSession.on_control

    async def on_control(self, msg):
        await original(self, msg)
        handled.put(self.state)

    monkeypatch.setattr(LiveSession, "on_control", on_control)
    store, session = live_app
    client, _ = _connect(store, session, ["Tell me about Pavlov."])
    with client, client.websocket_connect(f"/api/live?session_id={session['id']}", headers=ORIGIN) as ws:
        _receive_until(ws, "state")
        for frame in [SPEECH] * 20 + [SILENCE] * 30:
            ws.send_bytes(frame)
        _receive_until(ws, "user_final")
        while not ws.receive().get("bytes"):              # the tutor is speaking
            pass
        states = []
        for msg in ({"type": "mute", "muted": True}, {"type": "mute", "muted": False},
                    {"type": "mute", "muted": True}):
            ws.send_text(json.dumps(msg))
            states.append(handled.get(timeout=5))
        _receive_until(ws, "metrics")                     # the reply ends while muted
        ws.send_text(json.dumps({"type": "playback", "playing": False}))   # and the page finishes playing it
        states.append(handled.get(timeout=5))
    # unmuted while the tutor talks: "speaking", not "listening"; muted at the end, not "listening"
    assert states == ["muted", "speaking", "muted", "muted"]


def test_a_typed_message_is_answered_like_a_spoken_turn_muted_or_not(live_app, tmp_path):
    store, session = live_app
    client, voice = _connect(store, session, [])
    with client, client.websocket_connect(f"/api/live?session_id={session['id']}", headers=ORIGIN) as ws:
        _receive_until(ws, "state")
        ws.send_text(json.dumps({"type": "mute", "muted": True}))
        _receive_until(ws, "state")                       # muted
        ws.send_text(json.dumps({"type": "text", "text": "   "}))                  # nothing typed: ignored
        ws.send_text(json.dumps({"type": "text", "text": "  What is extinction?  "}))
        events, audio = _receive_until(ws, "metrics")
    final = next(e for e in events if e["type"] == "user_final")
    assert final == {"type": "user_final", "turn": 1, "text": "What is extinction?"}   # the empty one was no turn
    assert "reply_done" in [e["type"] for e in events] and audio >= 1          # answered, and spoken
    assert "interrupt" not in [e["type"] for e in events]                     # nothing to interrupt
    metrics = events[-1]
    assert metrics["endpoint_ms"] == 0 and metrics["stt_ms"] == 0 and metrics["first_audio_ms"] >= 0
    assert voice.hints == []                                                  # nothing was transcribed
    assert [(m["role"], m["content"]) for m in store.messages(session["id"])][0] == ("user", "What is extinction?")
    assert len(store.messages(session["id"])) == 2


def test_a_typed_message_while_the_tutor_speaks_interrupts_it_first(live_app):
    store, session = live_app
    slow = FakeLLM(reply=" ".join(["This is a long explanation."] * 30), word_delay=0.01)
    client, _ = _connect(store, session, ["Tell me about Pavlov."], llm=slow)
    with client, client.websocket_connect(f"/api/live?session_id={session['id']}", headers=ORIGIN) as ws:
        _receive_until(ws, "state")
        for frame in [SPEECH] * 20 + [SILENCE] * 30:
            ws.send_bytes(frame)
        _receive_until(ws, "user_final")
        while not ws.receive().get("bytes"):              # wait until the tutor is speaking
            pass
        ws.send_text(json.dumps({"type": "text", "text": "Sorry, what is a reflex?"}))
        events, _ = _receive_until(ws, "user_final")
        kinds = [e["type"] for e in events]
        assert "interrupt" in kinds and kinds.index("interrupt") < len(kinds) - 1   # interrupted first, then the turn
        assert next(e for e in events if e["type"] == "interrupt")["turn"] == 1
        assert events[-1] == {"type": "user_final", "turn": 2, "text": "Sorry, what is a reflex?"}
        events, _ = _receive_until(ws, "reply_done")
    assert {e.get("turn") for e in events if e["type"] == "token"} == {2}
    saved = store.messages(session["id"])
    # the reply that was cut off keeps what was said, the typed question gets the next answer
    assert [m["role"] for m in saved] == ["user", "assistant", "user", "assistant"]
    assert saved[1]["content"].endswith(" …") and saved[2]["content"] == "Sorry, what is a reflex?"


def test_a_typed_message_while_the_tutor_thinks_keeps_the_words_before_it(live_app):
    store, session = live_app
    # Each word takes 0.6 s: the message is typed before the first one, as a spoken barge-in would be
    client, _ = _connect(store, session, ["Tell me about"], llm=FakeLLM(reply="Short answer.", word_delay=0.6))
    with client, client.websocket_connect(f"/api/live?session_id={session['id']}", headers=ORIGIN) as ws:
        _receive_until(ws, "state")
        for frame in [SPEECH] * 20 + [SILENCE] * 70:     # past the longest pause: "Tell me about" ends the turn
            ws.send_bytes(frame)
        _receive_until(ws, "user_final")
        ws.send_text(json.dumps({"type": "text", "text": "Pavlov's dogs."}))
        events, _ = _receive_until(ws, "user_final")
        assert [e["type"] for e in events].count("interrupt") == 1
        assert events[-1]["text"] == "Tell me about Pavlov's dogs."
        _receive_until(ws, "reply_done")
    assert [m["content"] for m in store.messages(session["id"])][0] == "Tell me about Pavlov's dogs."


class _FailsOnce(FakeLLM):
    """The first reply fails with an error nobody planned for (a bug); the next one works."""

    failed = False

    async def stream_chat(self, messages, think=False):
        if not self.failed:
            self.failed = True
            raise RuntimeError("unexpected failure")
        async for piece in super().stream_chat(messages, think):
            yield piece


def test_an_unexpected_error_in_a_turn_is_reported_and_the_conversation_goes_on(live_app):
    store, session = live_app
    client, _ = _connect(store, session, ["What is a bell?", "What is food?"], llm=_FailsOnce(reply="Food is a reward."))
    with client, client.websocket_connect(f"/api/live?session_id={session['id']}", headers=ORIGIN) as ws:
        _receive_until(ws, "state")
        for frame in [SPEECH] * 20 + [SILENCE] * 30:
            ws.send_bytes(frame)
        events, _ = _receive_until(ws, "metrics")
        assert next(e for e in events if e["type"] == "error")["message"] == "unexpected failure"
        events, _ = _receive_until(ws, "state")
        assert events[-1]["state"] == "listening"         # not stuck on "thinking"
        for frame in [SPEECH] * 20 + [SILENCE] * 30:
            ws.send_bytes(frame)
        events, _ = _receive_until(ws, "reply_done")
    assert next(e for e in events if e["type"] == "user_final")["text"] == "What is food?"


def test_a_speech_failure_is_reported_once_per_turn(live_app):
    store, session = live_app
    client, voice = _connect(store, session, ["What is classical conditioning?"])

    def broken(text, voice_name, speed):
        raise VoiceError("Kokoro could not start on the GPU (CUDA is not available)")

    voice.tts.synthesize = broken
    with client, client.websocket_connect(f"/api/live?session_id={session['id']}", headers=ORIGIN) as ws:
        _receive_until(ws, "state")
        for frame in [SPEECH] * 20 + [SILENCE] * 30:
            ws.send_bytes(frame)
        events, audio = _receive_until(ws, "metrics")
    errors = [e for e in events if e["type"] == "error"]
    # two sentences failed: one message; the reply itself still arrives as text
    assert audio == 0 and len(errors) == 1 and "GPU" in errors[0]["message"]
    assert "reply_done" in [e["type"] for e in events]


def test_live_rejects_other_origins_and_unknown_sessions(live_app):
    store, session = live_app
    client, _ = _connect(store, session, [])
    with client:
        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect(f"/api/live?session_id={session['id']}",
                                          headers={"origin": "http://evil.example"}) as ws:
                ws.receive()
        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect("/api/live?session_id=9999", headers=ORIGIN) as ws:
                ws.receive()
