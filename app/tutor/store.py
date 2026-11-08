"""Data access for courses, materials, the study plan and the learner's memory.

All writes go through this class. The model never touches the database directly:
the tutor proposes changes, and this layer validates and de-duplicates them.
"""

import json
import re
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import srs
from .db import connect, init_schema, now


def local_today() -> str:
    """The learner's calendar date (the app runs on their own computer)."""
    return datetime.now().date().isoformat()

PROFILE_DEFAULTS = {
    "name": "",
    "english_level": "B1",
    "teaching_language": "english",   # Arabic only when the learner asks for it
    "goals": "",
    "interests": "",
    "notes": "",
    "voice": "af_heart",
    "speech_speed": "1.0",
    "turn_patience": "normal",   # live conversation: how long to wait in a pause before answering
    "careful_grammar": "off",    # "on": the model thinks before answering typed "is this correct?" questions
}
TURN_PATIENCE = ("short", "normal", "long")
ON_OFF = ("on", "off")
ENGLISH_LEVELS = ("A2", "B1", "B2", "C1", "C2")
TEACHING_LANGUAGES = ("english", "english_with_arabic_support", "arabic")
MEMORY_KINDS = ("preference", "goal", "background", "interest", "note")
MISTAKE_CATEGORIES = ("grammar", "vocabulary", "spelling", "word_order", "punctuation")

VOCAB_AGAIN_AFTER = timedelta(minutes=10)   # a forgotten word comes back in the same study block


class NotFound(LookupError):
    pass


class Duplicate(ValueError):
    pass


_CURLY_APOSTROPHES = str.maketrans("’‘", "''")


def normalize(text: str) -> str:
    """Lowercase, drop punctuation, collapse spaces: used to compare user-facing text. Curly apostrophes
    count as straight ones, so "don’t" typed on a phone matches the observer's "don't"."""
    text = text.translate(_CURLY_APOSTROPHES)
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s']", " ", text.lower())).strip()


def _similar(a: str, b: str, threshold: float = 0.8) -> bool:
    wa, wb = set(normalize(a).split()), set(normalize(b).split())
    if not wa or not wb:
        return False
    return len(wa & wb) / len(wa | wb) >= threshold


def _rows(rows) -> list[dict]:
    return [dict(r) for r in rows]


class Store:
    def __init__(self, path: Path | str):
        self.conn = connect(path)
        init_schema(self.conn)
        self.lock = threading.RLock()

    # --- low-level helpers ------------------------------------------------
    def _all(self, sql: str, args=()) -> list[dict]:
        with self.lock:
            return _rows(self.conn.execute(sql, args).fetchall())

    def _one(self, sql: str, args=()) -> dict | None:
        with self.lock:
            row = self.conn.execute(sql, args).fetchone()
            return dict(row) if row else None

    def _exec(self, sql: str, args=()) -> int:
        with self.lock:
            return self.conn.execute(sql, args).lastrowid

    @contextmanager
    def transaction(self):
        with self.lock:
            self.conn.execute("BEGIN")
            try:
                yield self.conn
            except BaseException:
                self.conn.execute("ROLLBACK")
                raise
            self.conn.execute("COMMIT")

    def _require(self, table: str, item_id: int) -> dict:
        row = self._one(f"SELECT * FROM {table} WHERE id = ?", (item_id,))
        if not row:
            raise NotFound(f"{table} {item_id} not found")
        return row

    def backup(self, target: Path) -> Path:
        """Consistent copy of the live database (includes changes still in the WAL file)."""
        target.parent.mkdir(parents=True, exist_ok=True)
        with self.lock:
            dest = sqlite3.connect(target)
            try:
                self.conn.backup(dest)
            finally:
                dest.close()
        return target

    # --- profile ----------------------------------------------------------
    def get_profile(self) -> dict:
        row = self._one("SELECT data FROM profile WHERE id = 1")
        return {**PROFILE_DEFAULTS, **(json.loads(row["data"]) if row else {})}

    def update_profile(self, changes: dict) -> dict:
        profile = self.get_profile()
        for key, value in changes.items():
            if key not in PROFILE_DEFAULTS:
                continue
            value = str(value).strip()
            if key == "english_level" and value not in ENGLISH_LEVELS:
                raise ValueError(f"english_level must be one of {ENGLISH_LEVELS}")
            if key == "teaching_language" and value not in TEACHING_LANGUAGES:
                raise ValueError(f"teaching_language must be one of {TEACHING_LANGUAGES}")
            if key == "speech_speed":
                try:
                    value = f"{min(max(float(value), 0.6), 1.4):.2f}"
                except ValueError:
                    raise ValueError("speech_speed must be a number") from None
            if key == "voice" and not re.fullmatch(r"[a-z]{2}_[a-z]+", value):
                raise ValueError("Unknown voice")
            if key == "turn_patience" and value not in TURN_PATIENCE:
                raise ValueError(f"turn_patience must be one of {TURN_PATIENCE}")
            if key == "careful_grammar" and value not in ON_OFF:
                raise ValueError(f"careful_grammar must be one of {ON_OFF}")
            profile[key] = value[:2000]
        self._exec(
            "INSERT INTO profile(id, data) VALUES (1, ?) ON CONFLICT(id) DO UPDATE SET data = excluded.data",
            (json.dumps(profile, ensure_ascii=False),),
        )
        return profile

    # --- courses ----------------------------------------------------------
    def list_courses(self) -> list[dict]:
        return self._all("""
            SELECT c.*,
                   (SELECT COUNT(*) FROM documents d WHERE d.course_id = c.id) AS documents,
                   (SELECT COUNT(*) FROM topics t WHERE t.course_id = c.id) AS topics
            FROM courses c ORDER BY c.created_at, c.id
        """)

    def get_course(self, course_id: int) -> dict:
        return self._require("courses", course_id)

    def create_course(self, name: str, description: str = "") -> dict:
        name = name.strip()
        if not name:
            raise ValueError("Course name is required")
        try:
            course_id = self._exec(
                "INSERT INTO courses(name, description, created_at) VALUES (?, ?, ?)",
                (name[:200], description.strip()[:2000], now()),
            )
        except sqlite3.IntegrityError:
            raise Duplicate(f"A course named '{name}' already exists") from None
        return self.get_course(course_id)

    def update_course(self, course_id: int, name: str | None = None, description: str | None = None) -> dict:
        course = self.get_course(course_id)
        name = (name if name is not None else course["name"]).strip()
        if not name:
            raise ValueError("Course name is required")
        description = description if description is not None else course["description"]
        try:
            self._exec("UPDATE courses SET name = ?, description = ? WHERE id = ?",
                       (name[:200], description.strip()[:2000], course_id))
        except sqlite3.IntegrityError:
            raise Duplicate(f"A course named '{name}' already exists") from None
        return self.get_course(course_id)

    def delete_course(self, course_id: int) -> None:
        self.get_course(course_id)
        self._exec("DELETE FROM courses WHERE id = ?", (course_id,))

    # --- documents and passages -------------------------------------------
    def add_document(self, course_id: int, filename: str, stored_path: str, sha256: str,
                     pages: int | None, chunks: list[tuple[int | None, str]]) -> dict:
        self.get_course(course_id)
        if self._one("SELECT id FROM documents WHERE course_id = ? AND sha256 = ?", (course_id, sha256)):
            raise Duplicate(f"'{filename}' is already in this course")
        chars = sum(len(text) for _, text in chunks)
        with self.transaction() as conn:
            doc_id = conn.execute(
                "INSERT INTO documents(course_id, filename, stored_path, sha256, pages, chars, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (course_id, filename, stored_path, sha256, pages, chars, now()),
            ).lastrowid
            conn.executemany(
                "INSERT INTO passages(document_id, course_id, page, position, text) VALUES (?, ?, ?, ?, ?)",
                [(doc_id, course_id, page, i, text) for i, (page, text) in enumerate(chunks)],
            )
        return self.get_document(doc_id)

    def get_document(self, document_id: int) -> dict:
        doc = self._one("""
            SELECT d.*, (SELECT COUNT(*) FROM passages p WHERE p.document_id = d.id) AS passages
            FROM documents d WHERE d.id = ?
        """, (document_id,))
        if not doc:
            raise NotFound(f"document {document_id} not found")
        return doc

    def list_documents(self, course_id: int) -> list[dict]:
        return self._all("""
            SELECT d.*, (SELECT COUNT(*) FROM passages p WHERE p.document_id = d.id) AS passages
            FROM documents d WHERE d.course_id = ? ORDER BY d.created_at, d.id
        """, (course_id,))

    def delete_document(self, document_id: int) -> dict:
        doc = self.get_document(document_id)
        self._exec("DELETE FROM documents WHERE id = ?", (document_id,))
        return doc

    def has_passages(self, course_id: int) -> bool:
        return self._one("SELECT 1 AS x FROM passages WHERE course_id = ? LIMIT 1", (course_id,)) is not None

    def search_passages(self, course_id: int, query: str, limit: int,
                        document_ids: list[int] | None = None) -> list[dict]:
        """Best-matching passages of one course only (never mixes courses), optionally of some documents only."""
        only = ""
        args: list = [query, course_id]
        if document_ids:
            only = f" AND p.document_id IN ({','.join('?' * len(document_ids))})"
            args += list(document_ids)
        return self._all(f"""
            SELECT p.id, p.page, p.text, d.filename, bm25(passages_fts) AS score
            FROM passages_fts
            JOIN passages p ON p.id = passages_fts.rowid
            JOIN documents d ON d.id = p.document_id
            WHERE passages_fts MATCH ? AND p.course_id = ?{only}
            ORDER BY score LIMIT ?
        """, (*args, limit))

    def first_passages(self, document_ids: list[int], limit: int) -> list[dict]:
        """The opening passages of documents, in the given document order (a topic's starting point)."""
        rows = []
        for doc_id in document_ids:
            rows += self._all("""
                SELECT p.id, p.page, p.text, d.filename FROM passages p JOIN documents d ON d.id = p.document_id
                WHERE p.document_id = ? ORDER BY p.position LIMIT ?
            """, (doc_id, limit - len(rows)))
            if len(rows) >= limit:
                break
        return rows

    # --- topic material -----------------------------------------------------
    def link_topic_document(self, topic_id: int, document_id: int) -> None:
        topic, doc = self.get_topic(topic_id), self.get_document(document_id)
        if topic["course_id"] != doc["course_id"]:
            raise ValueError("The document belongs to another course")
        pos = self._one("SELECT COALESCE(MAX(position), -1) + 1 AS p FROM topic_documents WHERE topic_id = ?",
                        (topic_id,))["p"]
        self._exec("INSERT OR IGNORE INTO topic_documents(topic_id, document_id, position) VALUES (?, ?, ?)",
                   (topic_id, document_id, pos))

    def unlink_topic_document(self, topic_id: int, document_id: int) -> None:
        self._exec("DELETE FROM topic_documents WHERE topic_id = ? AND document_id = ?", (topic_id, document_id))

    def topic_document_ids(self, topic_id: int) -> list[int]:
        return [r["document_id"] for r in self._all(
            "SELECT document_id FROM topic_documents WHERE topic_id = ? ORDER BY position", (topic_id,))]

    def sample_passages(self, course_id: int, limit: int) -> list[dict]:
        """Passages spread evenly across a course, in reading order (used to suggest topics)."""
        rows = self._all("""
            SELECT p.id, p.page, p.text, d.filename FROM passages p
            JOIN documents d ON d.id = p.document_id
            WHERE p.course_id = ? ORDER BY d.created_at, d.id, p.position
        """, (course_id,))
        if len(rows) <= limit:
            return rows
        step = len(rows) / limit
        return [rows[int(i * step)] for i in range(limit)]

    # --- topics -----------------------------------------------------------
    def list_topics(self, course_id: int) -> list[dict]:
        topics = self._all("SELECT * FROM topics WHERE course_id = ? ORDER BY position", (course_id,))
        for t in topics:
            t["document_ids"] = self.topic_document_ids(t["id"])
        return topics

    def get_topic(self, topic_id: int) -> dict:
        return self._require("topics", topic_id)

    def add_topic(self, course_id: int, title: str) -> dict:
        self.get_course(course_id)
        title = title.strip()
        if not title:
            raise ValueError("Topic title is required")
        for t in self.list_topics(course_id):
            if normalize(t["title"]) == normalize(title):
                raise Duplicate(f"Topic '{title}' already exists")
        pos = self._one("SELECT COALESCE(MAX(position), -1) + 1 AS p FROM topics WHERE course_id = ?",
                        (course_id,))["p"]
        topic_id = self._exec(
            "INSERT INTO topics(course_id, title, position, created_at) VALUES (?, ?, ?, ?)",
            (course_id, title[:300], pos, now()),
        )
        return self.get_topic(topic_id)

    def update_topic(self, topic_id: int, title: str | None = None, notes: str | None = None) -> dict:
        topic = self.get_topic(topic_id)
        title = (title if title is not None else topic["title"]).strip()
        if not title:
            raise ValueError("Topic title is required")
        for t in self.list_topics(topic["course_id"]):
            if t["id"] != topic_id and normalize(t["title"]) == normalize(title):
                raise Duplicate(f"Topic '{title}' already exists")
        notes = notes if notes is not None else topic["notes"]
        self._exec("UPDATE topics SET title = ?, notes = ? WHERE id = ?", (title[:300], notes[:2000], topic_id))
        return self.get_topic(topic_id)

    def move_topic(self, topic_id: int, direction: int) -> list[dict]:
        topic = self.get_topic(topic_id)
        topics = self.list_topics(topic["course_id"])
        i = next(i for i, t in enumerate(topics) if t["id"] == topic_id)
        j = i + (1 if direction > 0 else -1)
        if 0 <= j < len(topics):
            topics[i], topics[j] = topics[j], topics[i]
            with self.transaction() as conn:
                for pos, t in enumerate(topics):
                    conn.execute("UPDATE topics SET position = ? WHERE id = ?", (pos, t["id"]))
        return self.list_topics(topic["course_id"])

    def delete_topic(self, topic_id: int) -> None:
        self.get_topic(topic_id)
        self._exec("DELETE FROM topics WHERE id = ?", (topic_id,))

    def mark_topic_studied(self, topic_id: int) -> None:
        """After a study session: a new topic becomes 'learning' with its first review due tomorrow."""
        topic = self.get_topic(topic_id)
        if topic["status"] == "new":
            due = srs.due_in_days(1)
            self._exec("UPDATE topics SET status = 'learning', due_at = ? WHERE id = ?", (due, topic_id))

    def review_topic(self, topic_id: int, grade: str) -> dict:
        topic = self.get_topic(topic_id)
        stage, due = srs.schedule(topic["stage"], grade)
        with self.transaction() as conn:
            conn.execute("UPDATE topics SET status = 'reviewing', stage = ?, due_at = ? WHERE id = ?",
                         (stage, due, topic_id))
            conn.execute("INSERT INTO attempts(topic_id, method, grade, created_at) VALUES (?, 'self_report', ?, ?)",
                         (topic_id, grade, now()))
        return self.get_topic(topic_id)

    # --- sessions and messages --------------------------------------------
    def create_session(self, course_id: int | None, topic_id: int | None) -> dict:
        if course_id is not None:
            self.get_course(course_id)
        if topic_id is not None:
            topic = self.get_topic(topic_id)
            if course_id is not None and topic["course_id"] != course_id:
                raise ValueError("Topic does not belong to this course")
            course_id = topic["course_id"]
        session_id = self._exec("INSERT INTO sessions(course_id, topic_id, started_at) VALUES (?, ?, ?)",
                                (course_id, topic_id, now()))
        return self.get_session(session_id)

    # The chat list shows a title for every session: the learner's own, else the topic, else how the chat began
    _SESSION_SELECT = """
        SELECT s.*, c.name AS course_name, t.title AS topic_title,
               (SELECT COUNT(*) FROM messages m WHERE m.session_id = s.id) AS message_count,
               (SELECT m.content FROM messages m WHERE m.session_id = s.id AND m.role = 'user'
                ORDER BY m.id LIMIT 1) AS first_message
        FROM sessions s
        LEFT JOIN courses c ON c.id = s.course_id
        LEFT JOIN topics t ON t.id = s.topic_id
    """

    @staticmethod
    def _titled(session: dict) -> dict:
        first = " ".join((session.pop("first_message") or "").split())
        if len(first) > 60:
            first = first[:59].rstrip() + "…"
        session["title"] = session["title"] or session["topic_title"] or first or None
        return session

    def get_session(self, session_id: int) -> dict:
        session = self._one(self._SESSION_SELECT + " WHERE s.id = ?", (session_id,))
        if not session:
            raise NotFound(f"session {session_id} not found")
        return self._titled(session)

    def list_sessions(self, limit: int = 30) -> list[dict]:
        """Recent sessions. An ended session with no messages and no summary (left from before empty sessions
        were discarded) is not shown; the open session always is, because the page resumes it from this list."""
        return [self._titled(s) for s in self._all(self._SESSION_SELECT + """
            WHERE s.ended_at IS NULL OR s.summary IS NOT NULL
               OR EXISTS (SELECT 1 FROM messages m WHERE m.session_id = s.id)
            ORDER BY s.started_at DESC, s.id DESC LIMIT ?
        """, (limit,))]

    def rename_session(self, session_id: int, title: str) -> dict:
        self.get_session(session_id)
        title = " ".join(title.split())
        if not 1 <= len(title) <= 120:
            raise ValueError("A chat title must be 1 to 120 characters long")
        self._exec("UPDATE sessions SET title = ? WHERE id = ?", (title, session_id))
        return self.get_session(session_id)

    def delete_session(self, session_id: int) -> None:
        """Remove an ended session and its messages. What was learned in it (words, mistakes, memories) stays."""
        if not self.get_session(session_id)["ended_at"]:
            raise ValueError("The open chat cannot be deleted. End it first.")
        self._exec("DELETE FROM sessions WHERE id = ? AND ended_at IS NOT NULL", (session_id,))

    def reopen_session(self, session_id: int) -> dict:
        """Continue an ended session. Its summary stays until it is ended again, when it is written anew."""
        if not self.get_session(session_id)["ended_at"]:
            raise ValueError("This chat is still open")
        self._exec("UPDATE sessions SET ended_at = NULL WHERE id = ?", (session_id,))
        return self.get_session(session_id)

    def open_sessions(self) -> list[dict]:
        return self._all("SELECT id FROM sessions WHERE ended_at IS NULL ORDER BY started_at, id")

    def end_session(self, session_id: int, summary: str | None, next_step: str | None) -> dict:
        session = self.get_session(session_id)
        if session["message_count"] == 0:
            # Nothing happened: do not keep an empty session in the history
            self._exec("DELETE FROM sessions WHERE id = ?", (session_id,))
            return {**session, "ended_at": now(), "discarded": True}
        self._exec("UPDATE sessions SET ended_at = ?, summary = ?, next_step = ? WHERE id = ?",
                   (now(), summary, next_step, session_id))
        if session["topic_id"] and session["message_count"] >= 2:
            self.mark_topic_studied(session["topic_id"])
        return self.get_session(session_id)

    def add_message(self, session_id: int, role: str, content: str, sources: list | None = None) -> int:
        return self._exec(
            "INSERT INTO messages(session_id, role, content, sources, created_at) VALUES (?, ?, ?, ?, ?)",
            (session_id, role, content, json.dumps(sources, ensure_ascii=False) if sources else None, now()),
        )

    def delete_message(self, message_id: int) -> None:
        self._exec("DELETE FROM messages WHERE id = ?", (message_id,))

    def set_message_feedback(self, message_id: int, feedback: dict) -> None:
        self._exec("UPDATE messages SET feedback = ? WHERE id = ?",
                   (json.dumps(feedback, ensure_ascii=False), message_id))

    def messages(self, session_id: int, limit: int | None = None) -> list[dict]:
        rows = self._all("""
            SELECT * FROM (SELECT * FROM messages WHERE session_id = ? ORDER BY id DESC LIMIT ?)
            ORDER BY id
        """, (session_id, limit if limit is not None else -1))
        for r in rows:
            r["sources"] = json.loads(r["sources"]) if r["sources"] else []
            r["feedback"] = json.loads(r["feedback"]) if r["feedback"] else None
        return rows

    def last_summary(self, course_id: int | None = None) -> dict | None:
        return self._one("""
            SELECT s.summary, s.next_step, s.ended_at, t.title AS topic_title
            FROM sessions s LEFT JOIN topics t ON t.id = s.topic_id
            WHERE s.summary IS NOT NULL AND (? IS NULL OR s.course_id = ?)
            ORDER BY s.ended_at DESC LIMIT 1
        """, (course_id, course_id))

    # --- memories ---------------------------------------------------------
    def list_memories(self, active_only: bool = False) -> list[dict]:
        sql = "SELECT * FROM memories" + (" WHERE active = 1" if active_only else "")
        return self._all(sql + " ORDER BY active DESC, updated_at DESC")

    def add_memory(self, kind: str, content: str, origin: str, session_id: int | None = None) -> dict | None:
        """Add a memory unless one already says the same thing. Returns None for duplicates. A memory the
        learner paused stays paused when the tutor proposes it again; the learner adding it again resumes it."""
        kind = kind if kind in MEMORY_KINDS else "note"
        content = " ".join(content.split())[:500]
        if not content:
            raise ValueError("Memory content is required")
        for m in self.list_memories():          # active ones first
            if _similar(m["content"], content):
                if m["active"] or origin != "user":
                    return None
                return self.update_memory(m["id"], active=True)
        ts = now()
        memory_id = self._exec(
            "INSERT INTO memories(kind, content, origin, session_id, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
            (kind, content, origin, session_id, ts, ts),
        )
        return self._require("memories", memory_id)

    def update_memory(self, memory_id: int, content: str | None = None, kind: str | None = None,
                      active: bool | None = None) -> dict:
        m = self._require("memories", memory_id)
        content = " ".join((content if content is not None else m["content"]).split())[:500]
        if not content:
            raise ValueError("Memory content is required")
        kind = kind if kind in MEMORY_KINDS else m["kind"]
        active = int(active) if active is not None else m["active"]
        self._exec("UPDATE memories SET content = ?, kind = ?, active = ?, updated_at = ? WHERE id = ?",
                   (content, kind, active, now(), memory_id))
        return self._require("memories", memory_id)

    def delete_memory(self, memory_id: int) -> None:
        self._require("memories", memory_id)
        self._exec("DELETE FROM memories WHERE id = ?", (memory_id,))

    # --- vocabulary -------------------------------------------------------
    _VOCAB_WITH_LIST = """SELECT v.*, l.title AS list_title FROM vocab v
        LEFT JOIN word_list_items i ON i.vocab_id = v.id AND i.introduced_on != 'known'
        LEFT JOIN word_lists l ON l.id = i.list_id"""

    def list_vocab(self) -> list[dict]:
        return self._all(f"{self._VOCAB_WITH_LIST} ORDER BY v.created_at DESC, v.id DESC")

    def add_vocab(self, word: str, meaning: str, example: str = "", origin: str = "user",
                  course_id: int | None = None, session_id: int | None = None) -> tuple[dict, bool]:
        """Returns (row, created). A word seen again just raises its seen_count."""
        word = " ".join(word.split())[:80]
        meaning = " ".join(meaning.split())[:500]
        if not word or not meaning:
            raise ValueError("Word and meaning are required")
        existing = self._one("SELECT * FROM vocab WHERE word = ?", (word,))
        if existing:
            # The learner adding a word they already have is refused (409), not counted as seeing it again
            if origin != "user":
                self._exec("UPDATE vocab SET seen_count = seen_count + 1 WHERE id = ?", (existing["id"],))
            return self._require("vocab", existing["id"]), False
        vocab_id = self._exec(
            "INSERT INTO vocab(word, meaning, example, course_id, session_id, origin, due_at, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (word, meaning, " ".join(example.split())[:500], course_id, session_id, origin, now(), now()),
        )
        return self._require("vocab", vocab_id), True

    def update_vocab(self, vocab_id: int, meaning: str | None = None, example: str | None = None) -> dict:
        v = self._require("vocab", vocab_id)
        meaning = " ".join((meaning if meaning is not None else v["meaning"]).split())[:500]
        if not meaning:
            raise ValueError("Meaning is required")
        example = " ".join((example if example is not None else v["example"]).split())[:500]
        self._exec("UPDATE vocab SET meaning = ?, example = ? WHERE id = ?", (meaning, example, vocab_id))
        return self._require("vocab", vocab_id)

    def due_vocab(self, limit: int = 20) -> list[dict]:
        return self._all(f"{self._VOCAB_WITH_LIST} WHERE v.due_at <= ? ORDER BY v.due_at, v.id LIMIT ?", (now(), limit))

    def review_vocab(self, vocab_id: int, grade: str) -> dict:
        v = self._require("vocab", vocab_id)
        stage, due = srs.schedule(v["stage"], grade, again_after=VOCAB_AGAIN_AFTER)
        self._exec("UPDATE vocab SET stage = ?, due_at = ? WHERE id = ?", (stage, due, vocab_id))
        return self._require("vocab", vocab_id)

    def delete_vocab(self, vocab_id: int) -> None:
        self._require("vocab", vocab_id)
        self._exec("DELETE FROM vocab WHERE id = ?", (vocab_id,))

    # --- word lists -------------------------------------------------------
    def save_word_list(self, slug: str, title: str, description: str, source: str, items: list[dict],
                       daily_new: int = 10) -> dict:
        """Create or update a list. Words not yet introduced take the new order and texts; words already in
        the learner's vocabulary are left as they are. daily_new applies to a new list only: the learner's own
        choice is never overwritten. Safe to repeat."""
        slug = re.sub(r"[^a-z0-9-]", "", slug.lower())[:60]
        if not slug or not title.strip():
            raise ValueError("A word list needs a slug and a title")
        with self.transaction() as conn:
            row = conn.execute("SELECT id FROM word_lists WHERE slug = ?", (slug,)).fetchone()
            if row:
                list_id = row["id"]
                conn.execute("UPDATE word_lists SET title = ?, description = ?, source = ? WHERE id = ?",
                             (title.strip(), description.strip(), source.strip(), list_id))
            else:
                list_id = conn.execute(
                    "INSERT INTO word_lists(slug, title, description, source, daily_new, created_at) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (slug, title.strip(), description.strip(), source.strip(), max(0, min(50, int(daily_new))),
                     now())).lastrowid
            for position, item in enumerate(items):
                word = " ".join(str(item.get("word", "")).split())[:80]
                meaning = " ".join(str(item.get("meaning", "")).split())[:500]
                if not word or not meaning:
                    continue
                conn.execute("""
                    INSERT INTO word_list_items(list_id, position, word, meaning, example, forms, pos)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(list_id, word) DO UPDATE SET position = excluded.position,
                        meaning = excluded.meaning, example = excluded.example, forms = excluded.forms,
                        pos = excluded.pos
                    WHERE introduced_on IS NULL
                """, (list_id, position, word, meaning, " ".join(str(item.get("example", "")).split())[:500],
                      str(item.get("forms", ""))[:300], str(item.get("pos", ""))[:30]))
        return self.word_list(list_id)

    def word_list(self, list_id: int) -> dict:
        row = self._one("""
            SELECT l.*, COUNT(i.id) AS total,
                   SUM(i.introduced_on IS NOT NULL) AS introduced,
                   SUM(i.introduced_on = ?) AS introduced_today
            FROM word_lists l LEFT JOIN word_list_items i ON i.list_id = l.id
            WHERE l.id = ? GROUP BY l.id
        """, (local_today(), list_id))
        if not row:
            raise NotFound(f"word list {list_id} not found")
        return {**row, "introduced": row["introduced"] or 0, "introduced_today": row["introduced_today"] or 0}

    def word_lists(self) -> list[dict]:
        return [self.word_list(r["id"]) for r in self._all("SELECT id FROM word_lists ORDER BY created_at, id")]

    def update_word_list(self, list_id: int, daily_new: int) -> dict:
        self.word_list(list_id)
        if not 0 <= int(daily_new) <= 50:
            raise ValueError("daily_new must be between 0 and 50")
        self._exec("UPDATE word_lists SET daily_new = ? WHERE id = ?", (int(daily_new), list_id))
        return self.word_list(list_id)

    def introduce_words(self, today: str | None = None) -> list[dict]:
        """Today's new words from each list join the vocabulary (due at once, then spaced review). Up to
        daily_new per list per day, counted from what was already added today, so a day without study adds
        nothing and nothing piles up. A word the learner already has is marked 'known' and skipped."""
        today = today or local_today()
        added = []
        with self.lock:
            for wl in self._all("SELECT * FROM word_lists WHERE daily_new > 0"):
                done = self._one("SELECT COUNT(*) AS n FROM word_list_items WHERE list_id = ? AND introduced_on = ?",
                                 (wl["id"], today))["n"]
                while done < wl["daily_new"]:
                    item = self._one("""SELECT * FROM word_list_items WHERE list_id = ? AND introduced_on IS NULL
                                        ORDER BY position LIMIT 1""", (wl["id"],))
                    if not item:
                        break
                    existing = self._one("SELECT id FROM vocab WHERE word = ?", (item["word"],))
                    if existing:
                        self._exec("UPDATE word_list_items SET introduced_on = 'known', vocab_id = ? WHERE id = ?",
                                   (existing["id"], item["id"]))
                        continue
                    meaning = f"({item['pos']}) {item['meaning']}" if item["pos"] and item["pos"] != "term" else item["meaning"]
                    if item["forms"]:
                        meaning += f" · Forms: {item['forms']}"
                    row, _ = self.add_vocab(item["word"], meaning, item["example"], origin="auto")
                    self._exec("UPDATE word_list_items SET introduced_on = ?, vocab_id = ? WHERE id = ?",
                               (today, row["id"], item["id"]))
                    added.append({**row, "list": wl["title"]})
                    done += 1
        return added

    # --- English mistakes -------------------------------------------------
    def log_mistake(self, rule: str, category: str, explanation: str, wrong: str, right: str,
                    session_id: int | None = None) -> dict:
        """Record one mistake under its general rule, so repeated patterns become visible."""
        rule = " ".join(rule.split())[:120]
        key = normalize(rule)
        if not key:
            raise ValueError("Mistake rule is required")
        category = category if category in MISTAKE_CATEGORIES else "grammar"
        ts = now()
        with self.transaction() as conn:
            row = conn.execute("SELECT id FROM mistake_patterns WHERE rule_key = ?", (key,)).fetchone()
            if row:
                pattern_id = row["id"]
                # A pattern that comes back is no longer resolved
                conn.execute("UPDATE mistake_patterns SET count = count + 1, last_seen = ?, resolved = 0 "
                             "WHERE id = ?", (ts, pattern_id))
            else:
                pattern_id = conn.execute(
                    "INSERT INTO mistake_patterns(rule, rule_key, category, explanation, count, first_seen, last_seen) "
                    "VALUES (?, ?, ?, ?, 1, ?, ?)",
                    (rule, key, category, explanation[:500], ts, ts),
                ).lastrowid
            conn.execute(
                "INSERT INTO mistake_examples(pattern_id, wrong, right, session_id, created_at) VALUES (?, ?, ?, ?, ?)",
                (pattern_id, wrong[:300], right[:300], session_id, ts),
            )
        return self._require("mistake_patterns", pattern_id)

    def list_mistakes(self, include_resolved: bool = True, limit: int | None = None) -> list[dict]:
        sql = "SELECT * FROM mistake_patterns"
        if not include_resolved:
            sql += " WHERE resolved = 0"
        patterns = self._all(sql + " ORDER BY resolved, count DESC, last_seen DESC LIMIT ?",
                             (limit if limit is not None else -1,))
        for p in patterns:
            p["examples"] = self._all(
                "SELECT wrong, right, created_at FROM mistake_examples WHERE pattern_id = ? "
                "ORDER BY id DESC LIMIT 5", (p["id"],))
        return patterns

    def set_mistake_resolved(self, pattern_id: int, resolved: bool) -> dict:
        self._require("mistake_patterns", pattern_id)
        self._exec("UPDATE mistake_patterns SET resolved = ? WHERE id = ?", (int(resolved), pattern_id))
        return self._require("mistake_patterns", pattern_id)

    def delete_mistake(self, pattern_id: int) -> None:
        self._require("mistake_patterns", pattern_id)
        self._exec("DELETE FROM mistake_patterns WHERE id = ?", (pattern_id,))

    # --- pronunciation -----------------------------------------------------
    def add_pron_attempt(self, text: str, drill: str | None, result: dict) -> int:
        """Save a checked recording (not the audio) and one row per expected or added sound."""
        with self.transaction() as c:
            aid = c.execute("INSERT INTO pron_attempts(text, drill, score, result, created_at) VALUES (?,?,?,?,?)",
                            (text, drill, int(result["score"]), json.dumps(result, ensure_ascii=False), now())).lastrowid
            for w in result["words"]:
                for p in w["phones"]:
                    c.execute("INSERT INTO pron_sounds(attempt_id, phone, status, heard, word) VALUES (?,?,?,?,?)",
                              (aid, p["phone"], p["status"], p.get("heard") or ("" if p["status"] != "good" else None),
                               w["text"]))
                for a in w.get("added", []):
                    c.execute("INSERT INTO pron_sounds(attempt_id, phone, status, heard, word) VALUES (?,?,?,?,?)",
                              (aid, "+", "wrong", a["heard"], w["text"]))
        return aid

    def pron_attempts(self, limit: int = 20) -> list[dict]:
        return self._all("SELECT id, text, drill, score, created_at FROM pron_attempts ORDER BY id DESC LIMIT ?",
                         (limit,))

    def pron_attempt(self, attempt_id: int) -> dict:
        row = self._require("pron_attempts", attempt_id)
        row["result"] = json.loads(row["result"])
        return row

    def pron_sound_stats(self, days: int = 30, min_count: int = 5) -> list[dict]:
        """Each sound practised in the last `days`: how often it was said right, and what was said instead,
        weakest first. A sound needs `min_count` attempts before it is ranked."""
        since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat(timespec="seconds")
        rows = self._all("""
            SELECT s.phone, COUNT(*) AS total, SUM(s.status = 'good') AS good, SUM(s.status = 'wrong') AS wrong,
                   SUM(s.status = 'unsure') AS unsure
            FROM pron_sounds s JOIN pron_attempts a ON a.id = s.attempt_id
            WHERE a.created_at >= ? AND s.phone != '+'
            GROUP BY s.phone HAVING COUNT(*) >= ?
        """, (since, min_count))
        for r in rows:
            r["accuracy"] = round(r["good"] / r["total"], 3)
            r["instead"] = [x["heard"] for x in self._all("""
                SELECT s.heard, COUNT(*) AS n FROM pron_sounds s JOIN pron_attempts a ON a.id = s.attempt_id
                WHERE a.created_at >= ? AND s.phone = ? AND s.status = 'wrong' AND s.heard IS NOT NULL
                GROUP BY s.heard ORDER BY n DESC LIMIT 2
            """, (since, r["phone"]))]
        rows.sort(key=lambda r: (r["accuracy"], -r["total"]))
        return rows

    def pron_added_count(self, days: int = 30) -> int:
        since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat(timespec="seconds")
        return self._one("""
            SELECT COUNT(*) AS n FROM pron_sounds s JOIN pron_attempts a ON a.id = s.attempt_id
            WHERE a.created_at >= ? AND s.phone = '+'
        """, (since,))["n"]

    # --- today's plan -----------------------------------------------------
    def plan(self) -> dict:
        ts = now()
        due_topics = self._all("""
            SELECT t.*, c.name AS course_name FROM topics t JOIN courses c ON c.id = t.course_id
            WHERE t.status IN ('learning', 'reviewing') AND t.due_at <= ?
            ORDER BY t.due_at
        """, (ts,))
        next_topics = self._all("""
            SELECT t.*, c.name AS course_name FROM topics t JOIN courses c ON c.id = t.course_id
            WHERE t.status = 'new' AND t.position = (
                SELECT MIN(position) FROM topics t2 WHERE t2.course_id = t.course_id AND t2.status = 'new')
            ORDER BY c.created_at
        """)
        due_words = self._one("SELECT COUNT(*) AS n FROM vocab WHERE due_at <= ?", (ts,))["n"]
        return {
            "due_topics": due_topics,
            "next_topics": next_topics,
            "due_vocab": due_words,
            "focus_mistakes": self.list_mistakes(include_resolved=False, limit=3),
            "last_session": self.last_summary(),
        }
