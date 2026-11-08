"""Automatic checks for lesson guides written to LESSON_GUIDE_SPEC.md.

    uv run --with pronouncing python scripts/check_guides.py materials/library/esl-stage1/guides

Checks the template (headings in order, title and subtitle lines), English only (no Arabic script, no emoji),
length, and the pronunciation answers that can be checked against the CMU Pronouncing Dictionary
(American English): syllables and stress written as ba·NA·na, and the sound of -s and -ed endings written
next to a word as /s/ /z/ /ɪz/ or /t/ /d/ /ɪd/. Lines marked "(dictionaries differ)" or "(check)" are skipped.
Prints one line per problem; exit status 1 if any were found.
"""

import re
import sys
from pathlib import Path

import pronouncing

HEADINGS = ["In this lesson", "Learning goals", "Key language", "Rules and explanations", "Practice", "Answer key",
            "Speak with your tutor", "Read aloud", "Writing task", "Watch online"]
OPTIONAL = {"Read aloud", "Writing task", "Watch online"}
STRESS = re.compile(r"(?<![\w·])([A-Za-z']+(?:·[A-Za-z']+)+)(?![\w·])")
# "cats /s/", "cats: /s/", "cats → /s/", "| cats | /s/ |": only punctuation between the word and its sound
ENDING = re.compile(r"(?<![\w·-])([A-Za-z']+(?:s|ed))\b\**[\s\-–—:=→|(*]{1,8}(/(?:s|z|ɪz|əz|iz|t|d|ɪd|əd|id)/)")
# Words of the rules themselves ("the ending is /s/", "voiced (/z/)"), not answers
RULE_WORDS = set("""is as was has does his its this yes sounds vowels consonants voiced unvoiced words letters endings
ends verbs nouns plurals pronounced added followed used called becomes means pairs""".split())
SKIP = re.compile(r"dictionaries differ|\(check\)", re.I)
EMOJI = re.compile("[\U0001F300-\U0001FAFF☀-➿]")
VOWEL_END = {"IZ": "ɪz", "S": "s", "Z": "z", "ID": "ɪd", "T": "t", "D": "d"}


def pronunciations(word: str) -> list[str]:
    return pronouncing.phones_for_word(word.lower().strip("'"))


def check_stress(token: str) -> str | None:
    syllables = token.split("·")
    phones = pronunciations("".join(syllables))
    if not phones:
        return None
    stressed = [i + 1 for i, s in enumerate(syllables) if s.isupper() and any(c.isalpha() for c in s)]
    if len(stressed) == len(syllables):        # a word written fully in capitals is not a stress mark
        stressed = []
    options = []
    for p in phones:
        pattern = pronouncing.stresses(p)
        primary = [i + 1 for i, s in enumerate(pattern) if s == "1"]
        options.append((len(pattern), primary))
        if len(pattern) == len(syllables) and (not stressed or stressed == primary[:1] or stressed == primary):
            return None
    count, primary = options[0]
    return f"{token}: dictionary has {count} syllable(s), stress on {', '.join(map(str, primary)) or '-'}"


def ending_sound(word: str) -> set[str]:
    sounds = set()
    for p in pronunciations(word):
        ph = p.split()
        last2 = "".join(x.rstrip("012") for x in ph[-2:])
        if word.endswith("ed"):
            sounds.add("ɪd" if last2 in ("IHD", "AHD") and ph[-1] == "D" else VOWEL_END.get(ph[-1], ph[-1]))
        else:
            sounds.add("ɪz" if last2 in ("IHZ", "AHZ") else VOWEL_END.get(ph[-1], ph[-1]))
    return sounds


def check_guide(path: Path) -> list[str]:
    text = path.read_text(encoding="utf-8")
    problems = []
    lines = text.splitlines()
    if not re.match(r"# Lesson \d{2}: .+", lines[0] if lines else ""):
        problems.append("first line is not '# Lesson NN: title'")
    if not (len(lines) > 3 and lines[2].startswith("*English Stage") and lines[3].startswith("*Adapted from")):
        problems.append("subtitle lines ('*English Stage ...*', '*Adapted from ...*') missing or out of place")
    found = [h[3:].strip() for h in lines if h.startswith("## ")]
    unknown = [h for h in found if h not in HEADINGS]
    if unknown:
        problems.append(f"unexpected headings: {unknown}")
    missing = [h for h in HEADINGS if h not in found and h not in OPTIONAL]
    if missing:
        problems.append(f"missing headings: {missing}")
    order = [h for h in found if h in HEADINGS]
    if order != sorted(order, key=HEADINGS.index):
        problems.append(f"headings out of order: {order}")
    if re.search(r"[؀-ۿ]", text):
        problems.append("contains Arabic script")
    if EMOJI.search(text):
        problems.append("contains emoji")
    words = len(re.findall(r"[A-Za-z0-9']+", text))
    if not 800 <= words <= 2600:         # practice from the book makes some guides longer than the spec's 1,600
        problems.append(f"length {words} words (spec: about 900 to 1,600)")
    for n, line in enumerate(lines, 1):
        if SKIP.search(line):
            continue
        for token in dict.fromkeys(STRESS.findall(line)):
            issue = check_stress(token)
            if issue:
                problems.append(f"line {n}: {issue}")
        for word, sound in ENDING.findall(line):
            if word.lower() in RULE_WORDS:
                continue
            expected = ending_sound(word)
            given = sound.strip("/").replace("ə", "ɪ").replace("i", "ɪ").replace("ɪɪ", "ɪ")
            if expected and given not in expected and expected & {"s", "z", "ɪz", "t", "d", "ɪd"}:
                problems.append(f"line {n}: {word} {sound}: dictionary ending is /{'/ or /'.join(sorted(expected))}/")
    return problems


def main(folder: str) -> None:
    sys.stdout.reconfigure(encoding="utf-8")      # IPA symbols on a Windows console
    total = 0
    for path in sorted(Path(folder).glob("*.md")):
        problems = check_guide(path)
        total += len(problems)
        print(f"{path.name}: {'ok' if not problems else ''}")
        for p in problems:
            print(f"   - {p}")
    print(f"\n{total} problem(s)")
    sys.exit(1 if total else 0)


if __name__ == "__main__":
    main(sys.argv[1])
