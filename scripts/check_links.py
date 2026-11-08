"""Check the links that a course's printed lesson material and tutor text will show (the books' own links, after
build_syllabus's filters): dead pages, links that now lead to another site, and pages that became spam.

    uv run python scripts/check_links.py esl-stage1 esl-stage2 esl-stage3 esl-stage4

Prints one line per suspicious link with the lessons that use it, and adds the clearly dead ones (404, 410, no
server) to materials/library/dead-links.txt, which build_syllabus leaves out. The links already in that file are
tested again on each run, and one that answers again is taken off it. Old domains of open textbooks are
sometimes bought by gambling sites or parked: such links must not be printed for the learner (add them to
build_syllabus._SKIP_LINK).
"""

import json
import re
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import urlparse

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent))
import build_syllabus as bs  # noqa: E402

HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
                         "Chrome/130.0 Safari/537.36"}
SPAM = re.compile(r"casino|slot|gambl|betting|\bbet\b|poker|judi|togel|sportsbook|baccarat|roulette|lottery|"
                  r"domain (is )?for sale|buy this domain", re.I)
SAME_SITE = [("youtu.be", "youtube.com"), ("doi.org", ""), ("dx.doi.org", "")]


def host(url: str) -> str:
    return urlparse(url).netloc.lower().removeprefix("www.")


def check(url: str) -> dict:
    try:
        with httpx.Client(headers=HEADERS, follow_redirects=True, timeout=20) as client:
            r = client.get(url)
        title = re.search(r"<title[^>]*>(.*?)</title>", r.text[:20000], re.S | re.I)
        return {"url": url, "status": r.status_code, "final": str(r.url),
                "title": " ".join(title.group(1).split())[:120] if title else ""}
    except httpx.HTTPError as e:
        return {"url": url, "status": None, "final": "", "title": "", "error": type(e).__name__}


def moved_away(original: str, final: str) -> bool:
    a, b = host(original), host(final)
    if not b or a == b or b.endswith("." + a) or a.endswith("." + b):
        return False
    return not any(a == x and (not y or b.endswith(y)) for x, y in SAME_SITE)


def is_spam(r: dict) -> bool:
    return bool(SPAM.search(r["title"]) or SPAM.search(host(r["final"]) if r["final"] else ""))


def dead_links(results: list[dict], old: set[str]) -> set[str]:
    """The new list of dead links. Only the clearly dead: a 400, a server error (5xx) or a timeout may work in a
    browser or later, so it is listed for a look instead. A link from the old list stays on it until its page
    answers again (200, on its own site, not spam)."""
    dead = {r["url"] for r in results if r["status"] in (404, 410) or r.get("error") == "ConnectError"}
    back = {r["url"] for r in results if r["url"] in old and r["status"] == 200 and not is_spam(r)
            and not moved_away(r["url"], r["final"])}
    return (old - back) | dead


def main(names: list[str]) -> None:
    uses: dict[str, list[str]] = {}
    for name in names:
        syllabus = json.loads((bs.LIBRARY / name / "syllabus.json").read_text(encoding="utf-8"))
        for lesson in bs.lessons(syllabus):
            for source in lesson["sources"]:
                for _, url in bs.read_section(source)["links"]:
                    uses.setdefault(url, []).append(f"{name} L{lesson['number']:02d}")
    # read_section leaves out the links found dead on an earlier run: they are tested again, a page may be back
    old = set(bs.DEAD_LINKS_FILE.read_text(encoding="utf-8").split()) if bs.DEAD_LINKS_FILE.exists() else set()
    for url in old:
        uses.setdefault(url, ["dead-links.txt"])
    print(f"{len(uses)} links ({len(old)} from dead-links.txt)")
    with ThreadPoolExecutor(8) as pool:
        results = list(pool.map(check, sorted(uses)))
    dead = dead_links(results, old)
    bs.DEAD_LINKS_FILE.write_text("\n".join(sorted(dead)) + "\n", encoding="utf-8")
    print(f"{len(dead - old)} new dead links, {len(old - dead)} back again: {len(dead)} in {bs.DEAD_LINKS_FILE}")
    bad = 0
    for r in results:
        if r["url"] in old and r["url"] in dead:        # known dead and still left out: nothing to look at
            continue
        spam = is_spam(r)
        reasons = []
        if spam:
            reasons.append("SPAM")
        if r["status"] is None:
            reasons.append(r.get("error", "no answer"))
        elif r["status"] >= 400 and r["status"] not in (401, 403, 429):     # 403: sites that refuse scripts
            reasons.append(f"HTTP {r['status']}")
        if r["final"] and moved_away(r["url"], r["final"]):
            reasons.append(f"now {host(r['final'])}")
        if reasons:
            bad += 1
            where = ", ".join(sorted(set(uses[r["url"]])))[:80]
            print(f"{' / '.join(reasons):28} {r['url'][:90]}  [{where}]  {r['title'][:60]}")
    print(f"\n{bad} of {len(results)} links need a look")


if __name__ == "__main__":
    main(sys.argv[1:])
