"""Build printable PDFs for a course in materials/library/<name>: a course overview with a study plan, and per
lecture a study guide (with an answer page and a notes page), the formatted transcript, and the original slides.

    uv run --with markdown python scripts/build_print.py psyc-110

PDFs are rendered by Chrome (headless) from HTML with print CSS, into materials/library/<name>/print/.
"""

import html
import json
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import markdown

ROOT = Path(__file__).resolve().parent.parent
CHROME = Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe")
LECTURES_PER_WEEK = 2

CSS = """
@page { size: A4; margin: 18mm 17mm 20mm;
        @bottom-center { content: "Page " counter(page); font: 9pt "Segoe UI", sans-serif; color: #777; }
        @bottom-left { content: "%(course)s"; font: 9pt "Segoe UI", sans-serif; color: #777; } }
* { box-sizing: border-box; }
body { font: 11pt/1.55 Georgia, "Times New Roman", serif; color: #1b1b1b; margin: 0;
       -webkit-print-color-adjust: exact; print-color-adjust: exact; }
h1 { font: 600 20pt/1.25 "Segoe UI", sans-serif; margin: 0 0 4pt; color: #1f4f46; }
h2 { font: 600 13.5pt/1.3 "Segoe UI", sans-serif; margin: 16pt 0 6pt; padding-bottom: 3pt;
     border-bottom: 1.5pt solid #2e6b5f; color: #1f4f46; break-after: avoid; }
h3 { font: 600 11.5pt/1.3 "Segoe UI", sans-serif; margin: 11pt 0 4pt; break-after: avoid; }
p { margin: 0 0 6pt; }
h1 + p em, h1 + p + p em { font-size: 9.5pt; color: #555; }
ul, ol { margin: 0 0 6pt; padding-left: 18pt; }
li { margin-bottom: 3pt; }
table { width: 100%%; border-collapse: collapse; font-size: 9.8pt; margin: 4pt 0 8pt; }
th, td { border: 0.6pt solid #b9c4c0; padding: 4pt 5pt; vertical-align: top; text-align: left; }
th { background: #e6efec; font-family: "Segoe UI", sans-serif; }
tr { break-inside: avoid; }
.answers { break-before: page; }
.notes { break-before: page; }
.notes .line { height: 8.5mm; border-bottom: 0.5pt solid #aab6b2; }
.speaker { font-family: "Segoe UI", sans-serif; font-weight: 600; font-size: 10pt; color: #1f4f46; }
.meta { font-size: 9.5pt; color: #555; margin-bottom: 10pt; }
.cover { padding-top: 40mm; }
.cover h1 { font-size: 26pt; }
.small { font-size: 9.5pt; color: #555; }
a { color: inherit; text-decoration: none; }   /* prepare_md links bare URLs; on paper they read as text */
"""


def page(title: str, body: str, course: str) -> str:
    return (f"<!doctype html><html lang='en'><head><meta charset='utf-8'><title>{html.escape(title)}</title>"
            f"<style>{CSS % {'course': course.replace(chr(34), '')}}</style></head><body>{body}</body></html>")


def to_pdf(doc: str, target: Path, workdir: Path) -> None:
    src = workdir / (target.stem + ".html")
    src.write_text(doc, encoding="utf-8")
    subprocess.run([str(CHROME), "--headless=new", "--disable-gpu", "--no-pdf-header-footer",
                    f"--user-data-dir={workdir / 'chrome'}", f"--print-to-pdf={target}", src.as_uri()],
                   check=True, capture_output=True, timeout=120)


_LIST_ITEM = re.compile(r"\s*(?:[-+]|\*(?=\s)|\d+\.)\s+\S")
_META_LINE = re.compile(r"\*[^*\s]")   # an italic line such as "*Source: ...*"


def normalize_md(text: str) -> str:
    """Python-Markdown needs a blank line before a list, and joins consecutive lines into one paragraph.
    Measured on the first printed guide: "able to:" followed by "- ..." items printed as one run-on line,
    and the two italic lines under the title were merged."""
    out: list[str] = []
    for line in text.splitlines():
        prev = out[-1] if out else ""
        if _LIST_ITEM.match(line) and prev.strip() and not _LIST_ITEM.match(prev):
            out.append("")
        elif _META_LINE.match(line) and _META_LINE.match(prev):
            out.append("")
        out.append(line)
    return "\n".join(out)


_NESTED_ITEM = re.compile(r"^( +)(?=(?:[-*+]|\d+[.)])\s)")
_BARE_URL = re.compile(r"(?<!\]\()(?<!<)\bhttps?://[^\s<>|]+")


def _autolink(m: re.Match) -> str:
    url, trail = m.group(), ""
    while url and (url[-1] in ".,;:!?*" or (url[-1] == ")" and url.count("(") < url.count(")"))):
        url, trail = url[:-1], url[-1] + trail
    return f"<{url}>{trail}"


def prepare_md(text: str) -> str:
    """Markdown as the guides are written, made safe for Python-Markdown (each found in a printed guide):
    nested list items indented by 2-3 spaces were flattened; "____ ____" blanks became italics; underscores in
    plain URLs (LibreTexts addresses) became italics."""
    out = []
    previous = None                                        # (indent, numbered) of the last list item
    for line in text.splitlines():
        line = re.sub(r"^(\s*\d+[.)])$", r"\1 ", line)        # "6." alone, with its parts nested below it
        indent = _NESTED_ITEM.match(line)
        if indent and len(indent.group(1)) % 4:
            line = " " * (4 * -(-len(indent.group(1)) // 4)) + line.lstrip(" ")
        # Each numbered item keeps its own number (an answer key may skip one): marked here, set in guide_html
        line = re.sub(r"^( *)(\d+)([.)])\s+", lambda m: f"{m.group(1)}{m.group(2)}{m.group(3)} {m.group(2)}",
                      line)
        item = re.match(r"^( *)(?:([-*+])|\d+[.)])\s", line)
        if item:
            current = (len(item.group(1)), item.group(2) is None)
            # A bullet list straight after a numbered one (or the reverse) would be joined to it
            if previous and current[1] != previous[1] and current[0] <= previous[0] and out and out[-1].strip():
                out.append("")
            previous = current
        elif not line.strip():
            previous = None
        pieces = re.split(r"(<https?://[^>]+>)", _BARE_URL.sub(_autolink, line))
        out.append("".join(p if p.startswith("<http") else re.sub(r"(?<!\\)_{2,}", lambda m: "\\_" * len(m.group()), p)
                           for p in pieces))
    return "\n".join(out)


def guide_html(md_text: str) -> str:
    body = markdown.markdown(normalize_md(prepare_md(md_text)), extensions=["tables"])
    body = re.sub(r"<li>(\s*<p>)?(\d+)\s*", lambda m: f'<li value="{m.group(2)}">{m.group(1) or ""}', body)
    body = re.sub(r"\d+\s*", "", body)       # a number prepare_md marked that is not a list item
    # The answer guide goes on its own page, so it can be covered while answering
    body = body.replace("<h2>Answer guide</h2>", "<section class='answers'><h2>Answer guide</h2>", 1)
    body = body.replace("<h2>English practice</h2>", "</section><h2>English practice</h2>", 1)
    # Ruled lines as borders: Chrome prints borders reliably, while background gradients came out blank
    return body + "<section class='notes'><h2>My notes</h2>" + "<div class='line'></div>" * 26 + "</section>"


def transcript_html(text: str, heading: str) -> str:
    head, _, rest = text.partition("\n\n")
    title_lines = head.splitlines()
    out = [f"<h1>{html.escape(heading)} <span class='small'>(full transcript)</span></h1>",
           "<p class='meta'>" + "<br>".join(html.escape(x) for x in title_lines[2:]) + "</p>"]
    for block in re.split(r"\n\s*\n", rest.strip()):
        block = block.strip()
        if block.startswith("## "):
            out.append(f"<h2>{html.escape(block[3:])}</h2>")
        elif block.startswith("Overview:"):
            out.append(f"<p><em>{html.escape(block)}</em></p>")
        elif block:
            speaker = re.match(r"^([A-Z][\w .'-]{2,40}):\s", block)
            if speaker:
                block = (f"<span class='speaker'>{html.escape(speaker.group(1))}:</span> "
                         + html.escape(block[speaker.end():]))
            else:
                block = html.escape(block)
            out.append(f"<p>{block}</p>")
    return "\n".join(out)


def overview_html(meta: dict, files: list[dict]) -> str:
    rows = []
    for i, f in enumerate(files):
        week = i // LECTURES_PER_WEEK + 1
        rows.append(f"<tr><td>{week}</td><td>{html.escape(f['heading'])}</td>"
                    f"<td class='small'>{html.escape(', '.join(f['print']))}</td></tr>")
    return f"""
<section class='cover'>
  <h1>{html.escape(meta['title'])}</h1>
  <p>Yale University · Professor Paul Bloom · Spring 2007</p>
  <p class='small'>Study pack for personal study with a local AI tutor.<br>
  Source: {html.escape(meta['url'])}<br>License of the original material: {html.escape(meta['license'])}.</p>
</section>
<h2>How to study each lecture</h2>
<ol>
  <li><strong>Read the study guide</strong> (about 20 minutes). Underline new words. Try the questions before you look at the answer page.</li>
  <li><strong>Talk with your tutor</strong> (about 30 minutes). In the app, open <em>Study</em>, choose this course and the lecture's topic, and start <em>Live conversation</em>. Use the lines in "Study with your tutor".</li>
  <li><strong>Read or listen to the full lecture</strong> when you want more depth. The transcript PDF has every word, by chapter.</li>
  <li><strong>Review the next day.</strong> The app's <em>Today</em> page shows the topics and words that are due.</li>
</ol>
<h2>Study plan ({LECTURES_PER_WEEK} lectures a week)</h2>
<table><tr><th>Week</th><th>Lecture</th><th>Files to print</th></tr>{''.join(rows)}</table>
<p class='small'>Guest lectures 1 and 2 have no published transcript; their short guides are based on the official overview only.</p>
"""


def main(name: str) -> None:
    lib = ROOT / "materials" / "library" / name
    meta = json.loads((lib / "course.json").read_text(encoding="utf-8"))
    out = lib / "print"
    out.mkdir(exist_ok=True)
    files = []
    (ROOT / "cache").mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(dir=ROOT / "cache") as tmp:      # on E:, like every other cache
        tmp = Path(tmp)
        for lec in meta["lectures"]:
            stem = Path(lec["file"]).stem
            guide = lib / "guides" / f"{stem}.md"
            printed = []
            heading = re.sub(r"\s*\(Guest Lecture by [^)]*\)", "",
                             re.sub(r"^((?:Guest )?Lecture(?: \d+)?) - ", r"\1: ", lec["title"]))
            if guide.exists():
                text = guide.read_text(encoding="utf-8")
                to_pdf(page(heading, guide_html(text), meta["title"]), out / f"{stem}-1-study-guide.pdf", tmp)
                printed.append("study guide")
            if lec.get("has_transcript"):
                text = (lib / lec["file"]).read_text(encoding="utf-8")
                to_pdf(page(heading, transcript_html(text, heading), meta["title"]), out / f"{stem}-2-transcript.pdf", tmp)
                printed.append("transcript")
            for slide in lec.get("slide_files", []):
                shutil.copyfile(lib / slide, out / f"{stem}-3-slides.pdf")
                printed.append("slides")
            files.append({"heading": heading, "print": printed})
            print(f"{stem}: {', '.join(printed) or 'nothing (no guide yet)'}")
        to_pdf(page(meta["title"], overview_html(meta, files), meta["title"]), out / "00-course-overview-and-plan.pdf", tmp)
    print(f"\nPrint folder: {out}")


if __name__ == "__main__":
    main(sys.argv[1])
