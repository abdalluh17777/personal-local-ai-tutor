"""SQLite schema and connection. The database is the single source of truth for progress and memory."""

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

SCHEMA_VERSION = 1

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS profile (
    id   INTEGER PRIMARY KEY CHECK (id = 1),
    data TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS courses (
    id          INTEGER PRIMARY KEY,
    name        TEXT NOT NULL UNIQUE COLLATE NOCASE,
    description TEXT NOT NULL DEFAULT '',
    created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS documents (
    id          INTEGER PRIMARY KEY,
    course_id   INTEGER NOT NULL REFERENCES courses(id) ON DELETE CASCADE,
    filename    TEXT NOT NULL,
    stored_path TEXT NOT NULL,
    sha256      TEXT NOT NULL,
    pages       INTEGER,
    chars       INTEGER NOT NULL,
    created_at  TEXT NOT NULL,
    UNIQUE (course_id, sha256)
);

CREATE TABLE IF NOT EXISTS passages (
    id          INTEGER PRIMARY KEY,
    document_id INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    course_id   INTEGER NOT NULL,
    page        INTEGER,
    position    INTEGER NOT NULL,
    text        TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_passages_course ON passages(course_id);

CREATE VIRTUAL TABLE IF NOT EXISTS passages_fts USING fts5(
    text, content='passages', content_rowid='id', tokenize='porter unicode61'
);
CREATE TRIGGER IF NOT EXISTS passages_ai AFTER INSERT ON passages BEGIN
    INSERT INTO passages_fts(rowid, text) VALUES (new.id, new.text);
END;
CREATE TRIGGER IF NOT EXISTS passages_ad AFTER DELETE ON passages BEGIN
    INSERT INTO passages_fts(passages_fts, rowid, text) VALUES ('delete', old.id, old.text);
END;

-- status: new (never studied) -> learning (studied, not yet reviewed) -> reviewing (on the review schedule)
CREATE TABLE IF NOT EXISTS topics (
    id         INTEGER PRIMARY KEY,
    course_id  INTEGER NOT NULL REFERENCES courses(id) ON DELETE CASCADE,
    title      TEXT NOT NULL,
    position   INTEGER NOT NULL,
    status     TEXT NOT NULL DEFAULT 'new' CHECK (status IN ('new', 'learning', 'reviewing')),
    stage      INTEGER NOT NULL DEFAULT 0,
    due_at     TEXT,
    notes      TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);

-- The material a topic is taught from (for example a lecture's transcript and its study guide)
CREATE TABLE IF NOT EXISTS topic_documents (
    topic_id    INTEGER NOT NULL REFERENCES topics(id) ON DELETE CASCADE,
    document_id INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    position    INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (topic_id, document_id)
);

CREATE TABLE IF NOT EXISTS attempts (
    id         INTEGER PRIMARY KEY,
    topic_id   INTEGER NOT NULL REFERENCES topics(id) ON DELETE CASCADE,
    method     TEXT NOT NULL,
    grade      TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sessions (
    id         INTEGER PRIMARY KEY,
    course_id  INTEGER REFERENCES courses(id) ON DELETE SET NULL,
    topic_id   INTEGER REFERENCES topics(id) ON DELETE SET NULL,
    started_at TEXT NOT NULL,
    ended_at   TEXT,
    summary    TEXT,
    next_step  TEXT,
    title      TEXT                              -- the learner's own name for the chat, or NULL
);

CREATE TABLE IF NOT EXISTS messages (
    id         INTEGER PRIMARY KEY,
    session_id INTEGER NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    role       TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
    content    TEXT NOT NULL,
    sources    TEXT,
    feedback   TEXT,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_messages_session ON messages(session_id);

-- origin: 'user' (added by the learner) or 'auto' (extracted by the tutor; the learner can edit or remove it)
CREATE TABLE IF NOT EXISTS memories (
    id         INTEGER PRIMARY KEY,
    kind       TEXT NOT NULL,
    content    TEXT NOT NULL,
    origin     TEXT NOT NULL CHECK (origin IN ('user', 'auto')),
    session_id INTEGER REFERENCES sessions(id) ON DELETE SET NULL,
    active     INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS vocab (
    id         INTEGER PRIMARY KEY,
    word       TEXT NOT NULL UNIQUE COLLATE NOCASE,
    meaning    TEXT NOT NULL,
    example    TEXT NOT NULL DEFAULT '',
    course_id  INTEGER REFERENCES courses(id) ON DELETE SET NULL,
    session_id INTEGER REFERENCES sessions(id) ON DELETE SET NULL,
    origin     TEXT NOT NULL CHECK (origin IN ('user', 'auto')),
    stage      INTEGER NOT NULL DEFAULT 0,
    due_at     TEXT NOT NULL,
    seen_count INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS mistake_patterns (
    id          INTEGER PRIMARY KEY,
    rule        TEXT NOT NULL,
    rule_key    TEXT NOT NULL UNIQUE,
    category    TEXT NOT NULL,
    explanation TEXT NOT NULL DEFAULT '',
    count       INTEGER NOT NULL DEFAULT 0,
    resolved    INTEGER NOT NULL DEFAULT 0,
    first_seen  TEXT NOT NULL,
    last_seen   TEXT NOT NULL
);

-- Word lists (an academic word list, a course's terms): a few new words join the vocabulary each day
CREATE TABLE IF NOT EXISTS word_lists (
    id          INTEGER PRIMARY KEY,
    slug        TEXT NOT NULL UNIQUE,
    title       TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    source      TEXT NOT NULL DEFAULT '',
    daily_new   INTEGER NOT NULL DEFAULT 10,     -- 0 = paused
    created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS word_list_items (
    id            INTEGER PRIMARY KEY,
    list_id       INTEGER NOT NULL REFERENCES word_lists(id) ON DELETE CASCADE,
    position      INTEGER NOT NULL,
    word          TEXT NOT NULL,
    meaning       TEXT NOT NULL,
    example       TEXT NOT NULL DEFAULT '',
    forms         TEXT NOT NULL DEFAULT '',
    pos           TEXT NOT NULL DEFAULT '',
    introduced_on TEXT,                          -- local date it joined the vocabulary, or 'known'
    vocab_id      INTEGER REFERENCES vocab(id) ON DELETE SET NULL,
    UNIQUE (list_id, word)
);

-- Pronunciation check: one row per recording checked, and one per expected sound in it
CREATE TABLE IF NOT EXISTS pron_attempts (
    id          INTEGER PRIMARY KEY,
    text        TEXT NOT NULL,
    drill       TEXT,                            -- drill id (drills.py), or NULL for free practice
    score       INTEGER NOT NULL,                -- 0-100
    result      TEXT NOT NULL,                   -- JSON: words, sounds, issues
    created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS pron_sounds (
    id          INTEGER PRIMARY KEY,
    attempt_id  INTEGER NOT NULL REFERENCES pron_attempts(id) ON DELETE CASCADE,
    phone       TEXT NOT NULL,                   -- CMU symbol, or "+" for a sound the learner added
    status      TEXT NOT NULL,                   -- good / unsure / wrong
    heard       TEXT,                            -- what was said instead ('' = nothing)
    word        TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS pron_sounds_phone ON pron_sounds(phone, status);

CREATE TABLE IF NOT EXISTS mistake_examples (
    id         INTEGER PRIMARY KEY,
    pattern_id INTEGER NOT NULL REFERENCES mistake_patterns(id) ON DELETE CASCADE,
    wrong      TEXT NOT NULL,
    right      TEXT NOT NULL,
    session_id INTEGER REFERENCES sessions(id) ON DELETE SET NULL,
    created_at TEXT NOT NULL
);
"""


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def connect(path: Path | str) -> sqlite3.Connection:
    if str(path) != ":memory:":
        Path(path).parent.mkdir(parents=True, exist_ok=True)
    # Autocommit mode; multi-statement writes use explicit BEGIN/COMMIT in Store.transaction()
    conn = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=5000")
    return conn


def init_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    # Columns added after the learner's database was created: CREATE TABLE IF NOT EXISTS does not add them
    if "title" not in {row[1] for row in conn.execute("PRAGMA table_info(sessions)")}:
        conn.execute("ALTER TABLE sessions ADD COLUMN title TEXT")
    conn.execute(
        "INSERT INTO meta(key, value) VALUES ('schema_version', ?) ON CONFLICT(key) DO NOTHING",
        (str(SCHEMA_VERSION),),
    )
