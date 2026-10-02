import json
import sqlite3
import threading
from datetime import datetime, timedelta, timezone

from . import config

_lock = threading.Lock()

SCHEMA = """
CREATE TABLE IF NOT EXISTS pulls (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    mode TEXT,
    status TEXT,              -- ok | error | demo
    n_returned INTEGER DEFAULT 0,
    n_new INTEGER DEFAULT 0,
    counts_against_quota INTEGER DEFAULT 1,
    error TEXT
);
CREATE TABLE IF NOT EXISTS articles (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    url TEXT UNIQUE NOT NULL,
    title TEXT NOT NULL,
    description TEXT,
    source TEXT,
    author TEXT,
    image_url TEXT,
    published_at TEXT,
    fetched_at TEXT NOT NULL,
    pull_id INTEGER,
    tags TEXT DEFAULT '[]',
    event_type TEXT,
    event_confidence TEXT,
    event_rationale TEXT,
    people TEXT DEFAULT '[]',
    classifier TEXT
);
CREATE INDEX IF NOT EXISTS idx_articles_fetched ON articles(fetched_at DESC);
CREATE INDEX IF NOT EXISTS idx_articles_event ON articles(event_type);
CREATE TABLE IF NOT EXISTS people (
    name_key TEXT PRIMARY KEY,
    data TEXT NOT NULL,
    fetched_at TEXT NOT NULL
);
"""


def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def connect():
    conn = sqlite3.connect(config.DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def init():
    with _lock, connect() as conn:
        conn.executescript(SCHEMA)


# ---------- pulls ----------

def start_pull(mode, counts_against_quota=True):
    with _lock, connect() as conn:
        cur = conn.execute(
            "INSERT INTO pulls (started_at, mode, status, counts_against_quota) VALUES (?, ?, 'running', ?)",
            (now_iso(), mode, int(counts_against_quota)),
        )
        return cur.lastrowid


def finish_pull(pull_id, status, n_returned=0, n_new=0, error=None):
    with _lock, connect() as conn:
        conn.execute(
            "UPDATE pulls SET finished_at=?, status=?, n_returned=?, n_new=?, error=? WHERE id=?",
            (now_iso(), status, n_returned, n_new, error, pull_id),
        )


def requests_last_24h():
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat(timespec="seconds")
    with connect() as conn:
        row = conn.execute(
            "SELECT COUNT(*) FROM pulls WHERE started_at >= ? AND counts_against_quota = 1", (cutoff,)
        ).fetchone()
        return row[0]


def last_pull():
    with connect() as conn:
        row = conn.execute("SELECT * FROM pulls ORDER BY id DESC LIMIT 1").fetchone()
        return dict(row) if row else None


def last_regular_mode():
    with connect() as conn:
        row = conn.execute(
            "SELECT mode FROM pulls WHERE mode IN ('top', 'everything') ORDER BY id DESC LIMIT 1").fetchone()
        return row[0] if row else None


def hours_since_mode(mode):
    with connect() as conn:
        row = conn.execute("SELECT MAX(started_at) FROM pulls WHERE mode=?", (mode,)).fetchone()
    if not row or not row[0]:
        return None
    return (datetime.now(timezone.utc) - datetime.fromisoformat(row[0])).total_seconds() / 3600


def pull_count():
    with connect() as conn:
        return conn.execute("SELECT COUNT(*) FROM pulls").fetchone()[0]


# ---------- articles ----------

def insert_articles(rows, pull_id):
    """Insert new articles; skip URLs we've already seen. Returns the inserted rows."""
    inserted = []
    with _lock, connect() as conn:
        for r in rows:
            cur = conn.execute(
                """INSERT OR IGNORE INTO articles
                   (url, title, description, source, author, image_url, published_at, fetched_at, pull_id,
                    tags, event_type, event_confidence, event_rationale, people, classifier)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    r["url"], r["title"], r.get("description"), r.get("source"), r.get("author"),
                    r.get("image_url"), r.get("published_at"), r.get("fetched_at") or now_iso(), pull_id,
                    json.dumps(r.get("tags", [])), r.get("event_type"), r.get("event_confidence"),
                    r.get("event_rationale"), json.dumps(r.get("people", [])), r.get("classifier"),
                ),
            )
            if cur.rowcount:
                inserted.append({**r, "id": cur.lastrowid})
    return inserted


def known_urls(urls):
    if not urls:
        return set()
    with connect() as conn:
        q = "SELECT url FROM articles WHERE url IN (%s)" % ",".join("?" * len(urls))
        return {row[0] for row in conn.execute(q, list(urls))}


def _article_row(row):
    d = dict(row)
    d["tags"] = json.loads(d["tags"] or "[]")
    d["people"] = json.loads(d["people"] or "[]")
    return d


def list_articles(since_id=0, limit=1000):
    with connect() as conn:
        rows = conn.execute(
            "SELECT * FROM articles WHERE id > ? ORDER BY fetched_at DESC, published_at DESC LIMIT ?",
            (since_id, limit),
        ).fetchall()
        return [_article_row(r) for r in rows]


def get_article(article_id):
    with connect() as conn:
        row = conn.execute("SELECT * FROM articles WHERE id=?", (article_id,)).fetchone()
        return _article_row(row) if row else None


# ---------- people cache ----------

def get_person(name_key):
    with connect() as conn:
        row = conn.execute("SELECT data FROM people WHERE name_key=?", (name_key,)).fetchone()
        return json.loads(row[0]) if row else None


def put_person(name_key, data):
    with _lock, connect() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO people (name_key, data, fetched_at) VALUES (?, ?, ?)",
            (name_key, json.dumps(data), now_iso()),
        )
