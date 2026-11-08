"""Load every course that ships with the project into the running tutor (http://127.0.0.1:8765).

    uv run python scripts/load_bundled_courses.py

scripts/setup.ps1 runs this once, on a fresh install. The courses are created in the order they were first made,
so each one gets the same id and therefore the same folder under materials/ ("2-PSYC 110 ...", "3-English
Stage 1 (B1)", ...): the files already in those folders are reused, not copied a second time. Safe to run again:
a course, topic or file that is already there is kept as it is.

    1  PSYC 110 - Lecture 1 - Introduction     one lecture transcript, uploaded through the Courses page
    2  PSYC 110 · Introduction to Psychology   22 lectures: study guide + transcript each (load_course.py)
    3  English Stage 1 (B1)                    34 lessons  \
    4  English Stage 2 (B2)                    44 lessons   | study guide + lesson material each
    5  English Stage 3 (C1)                    52 lessons   | (build_syllabus.py <stage> load)
    6  English Stage 4 (Psychology)            37 lessons  /
       + the vocabulary word lists (NAWL academic words, psychology research terms)
"""

import sys
from pathlib import Path

import httpx

SCRIPTS = Path(__file__).resolve().parent
ROOT = SCRIPTS.parent
sys.path.insert(0, str(SCRIPTS))

import build_syllabus      # noqa: E402
import build_word_lists    # noqa: E402
import load_course         # noqa: E402

BASE = "http://127.0.0.1:8765"
FIRST_COURSE = "PSYC 110 - Lecture 1 - Introduction"
FIRST_FILE = ROOT / "materials" / f"1-{FIRST_COURSE}" / "1.txt"
STAGES = ["esl-stage1", "esl-stage2", "esl-stage3", "esl-stage4"]


def main() -> int:
    api = httpx.Client(base_url=BASE, headers={"Origin": BASE}, timeout=300)
    try:
        existing = api.get("/api/courses").json()
    except httpx.HTTPError:
        sys.exit(f"The tutor is not running on {BASE}: start it first (Start Personal Tutor.cmd).")
    if existing and not any(c["name"] == FIRST_COURSE for c in existing):
        # Courses made by hand came first: the ids no longer line up with the shipped folders. Loading still works,
        # the app just stores its own copy of each file in new folders.
        print("Note: this database already has other courses; the bundled ones get new folders under materials\\.")

    print(f"[1/6] {FIRST_COURSE}")
    course = next((c for c in existing if c["name"] == FIRST_COURSE), None)
    if not course:
        course = api.post("/api/courses", json={"name": FIRST_COURSE, "description": ""}).json()
    if FIRST_FILE.exists() and not api.get(f"/api/courses/{course['id']}/documents").json():
        r = api.post(f"/api/courses/{course['id']}/documents",
                     files=[("files", (FIRST_FILE.name, FIRST_FILE.read_bytes(), "text/plain"))]).json()[0]
        print("  1.txt: " + ("uploaded" if r["ok"] else r["error"]))

    print("\n[2/6] PSYC 110 · Introduction to Psychology (Yale)")
    load_course.main("psyc-110", "PSYC 110 · Introduction to Psychology (Yale)")

    for i, stage in enumerate(STAGES, start=3):
        print(f"\n[{i}/6] {stage}")
        build_syllabus.main(stage, "load")

    print("\nWord lists")
    build_word_lists.load()

    print("\nAll bundled courses are loaded.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
