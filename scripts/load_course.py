"""Load a prepared course from materials/library/<name> into the running tutor app: one course, one topic
per lecture (in order), each lecture's study guide and transcript uploaded and linked as the topic's material.

    uv run python scripts/load_course.py psyc-110 "PSYC 110 · Introduction to Psychology (Yale)"

Safe to run again: existing course, topics and files are reused, not duplicated; a guide or transcript that
changed replaces the old copy in the app (as build_syllabus.load does).
"""

import hashlib
import json
import re
import sys
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
BASE = "http://127.0.0.1:8765"


def main(name: str, course_name: str) -> None:
    lib = ROOT / "materials" / "library" / name
    meta = json.loads((lib / "course.json").read_text(encoding="utf-8"))
    api = httpx.Client(base_url=BASE, headers={"Origin": BASE}, timeout=300)

    course = next((c for c in api.get("/api/courses").json() if c["name"] == course_name), None)
    if not course:
        description = (f"{meta['title']}, Yale University, Professor Paul Bloom, Spring 2007. "
                       f"{len(meta['lectures'])} sessions. Source: {meta['url']} ({meta['license']}).")
        course = api.post("/api/courses", json={"name": course_name, "description": description}).json()
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

    for lec in meta["lectures"]:
        stem = Path(lec["file"]).stem
        # "Lecture 9: Evolution, Emotion, and Reason: Love" (the guest is named in the study guide)
        title = re.sub(r"\s*\(Guest Lecture by [^)]*\)", "",
                       re.sub(r"^((?:Guest )?Lecture(?: \d+)?) - ", r"\1: ", lec["title"]))
        topic = topics.get(title) or api.post(f"/api/courses/{cid}/topics", json={"title": title}).json()

        material = []   # the study guide first: a lesson that starts from scratch opens with its summary
        for path, mime in ((lib / "guides" / f"{stem}.md", "text/markdown"), (lib / lec["file"], "text/plain")):
            if path.exists():
                doc_id = upload(path, mime)
                if doc_id is not None:
                    material.append(doc_id)
        alive = {d["id"] for d in docs.values()}
        current = [d for d in topic.get("document_ids", []) if d in alive]
        if current[:len(material)] != material:   # links are kept in order: guide, transcript, then anything added
            extras = [d for d in current if d not in material]
            for doc_id in current:
                api.delete(f"/api/topics/{topic['id']}/documents/{doc_id}").raise_for_status()
            for doc_id in material + extras:
                api.put(f"/api/topics/{topic['id']}/documents/{doc_id}").raise_for_status()
        print(f"{title}: {len(material)} file(s) linked")

    print(f"\nCourse '{course_name}' (id {cid}) is ready in the app.")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
