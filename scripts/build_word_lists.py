"""Build the word lists for the tutor's daily vocabulary, and load them into the running app.

    uv run --with wordfreq python scripts/build_word_lists.py build [--check-senses]   # word-lists/*.json
    uv run python scripts/build_word_lists.py load                     # into the app (safe to repeat)

Two lists:
- NAWL, the New Academic Word List 1.2 (Browne, Culligan and Phillips, CC BY-SA 4.0): about 960 words of general
  academic English, with easy English definitions. Ordered by general English frequency (wordfreq), so the most
  common academic words come first.
- Psychology research terms: the glossary of "Research Methods in Psychology" (Jhangiani et al., CC BY-NC-SA
  4.0), ordered by the Stage 4 lesson in which each term first appears.
Example sentences are real sentences from the course books and the PSYC 110 lectures in the library, chosen
to be short and to use the word; a word with no good sentence keeps an empty example. With --check-senses (Ollama
running), the local model checks that each NAWL example uses the word in the list's meaning, and the next
candidate is tried when it does not. Research terms use the sentence where the book defines the term.
"""

import csv
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LIBRARY = ROOT / "materials" / "library"
OUT = LIBRARY / "word-lists"
BASE = "http://127.0.0.1:8765"
# Where example sentences come from, best first: written psychology, the advanced books, then the spoken lectures
CORPUS = ["psych-research-methods/sections", "esl-c1-reading/sections", "esl-c1-writing/sections",
          "esl-b2-grammar/sections", "esl-b2-writing/sections", "psyc-110/transcripts"]
POS = {"n": "noun", "v": "verb", "adj": "adjective", "adv": "adverb", "prep": "preposition", "conj": "conjunction",
       "n pl.": "noun", "det": "determiner", "pron": "pronoun"}     # with "verb" and "prefix": all of NAWL's (checked)


def plain(text: str) -> str:
    """Section text without its formatting marks, list markers and tables."""
    text = re.sub(r"\*\*|~~|(?<!_)__(?!_)|(?<![*\w])\*(?=\S)|(?<=\S)\*(?![*\w])", "", text)
    return re.sub(r"\s+([,.;:!?])", r"\1", text)      # "called conditions ." after a bold term


def sentences() -> list[tuple[int, str]]:
    """(source rank, sentence) for every usable sentence of the corpus."""
    out = []
    for rank, folder in enumerate(CORPUS):
        for path in sorted((LIBRARY / folder).glob("*.txt")):
            body = path.read_text(encoding="utf-8").partition("\n\n")[2]
            for line in body.splitlines():
                if line.startswith(("|", "#", "[", "   ", "Q:", "Task:", "Card:", "Source:", "License:")):
                    continue
                line = re.sub(r"^\s*(?:-|\d+\.|[a-z]\.)\s+", "", plain(line))
                for s in re.split(r"(?<=[.!?])\s+(?=[A-Z\"“])", line):
                    s = s.strip()
                    words = s.split()
                    balanced = s.count('"') % 2 == 0 and s.count("“") == s.count("”")
                    if 8 <= len(words) <= 26 and s[0].isupper() and s[-1] in ".!?" and balanced and not re.search(
                            r"https?://|\[|\]|\(|\)|_{2,}|\bFigure\b|\bTable\b|\bp\.\s*\d|\d{4}\)", s):
                        out.append((rank, s))
    return out


def form_pattern(forms: list[str]) -> re.Pattern:
    """The word's forms as written: lowercase forms match lowercase text (or a capital at the start of the
    sentence), so "super" does not match the song title "Super Freak"."""
    alts = []
    for f in sorted(forms, key=len, reverse=True):
        alts.append(re.escape(f))
        if f[:1].islower():
            alts.append(r"^" + re.escape(f[:1].upper() + f[1:]))
    return re.compile(r"(?:\b|^)(?:" + "|".join(alts) + r")\b")


def example_candidates(forms: list[str], corpus: list[tuple[int, str]], n: int = 4) -> list[str]:
    """Real sentences that use one of the word's forms, best first: the most relevant source, about 15 words."""
    pattern = form_pattern(forms)
    found = sorted({(rank, abs(len(s.split()) - 15), s) for rank, s in corpus if pattern.search(s)})
    return [s for _, _, s in found[:n]]


def example_for(forms: list[str], corpus: list[tuple[int, str]]) -> str:
    found = example_candidates(forms, corpus, 1)
    return found[0] if found else ""


SENSE_SCHEMA = {"type": "object", "properties": {"same_meaning": {"type": "boolean"}}, "required": ["same_meaning"]}


def same_sense(client, word: str, pos: str, meaning: str, sentence: str) -> bool:
    """Ask the local model whether the sentence uses the word in this meaning ("tense" the adjective is not the
    grammar term in "the simple present tense")."""
    prompt = (f'Word: "{word}" ({pos})\nMeaning: {meaning}\nSentence: {sentence}\n\n'
              f'Is "{word}" (or its form) used in the sentence with this meaning and as this part of speech? '
              'Answer with JSON: {"same_meaning": true} or {"same_meaning": false}.')
    r = client.post("/api/chat", json={"model": "tutor", "stream": False, "think": False, "format": SENSE_SCHEMA,
                                       "options": {"temperature": 0},
                                       "messages": [{"role": "user", "content": prompt}]})
    r.raise_for_status()
    return bool(json.loads(r.json()["message"]["content"]).get("same_meaning"))


def check_senses(items: list[dict], candidates: dict[str, list[str]]) -> None:
    """Keep only examples that use the word in the list's meaning: the first candidate that passes."""
    import httpx
    client = httpx.Client(base_url="http://127.0.0.1:11434", timeout=120)
    changed = emptied = 0
    for n, item in enumerate(items, 1):
        chosen = ""
        for sentence in candidates.get(item["word"], []):
            if same_sense(client, item["word"], item["pos"], item["meaning"], sentence):
                chosen = sentence
                break
        if chosen != item["example"]:
            changed += 1
            emptied += not chosen
        item["example"] = chosen
        if n % 100 == 0:
            print(f"  checked {n}/{len(items)}")
    print(f"  sense check: {changed} examples replaced ({emptied} left empty)")


def defining_sentence(term: str) -> str:
    """The sentence where the book introduces a term in bold ("... is called the **mean**."): it uses the term in
    the sense of the glossary."""
    folder = LIBRARY / "psych-research-methods" / "sections"
    bold = re.compile(r"\*\*" + re.escape(term) + r"s?\*\*", re.I)
    for path in sorted(folder.glob("[01]*.txt")):
        for line in path.read_text(encoding="utf-8").splitlines():
            if not bold.search(line) or line.startswith(("|", "#")):
                continue
            for s in re.split(r"(?<=[.!?])\s+(?=[A-Z\"“])", re.sub(r"^\s*(?:-|\d+\.)\s+", "", line)):
                if bold.search(s):
                    s = plain(s).strip()
                    if 6 <= len(s.split()) <= 40 and s[-1:] in ".!?":
                        return s
    return ""


def read_text(path: Path) -> str:
    """The NAWL files are not all UTF-8 (one has Windows-1252 accents)."""
    data = path.read_bytes()
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError:
        return data.decode("cp1252")


def build_nawl(corpus) -> dict:
    from wordfreq import zipf_frequency
    folder = LIBRARY / "vocab-nawl"
    forms = {}
    for row in csv.reader(read_text(folder / "NAWL_12_lemmatized_for_teaching.csv").splitlines()):
        row = [w.strip() for w in row if w.strip()]
        if row:
            forms[row[0].lower()] = row
    items = []
    if True:
        for row in csv.DictReader(read_text(folder / "NAWL_12_with_en_definitions.csv").splitlines()):
            word = row["Meanings"].strip()
            if not word or not row["English Definition"].strip():     # "DESCENDENT": no definition to learn
                continue
            family = forms.get(word.lower(), [word])
            pattern = form_pattern(family)
            items.append({"word": word, "meaning": row["English Definition"].strip(),
                          "pos": POS.get(row["POS"].strip(), row["POS"].strip()),
                          "forms": ", ".join(f for f in family if f.lower() != word.lower()),
                          "example": example_for(family, corpus),
                          # how often the word family is used in the academic books of the library
                          "uses": sum(1 for _, s in corpus if pattern.search(s)),
                          "frequency": zipf_frequency(word, "en")})
    # Most used in academic writing first; prefixes ("non", "anti") last: they are not words to learn alone
    items.sort(key=lambda i: (i["pos"] == "prefix", -i["uses"], -i["frequency"]))
    for i in items:
        del i["uses"], i["frequency"]
    if "--check-senses" in sys.argv:
        candidates = {i["word"]: example_candidates([i["word"]] + [f.strip() for f in i["forms"].split(",") if f.strip()],
                                                    corpus) for i in items}
        check_senses(items, candidates)
    return {"slug": "nawl", "title": "Academic words (NAWL)", "daily_new": 5,
            "description": "The New Academic Word List: about 960 words that are very frequent in academic English, "
                           "most common first.",
            "source": "New Academic Word List 1.2, Browne, C., Culligan, B., & Phillips, J. (2013), "
                      "newgeneralservicelist.com, CC BY-SA 4.0. Example sentences from the course books.",
            "items": items}


def build_research_terms(corpus) -> dict:
    rows = []
    text = (LIBRARY / "psych-research-methods" / "sections" / "14.03-glossary.txt").read_text(encoding="utf-8")
    for line in text.splitlines():
        cells = [c.strip() for c in line.strip("| ").split(" | ")]
        if line.startswith("| ") and len(cells) >= 2 and cells[0] not in ("Word(s)", "Words (or words that have the same definition)") \
                and not cells[0].startswith("(Eg."):
            rows.append((cells[0], plain(cells[1])))
    # Order: the Stage 4 lesson where the term first appears
    syllabus = json.loads((LIBRARY / "esl-stage4" / "syllabus.json").read_text(encoding="utf-8"))
    raw = [" ".join((LIBRARY / s.partition("#")[0]).read_text(encoding="utf-8") for s in lesson["sources"])
           for unit in syllabus["units"] for lesson in unit["lessons"]]
    lessons = [t.lower() for t in raw]
    everything = " ".join(raw)

    def first_lesson(term: str) -> int:
        pattern = re.compile(r"\b" + re.escape(term.lower()) + r"s?\b")
        return next((i for i, t in enumerate(lessons) if pattern.search(t)), len(lessons))
    items = []
    for term, definition in rows:
        # "Applied research" -> "applied research" when the book writes it so; "Bayesian statistics" stays
        lower = term[0].lower() + term[1:]
        word = lower if term[0].isupper() and re.search(r"\b" + re.escape(lower) + r"\b", everything) else term
        items.append({"word": word, "meaning": definition, "pos": "term", "forms": "",
                      "example": defining_sentence(term) or example_for([word, word + "s"], [c for c in corpus if c[0] == 0]),
                      "lesson": first_lesson(word)})
    items.sort(key=lambda i: (i["lesson"], i["word"].lower()))
    for i in items:
        del i["lesson"]
    # Paused at first: the terms belong to Stage 4 (start them from the Vocabulary page then)
    return {"slug": "psych-research-terms", "title": "Psychology research terms", "daily_new": 0,
            "description": "The terms of research methods in psychology, in the order of the Stage 4 lessons.",
            "source": "Glossary of Research Methods in Psychology, Jhangiani, Chiang, Cuttler and Leighton, "
                      "CC BY-NC-SA 4.0. Example sentences from the book and the PSYC 110 lectures.",
            "items": items}


def build() -> None:
    OUT.mkdir(exist_ok=True)
    corpus = sentences()
    print(f"{len(corpus)} example sentences available")
    for data in (build_nawl(corpus), build_research_terms(corpus)):
        path = OUT / f"{data['slug']}.json"
        path.write_text(json.dumps(data, indent=1, ensure_ascii=False), encoding="utf-8")
        with_example = sum(1 for i in data["items"] if i["example"])
        print(f"{data['title']}: {len(data['items'])} words, {with_example} with an example -> {path}")


def load() -> None:
    import httpx
    api = httpx.Client(base_url=BASE, headers={"Origin": BASE}, timeout=120)
    for path in sorted(OUT.glob("*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        result = api.post("/api/word-lists", json=data)
        result.raise_for_status()
        r = result.json()
        print(f"{r['title']}: {r['total']} words, {r['introduced']} already started, {r['daily_new']} new a day")


if __name__ == "__main__":
    {"build": build, "load": load}[sys.argv[1]]()
