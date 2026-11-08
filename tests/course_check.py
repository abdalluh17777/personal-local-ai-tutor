"""Check a loaded course with the real model, on a copy of the live data: the learner's sessions, memory,
mistakes and words are not touched.

    uv run python tests/course_check.py <work folder> ["English Stage 2 (B2)"]

Copies the live database into <work folder>, starts a second server on port 8799 that uses the copy, and
runs short lessons in a few topics of a course (default "English Stage 1 (B1)"): questions whose answers the
lesson teaches, answers with mistakes, and a paragraph sent for feedback. Prints each reply with its cited material and feedback, and
checks that the reply contains what the lesson teaches. Ollama must be running.
"""

import json
import os
import re
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
PORT = 8799
BASE = f"http://127.0.0.1:{PORT}"
NARRATIVE = ("Please check my paragraph. When I was ten years old, I feel very nervous on my first day in a new school. "
             "My family moved to a new city because my father get a new job. I didn't knew anyone in my class and the "
             "teacher speak very fast. In the break I sat alone, I was sad and I want to go home. Then a boy ask me to "
             "play football with him. After that day, school become easier for me.")

# course -> [(topic title prefix, [(message, [patterns: the reply must match one in each inner list;
#                                              "!...": must not match])])]
COURSES = {}
COURSES["English Stage 2 (B2)"] = [
    ("Lesson 03:", [
        ("Which is correct: 'I have lived in Mexico when I was a child' or 'I lived in Mexico when I was a child'?",
         [[r"I lived in Mexico when I was a child"]]),
    ]),
    ("Lesson 07:", [
        ("Is this sentence correct: 'I enjoy to read psychology books.'?", [[r"enjoy reading"]]),
    ]),
    ("Lesson 18:", [
        ("Is this a correct sentence: 'I like psychology it is very interesting.'?",
         [[r"run-on|fused"], [r"psychology\. It|psychology; it|psychology, and it|psychology because it"]]),
    ]),
    ("Lesson 23:", [
        ("Please change this sentence to the passive voice: 'The government moved Japanese Americans to camps in 1942.'",
         [[r"Japanese Americans were moved"]]),
    ]),
    ("Lesson 30:", [
        (NARRATIVE, [[r"topic sentence"], [r"\bfelt\b"], [r"didn't know|did not know"], [r"!Thanks for telling me"]]),
    ]),
]
ORIGINAL = ("Stereotype threat is a situational predicament in which people feel themselves to be at risk of "
            "conforming to stereotypes about their social group.")
COURSES["English Stage 3 (C1)"] = [
    ("Lesson 14:", [
        ("Please check my paraphrase. Original: " + ORIGINAL + " Paraphrase: Stereotype threat happens when people "
         "feel themselves to be at risk of confirming a negative belief about their group.",
         [[r"feel themselves to be at risk"], [r"!\b(perfect|excellent) paraphrase"]]),
    ]),
    ("Lesson 44:", [
        ("Make this claim more cautious with hedging: 'Social media causes depression in teenagers.'",
         [[r"\b(may|might|could|suggests?|appears?|likely|associated|linked)\b"]]),
    ]),
    ("Lesson 06:", [
        ("Do I need a comma in this sentence: 'Although the study was small the results were significant.'?",
         [[r"small, the results"], [r"!(?i:do not need a comma|don't need a comma|no comma is needed|no, you do not)"]]),
    ]),
    ("Lesson 43:", [
        ("What fallacy is this: 'Everyone in my class uses this app, so it must be good.'?",
         [[r"bandwagon|ad populum|appeal to (the )?(popularity|majority|people|common belief)"]]),
    ]),
    ("Lesson 22:", [
        ("Is this a good thesis statement: 'This essay is about sleep.'?",
         [[r"announce|declaration|too (broad|general|vague)|not an? (claim|position|argument|arguable)|arguable|"
           r"does not (take|make|state)|no (claim|position|controlling idea)|stance|"
           r"(lacks|missing) (a )?(claim|position|controlling idea|focus)"], [r"!(?i:yes, (this|it) is a good thesis)"]]),
    ]),
    ("Lesson 08:", [
        ("Is this sentence correct: 'The studys results were clear, and it's sample was large.'?",
         [[r"study's"], [r"\bits sample\b"], [r"!studies'"]]),
    ]),
]
COURSES["English Stage 4 (Psychology)"] = [
    ("Lesson 13:", [
        ("In my study, students either take a 60-minute nap or stay awake, and then I count how many of 40 word "
         "pairs they recall. What is the independent variable and what is the dependent variable?",
         [[r"\bindependent variable[\s\S]{0,160}\b(nap|sleep|condition|rest)"],
          [r"\bdependent variable[\s\S]{0,160}\b(recall|word pairs|remember)"]]),
    ]),
    ("Lesson 11:", [
        ("What is the difference between test-retest reliability and internal consistency?",
         [[r"over time|different (times|occasions)|later|twice|second time"],
          [r"items|split-half|Cronbach"]]),
    ]),
    ("Lesson 30:", [
        ("If p = .03, does that mean there is only a 3% chance that the null hypothesis is true?",
         [[r"\bno\b|not|misinterpret|misconception|misunderstanding"],
          [r"if the null hypothesis (were|is) (actually |really |indeed )?true|assuming (that )?the null|"
           r"null hypothesis (were|is) (actually |really )?true|null (were|is) true"]]),
    ]),
    ("Lesson 32:", [
        ("My results are t(18) = 3.95, p < .001. Can I write in my discussion: 'This proves that napping improves "
         "memory'?",
         [[r"suggests?|support|consistent with|evidence|hedg"]]),
    ]),
    ("Lesson 33:", [
        ("How do I cite a journal article by three authors in the text in APA 7th edition? The authors are Smith, "
         "Jones and Lee, 2019.",
         [[r"Smith et al\.,? \(?2019"], [r"!Smith, Jones,? (and|&) Lee,? \(?2019\)?[^.]*first"]]),
    ]),
    ("Lesson 17:", [
        ("Is this sentence correct: 'The results shows that sleep effect memory.'?",
         [[r"results show\b"], [r"\baffects?\b|\beffect on\b|associated with|correlated with|linked (to|with)"]]),
    ]),
]
COURSES["English Stage 1 (B1)"] = [
    ("Lesson 02:", [
        ("Let's start the lesson.", []),
        ("How many syllables are in the word 'watermelon', and which syllable is stressed?",
         [[r"\bfour\b", r"\b4\b"], [r"\bWA\b", r"first syllable", r"1st syllable", r"syllable 1\b"],
          [r"!\bME\b|second syllable|third syllable"]]),
    ]),
    ("Lesson 23:", [
        ("Is the -s ending in 'watches' pronounced /s/, /z/ or /ɪz/? And in 'dogs'?",
         [[r"/ɪz/|/iz/|/əz/|\biz\b"], [r"/z/"]]),
    ]),
    ("Lesson 19:", [
        ("I am practising questions for an interview. Is my question correct: Where you did grow up?",
         [[r"Where did you grow up"]]),
    ]),
    ("Lesson 32:", [
        ("What is the -ed sound in 'wanted', 'played' and 'walked'?",
         [[r"/ɪd/|/id/|/əd/"], [r"/d/"], [r"/t/"]]),
    ]),
    ("Lesson 06:", [
        ("How should I start a formal email to my professor? I know her name, it is Dr. Smith.",
         [[r"Dear (Dr\.|Professor) Smith"]]),
    ]),
]


def copy_database(target: Path) -> None:
    from tutor import config
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        target.unlink()
    source = sqlite3.connect(f"file:{config.DB_PATH}?mode=ro", uri=True)
    dest = sqlite3.connect(target)
    try:
        source.backup(dest)
    finally:
        dest.close()
        source.close()


def turn(client: httpx.Client, session_id: int, text: str) -> dict:
    events = []
    t0 = time.perf_counter()
    first = None
    with client.stream("POST", f"/api/sessions/{session_id}/messages", json={"text": text}, timeout=300) as r:
        r.raise_for_status()
        for line in r.iter_lines():
            if line.startswith("data: "):
                event = json.loads(line[6:])
                if event["type"] == "token" and first is None:
                    first = time.perf_counter() - t0
                events.append(event)
    done = next((e for e in events if e["type"] == "reply_done"), {})
    return {"reply": "".join(e["text"] for e in events if e["type"] == "token").strip(),
            "sources": done.get("sources", []), "unknown": done.get("unknown_citations"),
            "feedback": next((e["feedback"] for e in events if e["type"] == "feedback"), {}),
            "errors": [e["message"] for e in events if e["type"] == "error"],
            "first_token": first, "seconds": time.perf_counter() - t0}


def main(workdir: str, course_name: str = "English Stage 1 (B1)") -> int:
    work = Path(workdir)
    lessons = COURSES[course_name]
    copy_database(work / "data" / "tutor.sqlite3")
    env = {**os.environ, "TUTOR_PORT": str(PORT), "TUTOR_DATA_DIR": str(work / "data"),
           "TUTOR_MATERIALS_DIR": str(work / "materials"), "PYTHONIOENCODING": "utf-8"}
    log = open(work / "server.err.log", "w", encoding="utf-8")
    server = subprocess.Popen([sys.executable, "-m", "tutor"], cwd=ROOT, env=env, stdout=subprocess.DEVNULL, stderr=log)
    failures = 0
    try:
        client = httpx.Client(base_url=BASE, timeout=300)
        for _ in range(60):
            try:
                client.get("/api/profile")
                break
            except httpx.HTTPError:
                time.sleep(0.5)
        course = next(c for c in client.get("/api/courses").json() if c["name"] == course_name)
        topics = client.get(f"/api/courses/{course['id']}/topics").json()
        print(f"{course_name}: {len(topics)} topics")
        for prefix, messages in lessons:
            topic = next(t for t in topics if t["title"].startswith(prefix))
            session = client.post("/api/sessions", json={"course_id": course["id"], "topic_id": topic["id"]}).json()["session"]
            print(f"\n=== {topic['title']} (material: {len(topic.get('document_ids', []))} files)")
            for text, expected in messages:
                result = turn(client, session["id"], text)
                cited = sorted({s.get("filename", "?") for s in result["sources"] if s.get("cited")})
                # Each inner list: the reply must match one of its patterns; "!pattern": it must not match.
                # Markdown emphasis is ignored ("enjoy **reading**")
                reply = result["reply"].replace("**", "").replace("*", "")
                missing = [alts for alts in expected if not any(
                    not re.search(p[1:], reply) if p.startswith("!") else re.search(p, reply, re.I)
                    for p in alts)]
                failures += bool(missing) + bool(result["errors"])
                print(f"\n> {text}\n({result['first_token'] or 0:.2f}s to first word, {result['seconds']:.1f}s total)")
                print(result["reply"])
                print(f"cited: {cited or '-'} | passages sent: {len(result['sources'])} | unknown labels: {result['unknown']}")
                if result["feedback"]:
                    print("feedback:", json.dumps(result["feedback"], ensure_ascii=False))
                if result["errors"]:
                    print("ERRORS:", result["errors"])
                print("CHECK:", "ok" if not missing else f"MISSING {missing}")
            client.post(f"/api/sessions/{session['id']}/end")
    finally:
        server.terminate()
        server.wait(timeout=30)
        log.close()
    print(f"\n{failures} failed check(s)")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main(*sys.argv[1:3]))
