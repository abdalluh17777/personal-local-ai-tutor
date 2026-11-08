"""Apply reviewed corrections to the PSYC 110 study guides. Safe to run more than once.

Each correction below was checked: either a transcription error in the Yale transcript (the correct form is
the established name), a reading list split wrongly by the first importer, or a 2007 figure that needs a note.
"""

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LIB = ROOT / "materials" / "library" / "psyc-110"

# (file prefix, wrong text, right text): transcription errors in the Yale transcripts
SPELLING = [
    ("19-", "Rosenthal and Jackson", "Rosenthal and Jacobson"),   # Rosenthal & Jacobson, Pygmalion in the Classroom
    ("18-", "When Prophesy Fails", "When Prophecy Fails"),        # Festinger et al.
    ("17-", "super ordinate goal", "superordinate goal"),         # Sherif
    ("12-", "Anatol Rappaport", "Anatol Rapoport"),
    ("20-", "Criteria for Accessing Mental Disorders", "Criteria for Assessing Mental Disorders"),
    ("19-", "Mahzarin Banaji developed the **implicit attitudes test**",
     "Mahzarin Banaji helped develop the **Implicit Association Test** (the lecture calls it the \"implicit attitudes test\")"),
    ("19-", "the implicit attitudes test |", "the Implicit Association Test |"),
    ("19-", "Use \"implicit attitudes test\"", "Use \"Implicit Association Test\""),
]

# Figures given in the 2007 lectures that are now known to be different
DATED = [
    ("02-", "- There are about one thousand billion neurons.",
     "- There are about one thousand billion neurons (the figure given in the 2007 lecture; today's best estimate is about 86 billion)."),
    ("02-", "| About one thousand billion in the brain |", "| Billions in the brain (86 billion by today's estimate) |"),
    ("05-", "which affects about one in a thousand people, mostly boys.",
     "which affects about one in a thousand people, mostly boys (the 2007 lecture's figure; current estimates are much higher)."),
]


def readings_section(readings: list[str]) -> str:
    items = "\n".join(f"- {r}" for r in readings) if readings else "No readings listed."
    return f"## Readings from the original course\n{items}\n"


def main() -> int:
    meta = json.loads((LIB / "course.json").read_text(encoding="utf-8"))
    by_stem = {Path(lec["file"]).stem: lec for lec in meta["lectures"]}
    changes = 0
    for guide in sorted((LIB / "guides").glob("*.md")):
        text = original = guide.read_text(encoding="utf-8")
        for prefix, wrong, right in SPELLING + DATED:
            if guide.name.startswith(prefix):
                text = text.replace(wrong, right)
        # One heading style for guest lectures: the guest is named in the subtitle line
        text = re.sub(r"^(# .*?)\s*\(Guest Lecture by [^)]*\)\s*$", r"\1", text, count=1, flags=re.M)
        # Reading lists from the corrected course.json (the first import joined some of them)
        lec = by_stem.get(guide.stem)
        if lec:
            text = re.sub(r"## Readings from the original course\n.*\Z", readings_section(lec["readings"]),
                          text, flags=re.S)
        if text != original:
            guide.write_text(text, encoding="utf-8")
            changes += 1
            print(f"updated {guide.name}")
    print(f"{changes} guide(s) changed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
