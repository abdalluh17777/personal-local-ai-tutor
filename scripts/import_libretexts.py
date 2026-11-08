"""Download a LibreTexts book for the tutor: chapters, sections (in order) as structured text, plus the videos
and interactive exercises each section uses.

    uv run python scripts/import_libretexts.py <book URL> <name> [output folder]

Writes materials/library/<name>/ (or the given folder): book.json and sections/<NN.MM>-<slug>.txt.
LibreTexts books are openly licensed (the license is recorded in book.json); this is for personal study.
"""

import html
import json
import re
import sys
import time
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote

import httpx

ROOT = Path(__file__).resolve().parent.parent
BLOCK = {"p", "div", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6", "dt", "dd", "blockquote", "figcaption", "caption"}


class ContentParser(HTMLParser):
    """Turns a section's HTML into plain structured text: headings, paragraphs, list items, table rows,
    image descriptions, and markers for videos and interactive exercises.

    List items keep their numbering: "1. " for a numbered list, "a. " for a lettered one, "- " for bullets;
    an item inside another item is indented by two spaces. Exercises are often numbered questions with lettered
    options, and answer keys refer to them as "1. b"."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.lines: list[str] = []
        self.buf: list[str] = []
        self.prefix = ""
        self.skip = 0
        self.cells: list[str] | None = None
        self.media: list[dict] = []
        self.lists: list[dict] = []          # open <ol>/<ul>: ordered?, numbering style, items so far
        self.spans: list[list[tuple]] = []   # open <span>s: the marks (underline, line-through) each one opened

    def _item_marker(self) -> str:
        if not self.lists:
            return "- "
        current = self.lists[-1]
        current["count"] += 1
        indent = "  " if len(self.lists) > 1 else ""
        if not current["ordered"]:
            return indent + "- "
        n, style = current["count"], current["style"]
        if "lower-alpha" in style or "lower-latin" in style or style.endswith("type:a"):
            mark = "abcdefghijklmnopqrstuvwxyz"[(n - 1) % 26]
        elif "upper-alpha" in style or "upper-latin" in style or style.endswith("type:A"):
            mark = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"[(n - 1) % 26]
        elif "lower-roman" in style or style.endswith("type:i"):
            mark = ["i", "ii", "iii", "iv", "v", "vi", "vii", "viii", "ix", "x", "xi", "xii"][(n - 1) % 12]
        else:
            mark = str(n)
        return f"{indent}{mark}. "

    def _flush(self):
        text = " ".join(self._emphasis("".join(self.buf)).split())
        text = re.sub(r"\\\(\\PageIndex\{\d+\}\\\)", "", text)          # "Figure \(\PageIndex{1}\)"
        if text:
            if self.cells is not None:
                self.cells.append(text)
            else:
                self.lines.append(self.prefix + text)
        self.buf = []

    # Bold, underlined and crossed-out words: exercises say "the verbs in bold" or "the underlined words", and
    # editing examples cross out what was deleted. Marked while parsing with private characters, written as
    # **bold**, __underlined__ and ~~deleted~~ when the line is complete
    BOLD, UNDERLINE, STRIKE = ("", "", "**"), ("", "", "__"), ("", "", "~~")
    ITALIC = ("", "", "*")          # titles of books and newspapers are taught as italic

    @classmethod
    def _emphasis(cls, text: str) -> str:
        for start, end, mark in (cls.BOLD, cls.UNDERLINE, cls.STRIKE, cls.ITALIC):
            text = re.sub(f"{end}(\\s*){start}", lambda m: m.group(1), text)    # one span, not "word____,__"
            if mark == "*":
                # "*" must not touch a word ("end in*-ly*" would not read as italic): a space keeps them apart.
                # Not "_": that is an underline mark already written ("__***word***__" must stay one pair)
                text = re.sub(f"([^\\W_]){start}", lambda m: m.group(1) + " " + start, text)
                text = re.sub(f"{end}([^\\W_])", lambda m: end + " " + m.group(1), text)

            def wrap(m: re.Match) -> str:
                lead, core, trail = re.match(r"^(\s*)(.*?)(\s*)$", m.group(1), re.S).groups()
                if not core or (mark == "*" and not re.search(r"\w", core)):   # an italic comma means nothing
                    return lead + core + trail
                return f"{lead}{mark}{core}{mark}{trail}"
            text = re.sub(f"{start}([^{start}{end}]*){end}", wrap, text)
            text = text.replace(start, "").replace(end, "")      # unmatched: emphasis across lines
        return text

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag in ("script", "style", "noscript") or "mt-guide-tabs" in a.get("class", ""):
            self.skip += 1
            return
        if self.skip:
            return
        if tag in ("strong", "b"):
            self.buf.append(self.BOLD[0])
        elif tag == "u":
            self.buf.append(self.UNDERLINE[0])
        elif tag in ("s", "del", "strike"):
            self.buf.append(self.STRIKE[0])
        elif tag in ("em", "i"):
            self.buf.append(self.ITALIC[0])
        elif tag == "span":
            style = a.get("style") or ""
            marks = [m for m, word in ((self.UNDERLINE, "underline"), (self.STRIKE, "line-through"),
                                       (self.ITALIC, "italic")) if word in style]
            self.spans.append(marks)
            self.buf += [m[0] for m in marks]
        if tag in ("ol", "ul"):
            self._flush()
            try:
                start = int(a.get("start") or 1)
            except ValueError:
                start = 1
            self.lists.append({"ordered": tag == "ol", "count": start - 1,
                               "style": (a.get("style") or "").replace(" ", "") + f";type:{a.get('type') or ''}"})
        if tag in BLOCK:
            self._flush()
            self.prefix = self._item_marker() if tag == "li" else \
                {"h1": "# ", "h2": "## ", "h3": "## ", "h4": "### ", "h5": "### ", "h6": "### "}.get(tag, "")
        if tag == "tr":
            self.cells = []
        elif tag in ("td", "th"):
            self._flush()
        elif tag == "img" and (a.get("alt") or "").strip():
            self.buf.append(f" [Image: {a['alt'].strip()}] ")
        elif tag == "iframe" and a.get("src"):
            src = a["src"]
            kind = "video" if "youtube" in src or "vimeo" in src else "interactive"
            self.media.append({"kind": kind, "url": src.split("?")[0]})
            self._flush()
            self.lines.append(f"[{'Video' if kind == 'video' else 'Interactive exercise'}: {src.split('?')[0]}]")
        elif tag == "br":
            self._flush()

    def handle_endtag(self, tag):
        if tag in ("script", "style", "noscript"):
            self.skip = max(0, self.skip - 1)
            return
        if self.skip:
            return
        if tag in ("strong", "b"):
            self.buf.append(self.BOLD[1])
        elif tag == "u":
            self.buf.append(self.UNDERLINE[1])
        elif tag in ("s", "del", "strike"):
            self.buf.append(self.STRIKE[1])
        elif tag in ("em", "i"):
            self.buf.append(self.ITALIC[1])
        elif tag == "span" and self.spans:
            self.buf += [m[1] for m in reversed(self.spans.pop())]
        if tag in ("ol", "ul"):
            self._flush()
            if self.lists:
                self.lists.pop()
        elif tag in ("td", "th"):
            self._flush()
        elif tag == "tr" and self.cells is not None:
            row = [c for c in self.cells if c]
            self.cells = None
            if row:
                self.lines.append("| " + " | ".join(row) + " |")
        elif tag in BLOCK:
            self._flush()
            self.prefix = ""

    def handle_data(self, data):
        if not self.skip:
            self.buf.append(data)

    def text(self) -> str:
        self._flush()
        out, last = [], None
        for line in self.lines:
            if line != last:          # LibreTexts repeats some captions
                out.append(line)
            last = line
        return "\n".join(out)


def strip_tags(fragment: str) -> str:
    return " ".join(html.unescape(re.sub(r"<[^>]+>", " ", fragment or "")).split())


def with_bold(fragment: str) -> str:
    """Text of a question, keeping bold or underlined words as **word**: questions such as "Choose the correct
    form of the verb in bold" depend on them."""
    marked = re.sub(r"<(strong|b|u)\b[^>]*>(.*?)</\1>", lambda m: f" **{strip_tags(m.group(2))}** "
                    if strip_tags(m.group(2)) else " ", fragment or "", flags=re.S | re.I)
    return re.sub(r"\*\*\s*\*\*", " ", strip_tags(marked)).replace("** ,", "**,").replace("** .", "**.")


def starred(text: str) -> str:
    """H5P marks answers as *answer* (alternatives with "/"): show them as [answer]."""
    return re.sub(r"\*([^*]+)\*", lambda m: "[" + m.group(1).split(":")[0] + "]", strip_tags(text))


def h5p_to_text(library: str, p: dict) -> list[str]:
    """The questions and correct answers of the common H5P exercise types, as plain lines."""
    kind = library.split(" ")[0]
    out: list[str] = []
    if kind == "H5P.QuestionSet":
        for q in p.get("questions", []):
            out += h5p_to_text(q.get("library", ""), q.get("params", {}))
    elif kind == "H5P.MultiChoice":
        answers = p.get("answers", [])
        out.append("Q: " + with_bold(p.get("question")))
        out.append("   Options: " + " | ".join(strip_tags(a.get("text")) for a in answers))
        out.append("   Answer: " + " | ".join(strip_tags(a.get("text")) for a in answers if a.get("correct")))
    elif kind == "H5P.TrueFalse":
        out.append("Q (true or false): " + with_bold(p.get("question")))
        out.append(f"   Answer: {'True' if str(p.get('correct')).lower() == 'true' else 'False'}")
    elif kind == "H5P.SingleChoiceSet":
        for c in p.get("choices", []):
            answers = [strip_tags(a) for a in c.get("answers", [])]
            out.append("Q: " + with_bold(c.get("question")))
            # H5P stores the correct option first and shuffles them on screen: a fixed alphabetical order instead,
            # so the printed material, the guides and the tutor show the same letters without giving the answer
            shown = sorted(answers, key=lambda o: [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", o)])
            out.append("   Options: " + " | ".join(shown))
            out.append("   Answer: " + (answers[0] if answers else ""))
    elif kind == "H5P.Blanks":
        out.append("Task: " + strip_tags(p.get("text")))
        out += ["   " + starred(q) for q in p.get("questions", [])]
    elif kind in ("H5P.DragText", "H5P.MarkTheWords"):
        out.append("Task: " + strip_tags(p.get("taskDescription")))
        out.append("   " + starred(p.get("textField", "")))
    elif kind == "H5P.Summary":
        for s in p.get("summaries", []):
            statements = [strip_tags(x) for x in s.get("summary", [])]
            if statements:
                out.append("Choose the correct statement: " + " | ".join(statements))
                out.append("   Answer: " + statements[0])
    elif kind == "H5P.InteractiveVideo":
        for i in p.get("interactiveVideo", {}).get("assets", {}).get("interactions", []):
            action = i.get("action", {})
            out += h5p_to_text(action.get("library", ""), action.get("params", {}))
    elif kind == "H5P.Column":
        for c in p.get("content", []):
            content = c.get("content", {})
            out += h5p_to_text(content.get("library", ""), content.get("params", {}))
    elif kind in ("H5P.Text", "H5P.AdvancedText"):
        text = strip_tags(p.get("text"))
        if text:
            out.append(text)
    elif kind == "H5P.Accordion":                      # expandable explanation panels
        for panel in p.get("panels", []):
            content = panel.get("content", {})
            body = h5p_to_text(content.get("library", ""), content.get("params", {}))
            out.append(f"{strip_tags(panel.get('title'))}: " + " ".join(body))
    elif kind == "H5P.Dialogcards":                    # flash cards, e.g. a word and its meaning
        if strip_tags(p.get("description")):
            out.append(strip_tags(p.get("description")))
        for card in p.get("dialogs", []):
            out.append(f"Card: {strip_tags(card.get('text'))} -> {strip_tags(card.get('answer'))}")
    elif kind == "H5P.Essay":                          # an open writing box, sometimes with a sample answer
        task = strip_tags(p.get("taskDescription"))
        out.append("Writing task: " + (task or "write your answer in your notebook."))
        sample = strip_tags((p.get("solution") or {}).get("sample"))
        if sample:
            out.append("   Sample answer: " + sample)
    elif kind:
        out.append(f"(Interactive exercise of type {kind}: open it online to do it.)")
    return out


def fetch_h5p(client: httpx.Client, url: str) -> list[str]:
    page = client.get(url).text
    content = re.search(r'"jsonContent"\s*:\s*"((?:[^"\\]|\\.)*)"', page)
    library = re.search(r'"library"\s*:\s*"([^"]+)"', page)
    if not (content and library):
        return ["(Interactive exercise could not be read: open it online.)"]
    params = json.loads(json.loads(f'"{content.group(1)}"'))
    return h5p_to_text(library.group(1), params) or ["(Interactive exercise without questions.)"]


def main_content(page: str) -> str:
    """The page's content section, up to its own closing tag: it can contain nested <section> boxes
    (cutting at the first </section> lost most of "2.5: Reading Strategies")."""
    start = page.find('<section class="mt-content-container"')
    if start < 0:
        return ""
    depth = 0
    for m in re.finditer(r"<(/?)section\b[^>]*>", page[start:]):
        depth += -1 if m.group(1) else 1
        if depth == 0:
            return page[start:start + m.end()]
    return page[start:]


def page_license(text: str) -> tuple[str, str] | None:
    """(license, authors) from the footer every page ends with: "This page titled ... is shared under a
    CC BY 4.0 license and was authored, remixed, and/or curated by ...". It can differ from the book's:
    a section may be adapted from another open book."""
    m = re.search(r"is shared under an? (.+?) license and was authored, remixed, and/or curated by (.+?)\.?$",
                  text, re.M)
    return (m.group(1).strip(), m.group(2).strip()) if m else None


def child_links(page: str, parent_url: str) -> list[str]:
    """Direct child pages of parent_url, in the order they first appear."""
    pattern = re.escape(parent_url) + r"/[^\"#?/]+"
    return list(dict.fromkeys(re.findall(f'href="({pattern})"', page)))


def title_of(url: str) -> str:
    return " ".join(unquote(unquote(url.rsplit("/", 1)[1])).replace("_", " ").split())


def slugify(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:60]


def main(book_url: str, name: str, out_dir: str | None = None) -> None:
    out = Path(out_dir) if out_dir else ROOT / "materials" / "library" / name
    sections_dir = out / "sections"
    sections_dir.mkdir(parents=True, exist_ok=True)
    for old in sections_dir.glob("*.txt"):    # sections/ is rebuilt by every run
        old.unlink()
    client = httpx.Client(headers={"User-Agent": "personal-tutor (personal study)"}, follow_redirects=True, timeout=60)

    def get(url: str) -> str:
        time.sleep(0.4)   # be gentle with the site
        return client.get(url).text

    book_page = get(book_url)
    book_title = re.sub(r"\s+-\s+[\w. ]*LibreTexts\s*$", "",       # "... - Humanities LibreTexts", "- Social Sci ..."
                        html.unescape(re.search(r"<title>(.*?)</title>", book_page, re.S).group(1)).strip())
    license_match = re.search(r'"license"\s*:\s*"([^"]+)"|data-license="([^"]+)"|(CC BY[- A-Z0-9.]*)', book_page)
    license_text = next((g for g in (license_match.groups() if license_match else []) if g), "see book front matter")

    def save_page(url: str, page: str, number: str, parent: str | None = None, min_words: int = 0) -> dict | None:
        """One page as sections/<number>-<slug>.txt, its interactive exercises replaced by their questions and
        correct answers. A page shorter than min_words (a list of its subpages) is not saved."""
        parser = ContentParser()
        parser.feed(main_content(page))
        text = parser.text()
        if len(text.split()) < min_words:
            return None
        # The page's own heading: some URLs only say "Section 1-"
        h1 = re.search(r'<h1[^>]*id="title"[^>]*>(.*?)</h1>', page, re.S)
        title = strip_tags(h1.group(1)) if h1 and strip_tags(h1.group(1)) else title_of(url)
        for media in parser.media:
            if media["kind"] == "interactive" and "h5p" in media["url"]:
                time.sleep(0.3)
                try:
                    lines = fetch_h5p(client, media["url"])
                except (httpx.HTTPError, ValueError) as e:
                    lines = [f"(Interactive exercise could not be read: {e})"]
                media["questions"] = len([ln for ln in lines if "Answer" in ln or "[" in ln])
                text = text.replace(f"[Interactive exercise: {media['url']}]",
                                    "[Interactive exercise]\n" + "\n".join(lines), 1)
        stem = f"{number}-{slugify(re.sub(r'^[0-9a-z]+(\.[0-9]+)*:\s*', '', title))}"
        own = page_license(text)
        section_license = f"{own[0]}; authored, remixed or curated by {own[1]}" if own else license_text
        (sections_dir / f"{stem}.txt").write_text(
            f"{book_title}\n{title}\nSource: {url}\nLicense: {section_license}\n\n{text}\n", encoding="utf-8")
        return {"title": title, "url": url, "file": f"sections/{stem}.txt", "words": len(text.split()),
                "media": parser.media, **({"part_of": parent} if parent else {})}

    chapters = []
    for c_index, chapter_url in enumerate(child_links(book_page, book_url)):
        chapter_title = title_of(chapter_url)
        chapter_page = get(chapter_url)
        sections = []
        queue = child_links(chapter_page, chapter_url) or [chapter_url]
        for s_index, section_url in enumerate(queue, 1):
            page = chapter_page if section_url == chapter_url else get(section_url)
            number = f"{c_index:02d}.{s_index:02d}"
            # Some books go one level deeper (a chapter "Dreams" with pages "1.1: Discussion", "1.2: Listening"...):
            # each of those pages becomes a section of its own
            subpages = child_links(page, section_url) if section_url != chapter_url else []
            if subpages:
                parent = strip_tags((re.search(r'<h1[^>]*id="title"[^>]*>(.*?)</h1>', page, re.S) or [None, ""])[1]) \
                    or title_of(section_url)
                # The page itself when it has its own text ("1.5: Annotating a Text" with one example subpage),
                # not when it only lists its subpages ("Chapter 1: Dreams")
                own = save_page(section_url, page, number, min_words=150)
                if own:
                    sections.append(own)
                for k, sub_url in enumerate(subpages, 1):
                    sections.append(save_page(sub_url, get(sub_url), f"{number}.{k:02d}", parent))
            else:
                sections.append(save_page(section_url, page, number))
        chapters.append({"title": chapter_title, "url": chapter_url, "sections": sections})
        print(f"{chapter_title}: {len(sections)} sections, {sum(s['words'] for s in sections)} words")

    meta = {"title": book_title, "url": book_url, "license": license_text, "chapters": chapters}
    (out / "book.json").write_text(json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\n{book_title}\nlicense: {license_text}\nsaved to {out}")


if __name__ == "__main__":
    main(*sys.argv[1:4])
