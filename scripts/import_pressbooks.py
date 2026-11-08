"""Download a Pressbooks book for the tutor: parts and chapters (in order) as structured text, with the
questions and correct answers of the book's H5P exercises, and the chapters' links to other sites.

    uv run python scripts/import_pressbooks.py <book URL> <name> [output folder]

Writes materials/library/<name>/: book.json, sections/<NN.MM>-<slug>.txt (the same layout as
import_libretexts.py, so build_syllabus.py reads both) and links/<NN.MM>-<slug>.txt.
Used for "Building Academic Writing Skills" (Cui): its LibreTexts copy leaves Units 14-20 empty and replaces
every exercise with "An interactive H5P element has been excluded from this version of the text".
"""

import html
import json
import re
import sys
import time
from pathlib import Path

import httpx

from import_libretexts import ContentParser, h5p_to_text, slugify, strip_tags

ROOT = Path(__file__).resolve().parent.parent
# The site's firewall refuses programs that do not look like a browser; requests are spaced out like reading
HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
                         "Chrome/130.0 Safari/537.36"}


def balanced(page: str, start: int, tag: str = "section") -> str:
    depth = 0
    for m in re.finditer(rf"<(/?){tag}\b[^>]*>", page[start:]):
        depth += -1 if m.group(1) else 1
        if depth == 0:
            return page[start:start + m.end()]
    return page[start:]


def chapter_content(page: str) -> str:
    m = re.search(r'<section[^>]*data-type="(?:chapter|front-matter|back-matter|part)"[^>]*>', page)
    return balanced(page, m.start()) if m else ""


def h5p_contents(page: str) -> dict[str, list[str]]:
    """Content id -> the exercise's questions and correct answers, from the page's H5PIntegration settings."""
    m = re.search(r"H5PIntegration\s*=\s*(\{.*?\});\s*</script>", page, re.S)
    if not m:
        return {}
    out = {}
    for key, content in json.loads(m.group(1)).get("contents", {}).items():
        try:
            lines = h5p_to_text(content.get("library", ""), json.loads(content.get("jsonContent") or "{}"))
        except (ValueError, TypeError) as e:
            lines = [f"(Interactive exercise could not be read: {e})"]
        out[key.removeprefix("cid-")] = lines or ["(Interactive exercise without questions.)"]
    return out


def external_links(content: str, site: str) -> list[tuple[str, str]]:
    links = []
    for m in re.finditer(r'<a\s[^>]*href="(https?://[^"]+)"[^>]*>(.*?)</a>', content, re.S):
        url, text = html.unescape(m.group(1)), " ".join(strip_tags(m.group(2)).split())
        if site not in url and "creativecommons.org" not in url:
            links.append((text or url, url))
    return list(dict.fromkeys(links))


def main(book_url: str, name: str, out_dir: str | None = None) -> None:
    book_url = book_url.rstrip("/")
    site = re.match(r"https?://[^/]+", book_url).group(0)
    out = Path(out_dir) if out_dir else ROOT / "materials" / "library" / name
    sections_dir, links_dir = out / "sections", out / "links"
    for folder in (sections_dir, links_dir):
        folder.mkdir(parents=True, exist_ok=True)
        for old in folder.glob("*.txt"):           # rebuilt by every run
            old.unlink()
    client = httpx.Client(headers=HEADERS, follow_redirects=True, timeout=60)

    def get(url: str) -> str:
        time.sleep(1.0)
        response = client.get(url)
        response.raise_for_status()
        return response.text

    toc = json.loads(get(f"{book_url}/wp-json/pressbooks/v2/toc"))
    meta = json.loads(get(f"{book_url}/wp-json/pressbooks/v2/metadata"))
    book_title = meta.get("name", name)
    license_info = meta.get("license") or {}
    license_text = license_info.get("name") if isinstance(license_info, dict) else str(license_info)
    authors = ", ".join(a.get("name", "") for a in meta.get("author", []) if isinstance(a, dict)) or "see the book"

    groups = [("Front Matter", [{"title": c["title"], "link": c["link"]} for c in toc.get("front-matter", [])])]
    groups += [(p["title"], p.get("chapters", [])) for p in toc.get("parts", [])]
    chapters = []
    for c_index, (group_title, items) in enumerate(groups):
        sections = []
        for s_index, item in enumerate(items, 1):
            page = get(item["link"])
            content = chapter_content(page)
            exercises = h5p_contents(page)
            # Each H5P exercise becomes a marker the parser keeps, then its questions and answers
            content = re.sub(r'<div class="h5p-iframe-wrapper">\s*<iframe[^>]*data-content-id="(\d+)"[^>]*>\s*</iframe>\s*</div>',
                             r"<p>[[H5P:\1]]</p>", content)
            content = re.sub(r'<div class="h5p-content"[^>]*data-content-id="(\d+)"[^>]*>\s*</div>', r"<p>[[H5P:\1]]</p>", content)
            parser = ContentParser()
            parser.feed(content)
            text = re.sub(r"\[\[H5P:(\d+)\]\]", lambda m: "[Interactive exercise]\n" + "\n".join(
                exercises.get(m.group(1), ["(Interactive exercise not found: open the chapter online.)"])), parser.text())
            title = html.unescape(item["title"])
            stem = f"{c_index:02d}.{s_index:02d}-{slugify(title)}"
            (sections_dir / f"{stem}.txt").write_text(
                f"{book_title}\n{title}\nSource: {item['link']}\nLicense: {license_text}; by {authors}\n\n{text}\n",
                encoding="utf-8")
            links = external_links(content, site)
            if links:
                (links_dir / f"{stem}.txt").write_text("".join(f"{t} | {u}\n" for t, u in links), encoding="utf-8")
            sections.append({"title": title, "url": item["link"], "file": f"sections/{stem}.txt",
                             "words": len(text.split()), "exercises": len(exercises)})
        chapters.append({"title": html.unescape(group_title), "sections": sections})
        print(f"{group_title}: {len(sections)} chapters, {sum(s['words'] for s in sections)} words, "
              f"{sum(s['exercises'] for s in sections)} exercises")

    book = {"title": book_title, "url": book_url, "license": license_text, "authors": authors, "chapters": chapters}
    (out / "book.json").write_text(json.dumps(book, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\n{book_title} by {authors}\nlicense: {license_text}\nsaved to {out}")


if __name__ == "__main__":
    main(*sys.argv[1:4])
