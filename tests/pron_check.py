"""Check the pronunciation check on the synthetic set from tests/pron_synth.py, with the real model on the GPU.

    uv run python tests/pron_check.py <folder> [model dir]

Each recording is checked twice: as free practice (any sentence) and in the app's drill for the error it tests,
with that drill's focus sounds (drills.py). For recordings said correctly: how many sounds are flagged (should be none).
For recordings with one error: whether that error is found, on the right word and sound, and what was heard.
The thresholds in assessment.py were set on speechocean762 (non-native speakers, every sound scored by five
experts); this set checks them on clean speech with errors that are known exactly.
"""

import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "app"))
from tutor import assessment, drills  # noqa: E402


def drill_of(item: dict) -> dict:
    """The app's drill for an item's error: the first whose focus holds both sounds ("+AH": an added vowel, the
    consonant groups drill; "-": left out)."""
    heard = item["heard"]
    if heard == "+AH":
        return drills.DRILL_BY_ID["clusters"]
    sounds = {None if heard == "-" else heard.lstrip("+")}
    if item["expected"]:
        sounds.add(item["expected"])
    return next(d for d in drills.DRILLS if sounds <= set(d["focus"]))


def run(checker, folder: Path, manifest: list[dict], drill: bool, verbose: bool) -> dict:
    flagged = sounds = found = missed = right_sound = swaps = swaps_found = 0
    seconds = []
    for item in manifest:
        focus = set(drill_of(item)["focus"]) if drill else set()
        t0 = time.perf_counter()
        r = checker.check((folder / f"{item['id']}.wav").read_bytes(), item["text"], focus)
        seconds.append(time.perf_counter() - t0)
        flags = r["issues"]
        if item["kind"] == "correct":
            sounds += sum(len(w["phones"]) for w in r["words"])
            flagged += len(flags)
            if flags and verbose:
                print(f"  false alarm {item['id']:24} {item['text']:36} " + "; ".join(
                    f"{i['word']}: /{i['ipa'] or '+'}/ heard {i['heard']} ({i['status']})" for i in flags))
            continue
        added = item["heard"].startswith("+")
        hit = [i for i in flags if i["word_index"] == item["word"] and
               ((added and i["phone"] is None) or (not added and i["phone"] == item["expected"]))]
        swap = item["expected"] and not item["heard"].startswith(("+", "-"))
        swaps += bool(swap)
        if hit:
            found += 1
            swaps_found += bool(swap)
            right_sound += (hit[0]["heard"] or "-") == item["heard"].lstrip("+")
        else:
            missed += 1
            if verbose:
                print(f"  missed      {item['id']:24} {item['text']:36} flags: " +
                      "; ".join(f"{i['word']}:{i['phone']}->{i['heard']}" for i in flags))
    errors = found + missed
    return {"false alarms": f"{flagged} on {sounds} sounds ({flagged / max(sounds, 1):.1%})",
            "errors found": f"{found} of {errors} ({found / max(errors, 1):.0%}), {right_sound} with the right sound",
            "swapped sounds found": f"{swaps_found} of {swaps} ({swaps_found / max(swaps, 1):.0%})",
            "median seconds": round(sorted(seconds)[len(seconds) // 2], 2)}


def main(folder: str, model_dir: str | None = None) -> int:
    folder = Path(folder)
    model = assessment.PhoneModel(Path(model_dir)) if model_dir else assessment.PhoneModel()
    checker = assessment.PronunciationChecker(model)
    manifest = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
    print("free practice:", json.dumps(run(checker, folder, manifest, drill=False, verbose=True)))
    print("in its drill:", json.dumps(run(checker, folder, manifest, drill=True, verbose=True)))
    return 0


if __name__ == "__main__":
    sys.exit(main(*sys.argv[1:3]))
