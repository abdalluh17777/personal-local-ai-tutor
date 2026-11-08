"""Measure the pronunciation check, as the app runs it, against expert scores on speechocean762.

    uv run --with pyarrow python tests/pron_speechocean.py <test parquet> [limit]

speechocean762 (Apache 2.0, huggingface.co/datasets/mispeech/speechocean762): 2,500 test sentences read by
Mandarin-speaking learners, every sound scored by five experts (2 correct, 1 heavy accent, 0 wrong or missing;
the average is given). Only the speakers that were not used to choose the thresholds are counted (every second
speaker in sorted order was used for that). A sound counts when the word's pronunciation the app chose is the
one the experts scored.

Reports, for the app's "wrong" and "check" levels: the share of sounds every expert scored correct that are
flagged, and the share of clear errors (0.8 or less) that are found.
"""

import io
import sys
import time
import wave
from pathlib import Path

import numpy as np
import pyarrow.parquet as pq

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "app"))
from tutor import assessment  # noqa: E402


def main(parquet: str, limit: str | None = None) -> int:
    rows = pq.read_table(parquet).to_pylist()
    speakers = sorted({r["speaker"] for r in rows})
    held_out = set(speakers[1::2])
    rows = [r for r in rows if r["speaker"] in held_out][:int(limit) if limit else None]
    checker = assessment.PronunciationChecker()
    status, acc = [], []
    t0 = time.perf_counter()
    for r in rows:
        with wave.open(io.BytesIO(r["audio"]["bytes"])) as w:
            x = np.frombuffer(w.readframes(w.getnframes()), dtype="<i2").astype(np.float32) / 32768
        text = " ".join(w["text"] for w in r["words"])
        try:
            result = checker.check_array(x, text)
        except ValueError:
            continue
        for w_app, w_exp in zip(result["words"], r["words"]):
            expected = [p.rstrip("012*") for p in w_exp["phones"]]
            if [p["phone"] for p in w_app["phones"]] != expected:
                continue
            for p, a in zip(w_app["phones"], w_exp["phones-accuracy"]):
                status.append(p["status"])
                acc.append(a)
    s, a = np.array(status), np.array(acc)
    print(f"{len(rows)} sentences, {len(s)} sounds compared, {time.perf_counter() - t0:.0f} s")
    for label, flagged in (("wrong", s == "wrong"), ("wrong or check", s != "good")):
        print(f"{label:16} flagged on correct sounds {flagged[a >= 2].mean():6.1%}   clear errors found "
              f"{flagged[a <= 0.8].mean():6.1%}   flags the experts also marked {(flagged & (a < 2)).sum() / max(flagged.sum(), 1):6.1%}")
    return 0


if __name__ == "__main__":
    sys.exit(main(*sys.argv[1:3]))
