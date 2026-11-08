"""Download an Open Yale Courses course for the tutor: lecture list, overviews, chapters, readings,
full transcripts and slide PDFs.

    uv run python scripts/import_oyc.py https://oyc.yale.edu/introduction-psychology/psyc-110 psyc-110

Writes materials/library/<name>/: course.json, transcripts/NN-<slug>.txt, slides/NN-*.pdf.
Open Yale Courses are published under CC BY-NC-SA 3.0: personal, non-commercial use with attribution.
"""

import html
import json
import re
import sys
import time
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urljoin

import httpx

ROOT = Path(__file__).resolve().parent.parent
LICENSE = "Creative Commons Attribution-NonCommercial-ShareAlike 3.0 (Open Yale Courses)"


def text_of(fragment: str) -> str:
    """Plain text of an HTML fragment, with whitespace collapsed (the site's titles span several lines)."""
    text = " ".join(html.unescape(re.sub(r"<[^>]+>", " ", fragment)).split())
    return re.sub(r"\s+([.,;:)])", r"\1", text)   # "Reader ." -> "Reader." (a tag sat before the stop)


class TranscriptParser(HTMLParser):
    """Chapter headings (h3) and paragraphs (p) of a transcript. The site nests <h3> inside <p>,
    which a regular expression cannot handle reliably, so the document is walked tag by tag."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.blocks: list[tuple[str, str]] = []
        self.kind: str | None = None
        self.buf: list[str] = []

    def _flush(self):
        text = " ".join("".join(self.buf).split())
        if self.kind and text:
            self.blocks.append((self.kind, text))
        self.buf = []

    def handle_starttag(self, tag, attrs):
        if tag in ("h3", "p"):
            self._flush()
            self.kind = "chapter" if tag == "h3" else "para"

    def handle_endtag(self, tag):
        if tag in ("h3", "p"):
            self._flush()
            self.kind = "para"

    def handle_data(self, data):
        if self.kind:
            self.buf.append(data)

    def close(self):
        super().close()
        self._flush()


def slugify(title: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")[:60]


def parse_lecture(page: str, url: str) -> dict:
    title = text_of(re.search(r'<h2 class="session-full__title">(.*?)</h2>', page, re.S).group(1))
    body = re.search(r'<div class="session-full__body">(.*?)</div>', page, re.S)
    overview = " ".join(text_of(p) for p in re.findall(r"<p>(.*?)</p>", body.group(1), re.S)) if body else ""
    assignment = re.search(r'field--name-field-session-assignment[^>]*>(.*?)</div>', page, re.S)
    readings = []
    for p in re.findall(r"<p>(.*?)</p>", assignment.group(1), re.S) if assignment else []:
        # Some lectures list several readings in one paragraph, separated by <br><br>
        readings += [text_of(part) for part in re.split(r"<br\s*/?>", p) if text_of(part)]
    resources = re.search(r'block-views-blocksession-resource-files-block-1"(.*?)</div>\s*</div>\s*</div>\s*</div>', page, re.S)
    pdfs = [urljoin(url, h) for h in re.findall(r'href="([^"]+\.pdf)"', resources.group(1) if resources else page)]
    audio = [urljoin(url, h) for h in re.findall(r'href="([^"]+\.mp3)"', page)]

    inline = re.search(r'<div id="inline_content">(.*?)<a [^>]*href="#transcript-top"', page, re.S)
    parser = TranscriptParser()
    parser.feed(inline.group(1) if inline else "")
    parser.close()
    lines, chapters = [], []
    for kind, text in parser.blocks:
        if kind == "chapter":
            chapters.append(text)
            lines.append(f"\n## {text}\n")
        elif not text.startswith("[end of transcript]") and not re.fullmatch(r"(Guest )?Lecture.*", text):
            lines.append(text + "\n")
    return {"title": title, "url": url, "overview": overview, "readings": readings, "chapters": chapters,
            "slides": pdfs, "audio": audio[0] if audio else None, "transcript": "\n".join(lines).strip()}


def main(course_url: str, name: str) -> None:
    out = ROOT / "materials" / "library" / name
    (out / "transcripts").mkdir(parents=True, exist_ok=True)
    (out / "slides").mkdir(parents=True, exist_ok=True)
    for old in (out / "transcripts").glob("*.txt"):   # transcripts/ is rebuilt by every run
        old.unlink()
    client = httpx.Client(headers={"User-Agent": "personal-tutor (personal study)"}, follow_redirects=True, timeout=60)

    course_page = client.get(course_url).text
    course_title = text_of(re.search(r"<title>(.*?)\|", course_page, re.S).group(1))
    links = list(dict.fromkeys(re.findall(r'href="(/[^"]+/(?:lecture|guest-lecture)-\d+)"', course_page)))
    lectures = []
    for n, path in enumerate(links, 1):
        url = urljoin(course_url, path)
        lecture = parse_lecture(client.get(url).text, url)
        lecture["number"] = n
        lecture["has_transcript"] = bool(lecture["transcript"])
        stem = f"{n:02d}-{slugify(re.sub(r'^(Guest )?Lecture( \d+)? - ', '', lecture['title']))}"
        lecture["file"] = f"transcripts/{stem}.txt"
        header = (f"{course_title}\n{lecture['title']}\nSource: {url}\nLicense: {LICENSE}\n\n"
                  f"Overview: {lecture['overview']}\n")
        body = lecture.pop("transcript") or "(Open Yale Courses publishes no transcript for this lecture; only the overview above.)"
        (out / lecture["file"]).write_text(header + "\n" + body + "\n", encoding="utf-8")
        saved = []
        for i, pdf in enumerate(lecture["slides"], 1):
            target = out / "slides" / f"{n:02d}-slides{'' if i == 1 else f'-{i}'}.pdf"
            if not target.exists():
                target.write_bytes(client.get(pdf).content)
            saved.append(str(target.relative_to(out)).replace("\\", "/"))
        lecture["slide_files"] = saved
        lectures.append(lecture)
        print(f"{n:02d}  {lecture['title']}  ({len(lecture['chapters'])} chapters, {len(saved)} slide files)")
        time.sleep(0.5)   # be gentle with the site

    meta = {"title": course_title, "url": course_url, "license": LICENSE, "lectures": lectures}
    (out / "course.json").write_text(json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\nSaved {len(lectures)} lectures to {out}")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
