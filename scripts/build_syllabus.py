"""Build and load a combined course described by materials/library/<name>/syllabus.json (lessons drawn from
sections of several imported books, see import_libretexts.py).

    uv run --with markdown python scripts/build_syllabus.py esl-stage1 print   # PDFs into <name>/print/
    uv run python scripts/build_syllabus.py esl-stage1 load                     # into the running tutor app

Each lesson has a study guide (guides/L<NN>-*.md) and its source sections. For the tutor, the sections are
joined into one text per lesson (material/L<NN>-*.txt, answers kept so the tutor can check work). For print,
the same material is rendered with the exercise answers removed: they are in the study guide's answer key.
Loading is safe to repeat: a guide or material file that changed replaces the old copy in the app.
"""

import hashlib
import html
import json
import re
import sys
import tempfile
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

ROOT = Path(__file__).resolve().parent.parent
LIBRARY = ROOT / "materials" / "library"
BASE = "http://127.0.0.1:8765"
LESSONS_PER_WEEK = 3
PRINT_ATTACHMENT_WORDS = 1200      # a longer attached document (an article, a model paper) is named, not printed

_FILE_IMAGE = re.compile(r"\.(png|jpe?g|gif|svg)$|^Screen Shot|^fig-|^[\w.-]+$", re.I)
_AUDIO = re.compile(r"(?:(?<=[.:\"?)])|^)\s*([^\s.:\"?)][^.:\"?)]*?)\s*\.(?:m4a|mp3|wav)\b\.?")
_EMBED = [(re.compile(r"https?://www\.youtube(?:-nocookie)?\.com/embed/([\w-]+)"), r"https://youtu.be/\1"),
          (re.compile(r"https?://player\.vimeo\.com/video/(\d+)"), r"https://vimeo.com/\1")]


def slugify(text: str, limit: int = 50) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:limit].rstrip("-")


def lessons(syllabus: dict) -> list[dict]:
    """Every lesson in order, numbered from 1, with its unit."""
    out = []
    for unit in syllabus["units"]:
        for lesson in unit["lessons"]:
            out.append({**lesson, "number": len(out) + 1, "unit": unit["title"]})
    return out


def guide_path(lib: Path, number: int) -> Path | None:
    found = sorted((lib / "guides").glob(f"L{number:02d}-*.md"))
    return found[0] if found else None


def lesson_stem(lib: Path, lesson: dict) -> str:
    guide = guide_path(lib, lesson["number"])
    return guide.stem if guide else f"L{lesson['number']:02d}-{slugify(lesson['title'])}"


_SKIP_LINK = re.compile(r"unsplash\.com|flickr\.com|wikimedia\.org|pixabay\.com|pexels\.com|grossmont\.edu|"
                        r"reedleycollege\.edu|linkedin\.com|libretexts\.org|nice\.com|squareup\.com|sites\.google\.com|"
                        r"creativecommons\.org|youtube\.com/redirect|^https?://[^/]+/?$|"
                        # checked October 2026: gsuideas.org now leads to a spam site; Lumen Learning's old
                        # course pages redirect to a commercial tool
                        r"gsuideas\.org|courses\.lumenlearning\.com|3cmediasolutions\.org/privid|"
                        # now gambling sites (checked October 2026)
                        r"neurobonkers\.com|researchblogging\.org|"
                        # parked, closed or private (scripts/check_links.py, October 2026)
                        r"sometimesimwrong\.typepad\.com|fivethirtyeight\.com|fluidsurveys\.com|"
                        r"myscccd-my\.sharepoint\.com|forums\.aws\.amazon\.com|"
                        # footnote anchors on the book's original site
                        r"#(?:return-)?footnote", re.I)
# Links that scripts/check_links.py found dead (404, 410, no server): not printed
DEAD_LINKS_FILE = LIBRARY / "dead-links.txt"
_DEAD_LINKS: set[str] | None = None


def dead_links() -> set[str]:
    global _DEAD_LINKS
    if _DEAD_LINKS is None:
        _DEAD_LINKS = set(DEAD_LINKS_FILE.read_text(encoding="utf-8").split()) if DEAD_LINKS_FILE.exists() else set()
    return _DEAD_LINKS
_SKIP_LINK_TEXT = re.compile(r"^source content$", re.I)    # the editors' working document
# "here", "this video", "Click here", "the article": not a title. Case-sensitive after the first word, so a real
# title ("The Story Behind Dreams") is kept
_GENERIC_TEXT = re.compile(r"(?i:^(here|this|the|his|her|a|an|click))(\s+[a-z][^A-Z]{0,20})?\.?$|(?i:^https?://|^slides?\b)")


def clean_url(url: str) -> str:
    """The real address behind a Google redirect, without tracking parameters; YouTube as youtu.be/ID."""
    parsed = urlparse(url)
    host = parsed.netloc.lower().removeprefix("www.")     # whole host names: "hosted.com" is not TED
    if (host == "google.com" or host.endswith(".google.com")) and parsed.path == "/url":
        url = parse_qs(parsed.query).get("q", [url])[0]
        parsed = urlparse(url)
        host = parsed.netloc.lower().removeprefix("www.")
    url = re.sub(r"&sa=D&usg=\w+$", "", url)          # what is left of a Google redirect copied by hand
    if url.endswith("]") and "[" not in url:          # "[link]" written around the address
        url = url[:-1]
    if host in ("youtube.com", "m.youtube.com") and parse_qs(parsed.query).get("v"):
        return f"https://youtu.be/{parse_qs(parsed.query)['v'][0]}"
    if host in ("ted.com", "ed.ted.com"):
        if parsed.path.startswith("/lessons/"):       # TED-Ed lessons moved to their own site
            return f"https://ed.ted.com{parsed.path}"
        return f"https://www.ted.com{parsed.path}"
    return url


def link_label(text: str, url: str) -> str:
    host = urlparse(url).netloc.removeprefix("www.")
    if not _GENERIC_TEXT.search(text):
        return text.rstrip(".")
    if "youtu" in host:
        return "Video (YouTube)"
    if "ted.com" in host:
        return "TED talk"
    if "esl-lab" in host:
        return "Listening recording and questions (Randall's ESL Cyber Listening Lab)"
    if "docs.google.com/presentation" in url:
        return "Slides (Google Slides)"
    if "docs.google.com/document" in url:
        return "Document (Google Docs)"
    return f"Web page ({host})"


def section_links(path: Path) -> list[tuple[str, str]]:
    """Useful links of a section's web page (saved by fetch_attachments.py): videos, recordings, slides,
    further reading. Photo credits, author pages and site footers are left out."""
    file = path.parent.parent / "links" / f"{path.stem}.txt"
    if not file.exists():
        return []
    out: dict[str, str] = {}
    for line in file.read_text(encoding="utf-8").splitlines():
        text, _, url = line.rpartition(" | ")
        url = watch_url(clean_url(url.strip()))      # a video's page, not its player ("youtube.com/embed/...")
        if "..." in url or "…" in url:          # shortened in the book itself ("ted.com/talks/chimamanda...ry")
            continue
        if url in dead_links():
            continue
        if url and not _SKIP_LINK.search(url) and not _SKIP_LINK_TEXT.search(text.strip()) and url not in out:
            out[url] = link_label(text.strip(), url)
    return [(label, url) for url, label in out.items()]


def remove_older(folder: Path, pattern: str, keep: list[Path]) -> None:
    """Generated files of a lesson whose name changed (the guide's file name sets it)."""
    for old in folder.glob(pattern):
        if old not in keep:
            old.unlink()


_ROMAN = {"I": 1, "V": 5, "X": 10, "L": 50}


def roman(numeral: str) -> int:
    values = [_ROMAN[c] for c in numeral.upper()]
    return sum(-v if i + 1 < len(values) and v < values[i + 1] else v for i, v in enumerate(values))


def select_part(body: str, part: str) -> str:
    """Part of a long section by its numbered main headings: "I-VI" keeps "# I. Warm-Up" up to the end of
    "# VI. ...". Text before the first numbered heading (the unit title) goes with a part that starts at I.
    By heading names instead: "Method..Discussion" keeps the heading that starts with "Method" up to the end of
    the one that starts with "Discussion" (the next heading of the same level ends it)."""
    if ".." in part:
        start, end = (p.strip().lower() for p in part.split("..", 1))
        lines, keep, state, end_level = body.splitlines(), [], "before", 0
        preamble, levels = [], []           # lines before the start heading, and the levels of their headings
        for line in lines:
            heading = re.match(r"^(#+)\s+(.*)$", line)
            name = re.sub(r"[*_]", "", heading.group(2)).strip().lower() if heading else ""
            if state == "before" and heading and name.startswith(start):
                state = "in"
                # The section's first heading of its level: the learning objectives and the introduction above
                # it belong to this part (Stage 4 L34 lost them)
                if all(level > len(heading.group(1)) for level in levels):
                    keep += preamble
            if state == "in" and heading and name.startswith(end):
                state, end_level = "last", len(heading.group(1))
            elif state == "last" and heading and len(heading.group(1)) <= end_level:
                break
            if state != "before":
                keep.append(line)
            else:
                preamble.append(line)
                if heading:
                    levels.append(len(heading.group(1)))
        return "\n".join(keep)
    first, _, last = part.partition("-")
    low, high = roman(first), roman(last or first)
    keep, current = [], 0
    for line in body.splitlines():
        heading = re.match(r"# [*_]*([IVXL]+)\.\s", line)        # also "# **II. Focus of ...**"
        if heading:
            current = roman(heading.group(1))
        if (current == 0 and low == 1) or low <= current <= high:
            keep.append(line)
    return "\n".join(keep)


def read_section(source: str) -> dict:
    """A section file written by import_libretexts.py or import_pressbooks.py (book title, section title, Source,
    License, body), with the files attached to it on the web page (fetch_attachments.py): audio clips and
    document texts. "file.txt#I-VI" takes part of a long section (see select_part)."""
    source, _, part = source.partition("#")
    path = LIBRARY / source
    head, _, body = path.read_text(encoding="utf-8").partition("\n\n")
    lines = head.splitlines()
    # The site's footer ("This page titled ... was last modified on ...") repeats the attribution given above
    body = "\n".join(line for line in body.splitlines() if not line.startswith("This page titled "))
    if part:
        body = select_part(body, part)
    book_dir = path.parent.parent
    folder = book_dir / "attachments" / path.stem
    audio = sorted(p.name for p in folder.glob("*") if p.suffix.lower() in (".m4a", ".mp3", ".wav")) if folder.is_dir() else []
    documents = []                                  # (label, text), e.g. an answer sheet or slides
    texts = book_dir / "attachments" / f"{path.stem}.txt"
    if texts.exists():
        for m in re.finditer(r"(?ms)^--- (.+?) \(([^()]+)\) ---\n(.*?)(?=^--- |\Z)", texts.read_text(encoding="utf-8")):
            if all(d["label"] != m.group(1).strip() for d in documents):    # the same file as .docx and .pdf
                documents.append({"label": m.group(1).strip(), "file": m.group(2), "text": m.group(3).strip()})
    own_license = lines[3].removeprefix("License:").strip() if len(lines) > 3 else ""
    title = lines[1].strip()
    if part:
        title += f" ({part.replace('..', ' to ')})" if ".." in part else f" (part {part})"
    return {"book_key": source.split("/")[0], "title": title,
            "license": "" if own_license.startswith("not declared") else own_license,
            "url": lines[2].removeprefix("Source:").strip(), "body": body.strip(),
            "audio_dir": folder if audio else None, "audio": audio, "documents": documents,
            "links": section_links(path)}


def is_answer_sheet(document: dict) -> bool:
    return bool(re.search(r"answer", document["label"], re.I))


def watch_url(url: str) -> str:
    for pattern, short in _EMBED:
        url = pattern.sub(short, url)
    return url


def clean_inline(line: str) -> str:
    """Drop image placeholders that are only file names, keep described pictures, name audio clips."""
    def image(m: re.Match) -> str:
        alt = m.group(1).strip()
        return "" if _FILE_IMAGE.search(alt) else f"(Picture: {alt})"
    line = re.sub(r"\[Image:\s*([^\]]*)\]", image, line)
    line = re.sub(r"\?\s*(\.(?:m4a|mp3|wav)\b)", r"\1", line)        # "Long or Short Vowel Sound? .m4a"
    line = _AUDIO.sub(lambda m: f' (Audio on the lesson web page: "{m.group(1)}")', line)
    line = line.replace("Figure : ", "Figure: ")
    # An underline around italic text was imported as "__ ***water*** __" (import_libretexts.py, fixed since):
    # bold() did not read that pair, and the stray "__" underlined the wrong phrase up to the next "__".
    # Measured: 23 lines in stages 1-2 (Stage 1 L07, L08; Stage 2 L02, L06, L31, ...). Taken as pairs, so
    # 'the* __ *big* __ *mattress' keeps "big" underlined, not "mattress"; a word right after keeps its space
    line = re.sub(r"(?<!\w)__ (\*(?:(?!__).)*?\*) __(\w?)",
                  lambda m: f"__{m.group(1)}__" + (" " + m.group(2) if m.group(2) else ""), line)
    return " ".join(line.split())


# --- material for the tutor (answers kept) ---------------------------------------------------------------

def material_text(syllabus: dict, lesson: dict) -> str:
    parts = [f"Lesson {lesson['number']:02d} material: {lesson['title']}",
             f"{syllabus['short_title']} · {lesson['unit']}", ""]
    for source in lesson["sources"]:
        sec = read_section(source)
        book = syllabus["books"][sec["book_key"]]
        parts += [f"=== {sec['title']} ===",
                  f"From \"{book['title']}\" by {book['author']} ({book['license']}). "
                  f"This section: {sec['license'] or book['license']}. Original page: {sec['url']}", ""]
        for line in sec["body"].splitlines():
            if line.startswith("[Video:"):
                line = "Video: " + watch_url(line[7:].strip(" ]"))
            elif line.startswith("[Interactive exercise:"):
                line = "(Online slides or activity on the lesson web page.)"
            elif not line.startswith("   "):
                line = clean_inline(line)
            if line.strip():
                parts.append(line)
        for document in sec["documents"]:
            kind = "Answer sheet" if is_answer_sheet(document) else "Attached file"
            parts += ["", f"{kind} for this section: {document['label']}", document["text"]]
        if sec["audio"]:
            # Relative to the project folder, so the text is the same on every computer the project is cloned to
            where = Path(sec["audio_dir"]).resolve().relative_to(ROOT)
            parts += ["", "Audio clips of this section (saved in the project folder, " + str(where) + "): "
                      + "; ".join(sec["audio"])]
        if sec["links"]:
            parts += ["", "Links on this section's web page:"] + [f"- {label}: {url}" for label, url in sec["links"]]
        parts.append("")
    # The book's answer key: for the tutor to check the learner's work (never printed with the lesson material)
    for source in lesson.get("key", []):
        sec = read_section(source)
        parts += [f"=== Answer key from the book: {sec['title']} ===", sec["body"], ""]
    return "\n".join(parts).strip() + "\n"


def write_material(lib: Path, syllabus: dict) -> list[Path]:
    folder = lib / "material"
    folder.mkdir(exist_ok=True)
    paths = []
    for lesson in lessons(syllabus):
        path = folder / f"{lesson_stem(lib, lesson)}.txt"
        remove_older(folder, f"L{lesson['number']:02d}-*.txt", keep=[path])
        text = material_text(syllabus, lesson)
        if not path.exists() or path.read_text(encoding="utf-8") != text:
            path.write_text(text, encoding="utf-8")
        paths.append(path)
    return paths


# --- print ---------------------------------------------------------------------------------------------------

EXTRA_CSS = """
blockquote { margin: 8pt 0; padding: 6pt 10pt; border-left: 3pt solid #2e6b5f; background: #f1f6f4; }
blockquote p { margin: 0 0 3pt; }
code { font: 10pt Consolas, monospace; }
.source-title { break-before: page; margin-top: 0; }
.source-title.first { break-before: avoid; margin-top: 14pt; }
.exercise-label { font: 600 9.5pt "Segoe UI", sans-serif; color: #2e6b5f; text-transform: uppercase;
                  letter-spacing: .04em; margin: 10pt 0 3pt; }
.q { margin: 5pt 0 1pt; }
.opts { margin: 0 0 4pt 16pt; font-size: 10pt; }
.task { font-weight: 600; margin-top: 6pt; }
.cloze { line-height: 2.0; }
.online, .video { font-size: 10pt; color: #333; }
.audio { font-size: 9.5pt; padding: 4pt 8pt; background: #f4f4ee; border: 0.6pt solid #d8d8cc; }
.audio code { font-size: 8.5pt; word-break: break-all; }
.links { font-size: 9.5pt; }
.li { margin: 0 0 2pt; padding-left: 16pt; text-indent: -16pt; }
.li.d1 { margin-left: 18pt; }
.li .mk { display: inline-block; min-width: 14pt; text-indent: 0; font-family: "Segoe UI", sans-serif; font-size: 9.5pt; }
.url, a { color: #1b1b1b; text-decoration: none; word-break: break-all; font-size: 9pt; }
.video { font-family: "Segoe UI", sans-serif; }
"""


def print_page(title: str, body: str, course: str) -> str:
    from build_print import CSS
    css = CSS % {"course": course.replace('"', "")} + EXTRA_CSS
    return (f"<!doctype html><html lang='en'><head><meta charset='utf-8'><title>{html.escape(title)}</title>"
            f"<style>{css}</style></head><body>{body}</body></html>")


def bold(escaped: str) -> str:
    """The book's emphasis marks (see import_libretexts.ContentParser) as type: **bold**, __underlined__ (never a
    run of underscores, which is a blank to fill in), ~~crossed out~~."""
    escaped = re.sub(r"\*\*\*([^*]+?)\*\*\*", r"<b><i>\1</i></b>", escaped)
    escaped = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", escaped)
    # *italic*, also inside an underline ("__*Pixaby*__"): "_" next to it is a mark, not a letter
    escaped = re.sub(r"(?<![^\W_]|\*)\*(?=[^\s*])([^*]+?)(?<=[^\s*])\*(?![^\W_]|\*)", r"<i>\1</i>", escaped)
    escaped = re.sub(r"(?<!_)__(?=[^_\s])(.+?)(?<=[^_\s])__(?!_)", r"<u>\1</u>", escaped)
    return re.sub(r"~~(.+?)~~", r"<s>\1</s>", escaped)


def plain(text: str) -> str:
    """Text without emphasis marks (headings are bold already)."""
    text = re.sub(r"(?<![^\W_]|\*)\*(?=[^\s*])(.+?)(?<=[^\s*])\*(?![^\W_]|\*)", r"\1",
                  text.replace("**", "").replace("~~", ""))
    return re.sub(r"(?<!_)__(?=[^_\s])(.+?)(?<=[^_\s])__(?!_)", r"\1", text)


def options_html(options: list[str]) -> str:
    # In the file's order: the book's order, or for quizzes that store the answer first, the fixed order the
    # importer gave them (import_libretexts.h5p_to_text), so the guide's letters are the material's letters
    options = [re.sub(r"^(\d+(?:st|nd|rd|th) syllable):\s*", r"\1: ", o) for o in options]   # "2nd syllable:real"
    letters = "abcdefghij"
    return "<p class='opts'>" + " &nbsp;&nbsp; ".join(
        f"{letters[i]}) {bold(html.escape(o))}" for i, o in enumerate(options)) + "</p>"


def section_html(sec: dict, book: dict, first: bool) -> str:
    out = [f"<h2 class='source-title{' first' if first else ''}'>{html.escape(sec['title'])}</h2>",
           f"<p class='meta'>From <em>{html.escape(book['title'])}</em> by {html.escape(book['author'])} "
           f"({html.escape(book['license'])}). This section: {html.escape(sec['license'] or book['license'])}."
           f"<br>Original page, with audio and online exercises: "
           f"{html.escape(sec['url'])}<br>Changes: formatted for print; exercise answers are in the study guide.</p>"]
    if sec["audio"]:
        out.append("<p class='audio'><b>Audio for this section</b> is saved on your computer in "
                   f"<code>{html.escape(str(sec['audio_dir']))}</code>: "
                   + "; ".join(html.escape(unquote(a.rsplit('.', 1)[0]).replace('_', ' ')) for a in sec["audio"]) + "</p>")
    pending: list[str] = []     # consecutive list items or table rows
    kind = None
    question = 0
    marking = False             # the current task asks to highlight words, so its [marks] are not blanks

    def flush():
        nonlocal pending, kind
        if kind == "li":
            out.append("<ul>" + "".join(f"<li>{x}</li>" for x in pending) + "</ul>")
        elif kind == "tr":
            out.append("<table>" + "".join(pending) + "</table>")
        pending, kind = [], None

    for raw in sec["body"].splitlines():
        line = raw.rstrip()
        if not line.strip():
            continue
        if raw.startswith("   "):                                  # lines of an interactive exercise
            body = line.strip()
            if body.startswith(("Answer:", "Sample answer:")):
                continue
            flush()
            if body.startswith("Options:"):
                out.append(options_html([o.strip() for o in body[8:].split("|")]))
                continue
            # Choices in brackets, "(noun, verb, [adverb])": the answer is one of the printed choices
            body = re.sub(r"\(([^()]*\[[^()]*)\)", lambda m: "(" + re.sub(r"\[([^\]]+)\]", r"\1", m.group(1)) + ")"
                          if re.search(r"[,/]", m.group(1)) else m.group(0), body)
            if marking or re.match(r"(Highlight|Click|Mark|Underline|Circle)\b", body):
                body = re.sub(r"\[([^\]]+)\]", r"\1", body)
            else:
                body = re.sub(r"\[[^\]]+\]", "\x00", body)
            body = bold(html.escape(clean_inline(body))).replace("\x00", "__________")
            body = re.sub(r"\s(?=\d{1,2}\.\s)", "<br>", body)      # "1. ... 2. ..." on separate lines
            out.append(f"<p class='cloze'>{body}</p>")
            continue
        if line.startswith("| "):
            if kind != "tr":
                flush()
                kind = "tr"
            cells = [bold(html.escape(clean_inline(c))) for c in line.strip("| ").split(" | ")]
            pending.append("<tr>" + "".join(f"<td>{c}</td>" for c in cells) + "</tr>")
            continue
        if line.startswith("- "):
            if kind != "li":
                flush()
                kind = "li"
            pending.append(bold(html.escape(clean_inline(line[2:]))))
            continue
        # Numbered and lettered list items ("1. question", "  a. option") keep their marks: answer keys use them
        item = re.match(r"^( {2})?(-|\d{1,3}|[a-zA-Z]|[ivx]{1,4})\. (.*)$", line) or re.match(r"^( {2})(-) (.*)$", line)
        if item:
            flush()
            mark = "•" if item.group(2) == "-" else item.group(2) + "."
            depth = " d1" if item.group(1) else ""
            out.append(f"<p class='li{depth}'><span class='mk'>{html.escape(mark)}</span> "
                       f"{bold(html.escape(clean_inline(item.group(3))))}</p>")
            continue
        if line.startswith("Card:"):                            # vocabulary cards: word -> meaning
            word, _, meaning = line[5:].partition("->")
            if kind != "li":
                flush()
                kind = "li"
            pending.append(f"<b>{html.escape(plain(word.strip()))}</b>: {bold(html.escape(meaning.strip()))}")
            continue
        flush()
        if re.match(r"#+\s", line):        # a heading; "#1 ..." labels a model paragraph (esl-b2-writing)
            level = len(line) - len(line.lstrip("#"))
            tag = "h3" if level <= 2 else "h4"
            heading = plain(clean_inline(line.lstrip("# ")))
            if heading.lower() not in ("query", "multiple choice: interactive"):   # H5P widget titles
                out.append(f"<{tag}>{html.escape(heading)}</{tag}>")
        elif line == "[Interactive exercise]":
            question = 0
            marking = False
            out.append("<p class='exercise-label'>Exercise</p>")
        elif line.startswith("[Interactive exercise:"):
            out.append("<p class='online'>(Online slides or activity: open the original page above.)</p>")
        elif line.startswith("[Video:"):
            out.append(f"<p class='video'>Video: {html.escape(watch_url(line[7:].strip(' ]')))}</p>")
        elif line.startswith("Q (true or false):"):
            question += 1
            out.append(f"<p class='q'><b>{question}.</b> {bold(html.escape(line[18:].strip()))} &nbsp; (True / False)</p>")
        elif line.startswith("Q:"):
            question += 1
            out.append(f"<p class='q'><b>{question}.</b> {bold(html.escape(line[2:].strip()))}</p>")
        elif line.startswith("Task:"):
            task = line[5:].strip()
            marking = bool(re.search(r"\b(highlight|mark|click)\b", task, re.I))
            if task.strip(" .:"):                              # some exercises have only "." as their task
                out.append(f"<p class='task'>{html.escape(task)}</p>")
        elif line.startswith("Writing task:"):
            out.append(f"<p class='task'>{html.escape(line)}</p>")
        elif line.startswith("Choose the correct statement:"):
            out.append("<p class='task'>Choose the correct statement.</p>")
            out.append(options_html([o.strip() for o in line[29:].split("|")]))
        else:
            text = clean_inline(line)
            if text:
                out.append(f"<p>{bold(html.escape(text))}</p>")
    flush()
    if sec["links"]:
        out.append("<h3>Links on the original page</h3>")
        out.append("<ul class='links'>" + "".join(f"<li>{html.escape(label)}: <span class='url'>{html.escape(url)}"
                                                  f"</span></li>" for label, url in sec["links"]) + "</ul>")
    for document in sec["documents"]:
        if is_answer_sheet(document):           # answers belong to the study guide's answer key
            continue
        out.append(f"<h3>Attached: {html.escape(document['label'])}</h3>")
        if len(document["text"].split()) > PRINT_ATTACHMENT_WORDS:
            out.append(f"<p class='small'>A long document ({len(document['text'].split()):,} words), not printed here. "
                       f"Open it from the original page above or from your computer's attachments folder "
                       f"(file: {html.escape(document['file'])}). Your tutor has its full text.</p>")
            continue
        out += [f"<p>{html.escape(line)}</p>" for line in document["text"].splitlines() if line.strip()]
    return "\n".join(out)


def material_html(syllabus: dict, lesson: dict) -> str:
    out = [f"<h1>Lesson {lesson['number']:02d} material: {html.escape(lesson['title'])}</h1>",
           f"<p class='meta'>{html.escape(syllabus['short_title'])} · {html.escape(lesson['unit'])}</p>"]
    for i, source in enumerate(lesson["sources"]):
        sec = read_section(source)
        out.append(section_html(sec, syllabus["books"][sec["book_key"]], first=i == 0))
    return "\n".join(out)


def guide_html(md_text: str) -> str:
    """The guide, with its answer key moved to a page of its own at the end (it can be kept face down while
    practising), then a page of lines for notes."""
    import markdown
    from build_print import normalize_md, prepare_md
    parts = re.split(r"(?m)^(?=## )", md_text)
    answers = [p for p in parts if p.startswith("## Answer key")]
    main = "".join(p for p in parts if not p.startswith("## Answer key"))
    # sane_lists: an answer key that starts at "3." keeps its numbers, and a bullet list after a numbered one
    # starts a new list instead of joining it
    def render(text: str) -> str:
        out = markdown.markdown(normalize_md(prepare_md(text)), extensions=["tables", "sane_lists"])
        out = re.sub(r"<li>(\s*<p>)?(\d+)\s*", lambda m: f'<li value="{m.group(2)}">{m.group(1) or ""}', out)
        return re.sub(r"\d+\s*", "", out)
    body = render(main)
    if answers:
        body += "<section class='answers'>" + render("".join(answers)) + "</section>"
    return body + "<section class='notes'><h2>My notes</h2>" + "<div class='line'></div>" * 26 + "</section>"


def pdf_pages(path: Path) -> int:
    from pypdf import PdfReader
    return len(PdfReader(str(path)).pages)


def overview_html(syllabus: dict, rows: list[dict]) -> str:
    books = "".join(f"<li><em>{html.escape(b['title'])}</em>, {html.escape(b['author'])} ({html.escape(b['license'])})"
                    f"<br><span class='small'>{html.escape(b['url'])}</span></li>"
                    for b in syllabus["books"].values())
    table, week, unit = [], 0, None
    for unit_title in dict.fromkeys(r["unit"] for r in rows):
        unit_rows = [r for r in rows if r["unit"] == unit_title]
        weeks = -(-len(unit_rows) // LESSONS_PER_WEEK)
        per_week = -(-len(unit_rows) // weeks)
        table.append(f"<tr><th colspan='4'>{html.escape(unit_title)}</th></tr>")
        for i, r in enumerate(unit_rows):
            if i % per_week == 0:
                week += 1
            files = ", ".join(f"{name} ({pages} p.)" for name, pages in r["print"])
            table.append(f"<tr><td>{week}</td><td>{r['number']:02d}</td><td>{html.escape(r['title'])}</td>"
                         f"<td class='small'>{html.escape(files)}</td></tr>")
    total = sum(p for r in rows for _, p in r["print"])
    return f"""
<section class='cover'>
  <h1>{html.escape(syllabus['title'])}</h1>
  <p>{html.escape(syllabus['level'])} · {len({r['unit'] for r in rows})} units · {len(rows)} lessons · about {week} weeks</p>
  <p class='small'>Study pack for personal study with a local AI tutor. {html.escape(syllabus['books_intro'])}</p>
  <ul class='small'>{books}</ul>
  <p class='small'>The books are openly licensed; the guides and the formatting are adaptations for personal study.
  Audio clips and online exercises are on the original web pages (each lesson material shows the link).</p>
</section>
<h2>How to study each lesson</h2>
<ol>
  <li><strong>Read the study guide</strong> up to "Practice": goals, key language and rules (about 15 minutes).</li>
  <li><strong>Work through the lesson material</strong>: read the texts, watch the videos, and listen to the audio on the
  original page when the material asks you to listen.</li>
  <li><strong>Do the Practice</strong> in the guide, then check your answers on the answer key page (the last page
  before your notes).</li>
  <li><strong>Speak with your tutor</strong> (about 20 to 30 minutes). In the app, open <em>Study</em>, choose
  <em>{html.escape(syllabus['short_title'])}</em> and the lesson's topic, and start <em>Live conversation</em>. Use the
  ideas in "Speak with your tutor". Ask the tutor to correct your grammar and pronunciation.</li>
  <li><strong>Read aloud and write.</strong> Record yourself reading the "Read aloud" lines with your phone and listen
  back. Write the writing task, paste it into the chat, and ask the tutor for corrections. Then rewrite it.</li>
  <li><strong>Review the next day.</strong> The app's <em>Today</em> page shows the lessons and words that are due.</li>
</ol>
<h2>Study plan ({LESSONS_PER_WEEK} lessons a week)</h2>
<table><tr><th>Week</th><th>Lesson</th><th>Title</th><th>Files to print</th></tr>{''.join(table)}</table>
<p class='small'>Total: about {total} printed pages. A project or review lesson takes longer: give it a full study day.
{html.escape(syllabus['after'])}</p>
"""


def build_print(lib: Path, syllabus: dict) -> None:
    from build_print import to_pdf
    out = lib / "print"
    out.mkdir(exist_ok=True)
    course = syllabus["short_title"]
    rows = []
    (ROOT / "cache").mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(dir=ROOT / "cache") as tmp:      # on E:, like every other cache
        tmp = Path(tmp)
        for lesson in lessons(syllabus):
            stem = lesson_stem(lib, lesson)
            heading = f"Lesson {lesson['number']:02d}: {lesson['title']}"
            remove_older(out, f"L{lesson['number']:02d}-*.pdf", keep=[out / f"{stem}-1-study-guide.pdf",
                                                                     out / f"{stem}-2-lesson-material.pdf"])
            printed = []
            guide = guide_path(lib, lesson["number"])
            if guide:
                target = out / f"{stem}-1-study-guide.pdf"
                to_pdf(print_page(heading, guide_html(guide.read_text(encoding="utf-8")), course), target, tmp)
                printed.append(("study guide", pdf_pages(target)))
            target = out / f"{stem}-2-lesson-material.pdf"
            to_pdf(print_page(heading, material_html(syllabus, lesson), course), target, tmp)
            printed.append(("lesson material", pdf_pages(target)))
            rows.append({**lesson, "print": printed})
            print(f"{stem}: " + ", ".join(f"{n} {p} p." for n, p in printed))
        to_pdf(print_page(syllabus["title"], overview_html(syllabus, rows), course),
               out / "00-course-overview-and-plan.pdf", tmp)
    print(f"\nPrint folder: {out}")


# --- load into the app ---------------------------------------------------------------------------------------

def load(lib: Path, syllabus: dict) -> None:
    import httpx
    write_material(lib, syllabus)
    api = httpx.Client(base_url=BASE, headers={"Origin": BASE}, timeout=300)
    name = syllabus["short_title"]
    course = next((c for c in api.get("/api/courses").json() if c["name"] == name), None)
    if not course:
        books = "; ".join(f"{b['title']} ({b['author']}, {b['license']})" for b in syllabus["books"].values())
        course = api.post("/api/courses", json={"name": name, "description": f"{syllabus['description']} "
                                                                            f"Books: {books}."}).json()
    cid = course["id"]
    docs = {d["filename"]: d for d in api.get(f"/api/courses/{cid}/documents").json()}
    topics = {t["title"]: t for t in api.get(f"/api/courses/{cid}/topics").json()}

    def upload(path: Path, mime: str) -> int | None:
        data = path.read_bytes()
        old = docs.get(path.name)
        if old and old.get("sha256") == hashlib.sha256(data).hexdigest():
            return old["id"]
        if old:                                   # the file changed: replace it
            api.delete(f"/api/documents/{old['id']}").raise_for_status()
        result = api.post(f"/api/courses/{cid}/documents", files=[("files", (path.name, data, mime))]).json()[0]
        if not result["ok"]:
            print(f"  ! {path.name}: {result['error']}")
            return None
        docs[path.name] = result["document"]
        return result["document"]["id"]

    for lesson in lessons(syllabus):
        title = f"Lesson {lesson['number']:02d}: {lesson['title']}"
        topic = topics.get(title) or api.post(f"/api/courses/{cid}/topics", json={"title": title}).json()
        stem = lesson_stem(lib, lesson)
        files = [(guide_path(lib, lesson["number"]), "text/markdown"), (lib / "material" / f"{stem}.txt", "text/plain")]
        linked = []
        for path, mime in files:            # the guide first: a lesson that starts from scratch opens with it
            if path and path.exists():
                doc_id = upload(path, mime)
                if doc_id is not None:
                    linked.append(doc_id)
        alive = {d["id"] for d in docs.values()}
        current = [d for d in topic.get("document_ids", []) if d in alive]
        if current[:len(linked)] != linked:       # links are kept in order: guide, material, then anything added
            extras = [d for d in current if d not in linked]
            for doc_id in current:
                api.delete(f"/api/topics/{topic['id']}/documents/{doc_id}").raise_for_status()
            for doc_id in linked + extras:
                api.put(f"/api/topics/{topic['id']}/documents/{doc_id}").raise_for_status()
        print(f"{title}: {len(linked)} file(s) linked")
    print(f"\nCourse '{name}' (id {cid}) is ready in the app.")


def main(name: str, action: str) -> None:
    lib = LIBRARY / name
    syllabus = json.loads((lib / "syllabus.json").read_text(encoding="utf-8"))
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    if action == "print":
        write_material(lib, syllabus)
        build_print(lib, syllabus)
    elif action == "load":
        load(lib, syllabus)
    elif action == "material":
        for path in write_material(lib, syllabus):
            print(path.name)
    else:
        sys.exit("action must be print, load or material")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
