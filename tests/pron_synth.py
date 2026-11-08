"""Make a test set for the pronunciation check with the tutor's own voice (Kokoro): each item is said correctly
and with one typical learner error, by four voices.

    uv run python tests/pron_synth.py <folder>

Writes <folder>/<id>.wav and <folder>/manifest.json. The error is made in Kokoro's phoneme string, so it is
exactly known: a substituted sound ("pen" said with /b/), a vowel added inside a consonant group ("street" as
"sitreet"), or /g/ added after /ŋ/. A real learner's voice is harder than this; the set checks that correct
speech is not flagged and that clear errors are found in the right place.
"""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "app"))
from tutor.voice import TextToSpeech, wav_bytes  # noqa: E402

VOICES = ["af_heart", "am_michael", "bf_emma", "bm_george"]
# text, word with the error, Kokoro phoneme before -> after, expected sound, sound said instead ("+X": added)
ITEMS = [
    ("pen", "pen", "p", "b", "P", "B"),
    ("Please pass the paper.", "pass", "p", "b", "P", "B"),
    ("very", "very", "v", "f", "V", "F"),
    ("I have a very good view.", "view", "v", "f", "V", "F"),
    ("ship", "ship", "ɪ", "iː", "IH", "IY"),
    ("We need to leave this city.", "leave", "iː", "ɪ", "IY", "IH"),
    ("pin", "pin", "ɪ", "ɛ", "IH", "EH"),
    ("The man had a bad day.", "bad", "æ", "ɛ", "AE", "EH"),
    ("cup", "cup", "ʌ", "ɑː", "AH", "AA"),
    ("full", "full", "ʊ", "uː", "UH", "UW"),
    ("chair", "chair", "tʃ", "ʃ", "CH", "SH"),
    ("Which chair should I choose?", "chair", "tʃ", "ʃ", "CH", "SH"),
    ("think", "think", "θ", "s", "TH", "S"),
    ("I think there are three things.", "three", "θ", "t", "TH", "T"),
    ("this", "this", "ð", "z", "DH", "Z"),
    ("They thought it was better.", "They", "ð", "d", "DH", "D"),
    ("street", "street", "st", "sət", None, "+AH"),
    ("Students study in the school library.", "school", "sk", "sək", None, "+AH"),
    ("sing", "sing", "ŋ", "ŋɡ", None, "+G"),
    ("She asked him a question.", "asked", "skt", "sk", "T", "-"),
]


def main(folder: str) -> None:
    out = Path(folder)
    out.mkdir(parents=True, exist_ok=True)
    tts = TextToSpeech()
    kokoro = tts.load()
    manifest = []
    for n, (text, word, before, after, expected, heard) in enumerate(ITEMS):
        phonemes = kokoro.tokenizer.phonemize(text, "en-us")
        index = next(i for i, w in enumerate(text.replace("?", "").replace(".", "").split()) if w == word)
        # eSpeak joins some words ("in the" -> "ɪnðə") and drops stress in weak words, so the word is found by
        # its own phonemes, ignoring stress marks
        target = kokoro.tokenizer.phonemize(word, "en-us").replace("ˈ", "").replace("ˌ", "")
        plain, where = "", []
        for i, ch in enumerate(phonemes):
            if ch not in "ˈˌ":
                plain += ch
                where.append(i)
        start = plain.find(target)
        if start < 0 or before not in target:
            raise SystemExit(f"{text}: '{target}' / '{before}' not found in '{phonemes}'")
        at = where[start + target.index(before)]
        bad = phonemes[:at] + after + phonemes[at + len(before):]
        for voice in VOICES:
            for kind, ph in (("correct", phonemes), ("error", bad)):
                samples, rate = kokoro.create(ph, voice=voice, speed=0.95, lang="en-us", is_phonemes=True)
                item_id = f"{n:02d}-{voice}-{kind}"
                (out / f"{item_id}.wav").write_bytes(wav_bytes(samples, rate))
                manifest.append({"id": item_id, "text": text, "voice": voice, "kind": kind, "word": index,
                                 "expected": expected, "heard": heard, "phonemes": ph})
        print(f"{text:40} {phonemes}  ->  {bad}")
    (out / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"{len(manifest)} recordings in {out}")


if __name__ == "__main__":
    main(sys.argv[1])
