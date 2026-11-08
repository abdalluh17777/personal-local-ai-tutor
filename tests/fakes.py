"""Stand-ins for the model and the speech engines, so the app's logic is tested without the GPU."""

import asyncio

import numpy as np

from tutor.llm import ModelError
from tutor.voice import wav_bytes


class FakeLLM:
    """Stands in for Ollama: replays scripted outputs and records every request."""

    def __init__(self, reply="Classical conditioning pairs a neutral stimulus with a reflex [S1].",
                 observation=None, reflection=None, keywords=None, fail_stream=False, word_delay=0.0,
                 fail_json=False):
        self.reply = reply
        self.observation = observation or {"corrections": [], "better_phrasing": "", "vocabulary": [], "memories": []}
        self.reflection = reflection or {"summary": "Studied conditioning.", "next_step": "Review it.",
                                         "new_memories": [], "outdated_memory_ids": []}
        self.keywords = keywords or ["classical conditioning"]
        self.fail_stream = fail_stream
        self.fail_json = fail_json
        self.word_delay = word_delay
        self.chat_requests = []
        self.think_requests = []
        self.json_requests = []

    async def stream_chat(self, messages, think=False):
        self.chat_requests.append(messages)
        self.think_requests.append(think)
        if self.fail_stream:
            raise ModelError("model down")
        for word in self.reply.split(" "):
            if self.word_delay:
                await asyncio.sleep(self.word_delay)
            yield word + " "

    async def json_chat(self, prompt, schema, *, think=False, temperature=0.2):
        self.json_requests.append(prompt)
        if self.fail_json:
            raise ModelError("model down")
        props = schema["properties"]
        if "corrections" in props:
            return self.observation
        if "summary" in props:
            return self.reflection
        if "keywords" in props:
            return {"keywords": self.keywords}
        if "meaning" in props:
            return {"meaning": "a simple meaning", "example": "An example sentence."}
        return {"topics": ["Classical conditioning", "Operant conditioning"]}

    async def status(self):
        # The keys of LLM.status() for a loaded model: the page shows "model" and "vram_gb"
        return {"ollama": "test", "model": "tutor", "installed": True, "loaded": True, "gpu_percent": 100.0,
                "vram_gb": 9.5}

    async def close(self):
        pass


class FakeVoice:
    """Stands in for Whisper and Kokoro; records what the server asked for."""

    def __init__(self, transcripts=None):
        self.hints = []
        self.spoken = []
        self.transcripts = list(transcripts or [])
        fake = self

        class STT:
            loaded = False

            def transcribe(self, audio, hint=""):
                fake.hints.append(hint)
                return {"text": "I goes to the library", "language": "en", "duration": 2.0}

            def transcribe_array(self, samples, hint=""):
                fake.hints.append(hint)
                text = fake.transcripts.pop(0) if fake.transcripts else ""
                return {"text": text, "language": "en", "duration": len(samples) / 16000}

        class TTS:
            loaded = False

            def synthesize(self, text, voice, speed):
                fake.spoken.append((text, voice, speed))
                return np.zeros(2400, dtype=np.float32), 24000

            def speak(self, text, voice, speed):
                return wav_bytes(*self.synthesize(text, voice, speed))

            def voices(self):
                return [{"id": "af_heart", "label": "Heart (American, female)"}]

        class Pron:
            """Says the first sound of the first word was wrong (/p/ heard as /b/), everything else right."""

            def check(self, audio, text, focus=frozenset()):
                if not text.strip():   # as the real check: a bad request (400), before any sound is judged
                    raise ValueError("Type the word or sentence you want to practise.")
                fake.checked.append((text, set(focus)))
                words = []
                for i, w in enumerate(text.split()):
                    phones = [{"phone": "P", "ipa": "p", "status": "wrong" if i == 0 else "good", "confidence": 0.01,
                               **({"heard": "B", "heard_ipa": "b", "tip": "Puff of air."} if i == 0 else {})},
                              {"phone": "EH", "ipa": "e", "status": "good", "confidence": 0.9}]
                    words.append({"text": w.strip(".,!?"), "phones": phones, "added": [], "score": 45 if i == 0 else 90,
                                  "status": "wrong" if i == 0 else "good"})
                issue = {"word": words[0]["text"], "word_index": 0, "phone": "P", "ipa": "p", "heard": "B",
                         "heard_ipa": "b", "status": "wrong", "tip": "Puff of air.", "confidence": 0.01}
                return {"text": text, "score": 70, "duration": 1.0, "words": words, "issues": [issue]}

            def status(self):
                return {"available": True, "loaded": False, "model": "fake"}

        self.checked = []
        self.stt, self.tts, self.pron = STT(), TTS(), Pron()

    def status(self):
        # The keys of Voice.status()
        return {"stt_loaded": False, "tts_loaded": False, "tts_device": None, "stt_model": "fake",
                "kokoro_files": True, "arabic_voice": False, "pronunciation": self.pron.status()}

    def warmup(self):
        return self.status()


class LoudnessVAD:
    """Scripted VAD: a frame is speech when it is loud. Lets tests 'speak' with plain arrays."""

    def __call__(self, frame):
        return 1.0 if np.abs(frame).max() > 0.1 else 0.0
