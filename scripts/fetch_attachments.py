"""Download the files attached to a LibreTexts book's sections (answer sheets, slides, audio clips) after
import_libretexts.py, extract the text of the documents, and list the sections' links to other sites.

    uv run python scripts/fetch_attachments.py esl-b1-speaking

Writes materials/library/<name>/attachments/<section stem>/<file> and, per section that has documents,
attachments/<section stem>.txt with their text (answer sheets for the listening exercises, for example);
links/<section stem>.txt lists "text | URL" for links that leave LibreTexts. Already downloaded files are kept.
"""

import html
import json
import re
import sys
import time
import zipfile
from pathlib import Path
from urllib.parse import unquote
from xml.etree import ElementTree

import httpx
from pypdf import PdfReader
from pypdf.errors import PdfReadError as PdfError

from import_libretexts import main_content

ROOT = Path(__file__).resolve().parent.parent
ATTACHMENT = re.compile(r'href="(https://[^"]+/@api/deki/files/\d+/([^"?]+\.(docx|pptx|m4a|mp3|wav|pdf)))(?:\?[^"]*)?"', re.I)
W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
A = "{http://schemas.openxmlformats.org/drawingml/2006/main}"


def docx_text(path: Path) -> str:
    """Paragraphs and table rows of a Word file, in order. Answer sheets mark the answer by underlining,
    highlighting or bold type; marked words are shown in [brackets]. Bold counts only where a paragraph or
    table cell mixes bold and plain text, so bold headings stay as they are."""
    root = ElementTree.fromstring(zipfile.ZipFile(path).read("word/document.xml"))

    def on(props, tag: str) -> bool:
        el = props.find(f"{W}{tag}") if props is not None else None
        return el is not None and el.get(f"{W}val") not in ("none", "0", "false")

    def runs(paragraphs) -> list[tuple[str, bool, bool]]:
        out = []
        for p in paragraphs:
            if out:
                out.append((" ", False, False))
            for run in p.iter(f"{W}r"):
                text = "".join(t.text or "" if t.tag == f"{W}t" else " " for t in run
                               if t.tag in (f"{W}t", f"{W}tab", f"{W}br"))
                props = run.find(f"{W}rPr")
                out.append((text, on(props, "u") or on(props, "highlight"), on(props, "b")))
        return out

    def render(parts: list[tuple[str, bool, bool]]) -> str:
        written = [(t, u, b) for t, u, b in parts if t.strip()]
        use_bold = any(b for _, _, b in written) and not all(b for _, _, b in written)
        out = []
        for text, underlined, bold in parts:
            # Underlining often runs over the comma and space after a marked letter: keep those outside
            lead, core, trail = re.match(r"^(\W*)(.*?)(\W*)$", text, re.S).groups()
            marked = underlined or (use_bold and bold)
            out.append(f"{lead}[{core}]{trail}" if marked and core else text)
        return " ".join(re.sub(r"\]\s*\[", lambda m: m.group(0)[1:-1], "".join(out)).split())

    lines = []
    for block in root.find(f"{W}body"):
        if block.tag == f"{W}p":
            lines.append(render(runs([block])))
        elif block.tag == f"{W}tbl":
            for row in block.iter(f"{W}tr"):
                cells = [render(runs(cell.iter(f"{W}p"))) for cell in row.iter(f"{W}tc")]
                if any(cells):
                    lines.append("| " + " | ".join(cells) + " |")
    return "\n".join(line for line in lines if line)


def pdf_text(path: Path) -> str:
    """Text of a PDF attachment (a model essay, a checklist), page by page."""
    pages = [" ".join((page.extract_text() or "").split()) for page in PdfReader(str(path)).pages]
    return "\n".join(f"Page {i}: {text}" for i, text in enumerate(pages, 1) if text)


def pptx_text(path: Path) -> str:
    archive = zipfile.ZipFile(path)
    slides = sorted((n for n in archive.namelist() if re.fullmatch(r"ppt/slides/slide\d+\.xml", n)),
                    key=lambda n: int(re.search(r"(\d+)", n.rsplit("/", 1)[1]).group(1)))
    out = []
    for i, name in enumerate(slides, 1):
        root = ElementTree.fromstring(archive.read(name))
        paras = ["".join(t.text or "" for t in p.iter(f"{A}t")).strip() for p in root.iter(f"{A}p")]
        out.append(f"Slide {i}: " + " / ".join(p for p in paras if p))
    return "\n".join(out)


def external_links(page: str) -> list[tuple[str, str]]:
    """(link text, URL) of links in a section's main text that leave LibreTexts, e.g. a listening lab or a
    video that is linked rather than embedded."""
    content = main_content(page)
    links = []
    for m in re.finditer(r'<a\s[^>]*href="(https?://[^"]+)"[^>]*>(.*?)</a>', content, re.S):
        url, text = html.unescape(m.group(1)), " ".join(html.unescape(re.sub(r"<[^>]+>", " ", m.group(2))).split())
        if "libretexts.org" in url or "creativecommons.org" in url:
            continue
        links.append((text or url, url))
    return list(dict.fromkeys(links))


def main(name: str) -> None:
    lib = ROOT / "materials" / "library" / name
    book = json.loads((lib / "book.json").read_text(encoding="utf-8"))
    client = httpx.Client(headers={"User-Agent": "personal-tutor (personal study)"}, follow_redirects=True, timeout=120)
    for chapter in book["chapters"]:
        for section in chapter["sections"]:
            stem = Path(section["file"]).stem
            time.sleep(0.4)
            page = client.get(section["url"]).text
            links = external_links(page)
            if links:
                (lib / "links").mkdir(exist_ok=True)
                (lib / "links" / f"{stem}.txt").write_text(
                    "".join(f"{text} | {url}\n" for text, url in links), encoding="utf-8")
                print(f"{stem}: {len(links)} link(s)")
            files = list(dict.fromkeys((m.group(1), unquote(m.group(2))) for m in ATTACHMENT.finditer(page)))
            if not files:
                continue
            folder = lib / "attachments" / stem
            folder.mkdir(parents=True, exist_ok=True)
            texts = []
            for url, filename in files:
                target = folder / filename
                if not target.exists():
                    time.sleep(0.3)
                    response = client.get(url)
                    response.raise_for_status()
                    target.write_bytes(response.content)
                label = filename.rsplit(".", 1)[0].replace("_", " ").strip()
                suffix = target.suffix.lower()
                try:
                    if suffix == ".docx":
                        texts.append(f"--- {label} ({filename}) ---\n{docx_text(target)}")
                    elif suffix == ".pptx":
                        texts.append(f"--- {label} ({filename}) ---\n{pptx_text(target)}")
                    elif suffix == ".pdf":
                        texts.append(f"--- {label} ({filename}) ---\n{pdf_text(target)}")
                except (zipfile.BadZipFile, KeyError, ElementTree.ParseError, PdfError) as e:
                    texts.append(f"--- {label} ({filename}) ---\n(could not read: {e})")
            if texts:
                (lib / "attachments" / f"{stem}.txt").write_text(
                    f"Attached files of \"{section['title']}\" ({section['url']})\n\n" + "\n\n".join(texts) + "\n",
                    encoding="utf-8")
            print(f"{stem}: {len(files)} file(s): " + ", ".join(f for _, f in files))


if __name__ == "__main__":
    main(sys.argv[1])
