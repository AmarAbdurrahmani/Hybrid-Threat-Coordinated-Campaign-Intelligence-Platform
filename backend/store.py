"""SQLite persistence (single file, no server). Swap for Postgres after the hackathon."""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from typing import Any

SCHEMA = """
CREATE TABLE IF NOT EXISTS threads (
  id TEXT PRIMARY KEY, platform TEXT, external_id TEXT, title TEXT, url TEXT,
  published REAL, simulated INTEGER DEFAULT 0, created_at REAL
);
CREATE TABLE IF NOT EXISTS comments (
  id TEXT PRIMARY KEY, thread_id TEXT, platform TEXT, user TEXT, ts REAL, text TEXT, source_text TEXT,
  account TEXT, infra TEXT, simulated INTEGER DEFAULT 0, ingested_at REAL
);
CREATE INDEX IF NOT EXISTS ix_comments_thread ON comments(thread_id, ts);
CREATE INDEX IF NOT EXISTS ix_comments_ts ON comments(ts);
CREATE TABLE IF NOT EXISTS sources (
  id TEXT PRIMARY KEY, kind TEXT, ref TEXT, cursor TEXT, active INTEGER DEFAULT 1, added_at REAL
);
CREATE TABLE IF NOT EXISTS alerts (
  id INTEGER PRIMARY KEY AUTOINCREMENT, thread_id TEXT, level INTEGER, sent_at REAL, delivered INTEGER, text TEXT
);
"""


class Store:
    def __init__(self, path: str = "htccip.db"):
        self._db = sqlite3.connect(path, check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        with self._lock:
            self._db.executescript(SCHEMA)
            self._db.commit()

    # --- threads ---
    def upsert_thread(self, t: dict) -> None:
        with self._lock:
            self._db.execute(
                "INSERT INTO threads(id, platform, external_id, title, url, published, simulated, created_at) VALUES(?,?,?,?,?,?,?,?) "
                "ON CONFLICT(id) DO UPDATE SET title=COALESCE(excluded.title, threads.title), url=COALESCE(excluded.url, threads.url), "
                "published=COALESCE(excluded.published, threads.published), simulated=MAX(threads.simulated, excluded.simulated)",
                (t["id"], t.get("platform"), t.get("external_id"), t.get("title"), t.get("url"), t.get("published"), int(bool(t.get("simulated"))), time.time()),
            )
            self._db.commit()

    def get_thread(self, tid: str) -> dict | None:
        with self._lock:
            r = self._db.execute("SELECT * FROM threads WHERE id=?", (tid,)).fetchone()
        return dict(r) if r else None

    def list_threads(self) -> list[dict]:
        with self._lock:
            rows = self._db.execute(
                "SELECT t.*, COUNT(c.id) AS n, MAX(c.ts) AS last_ts FROM threads t LEFT JOIN comments c ON c.thread_id=t.id "
                "GROUP BY t.id ORDER BY COALESCE(MAX(c.ts), t.published) DESC").fetchall()
        return [dict(r) for r in rows]

    # --- comments ---
    def add_comments(self, comments: list[dict]) -> list[dict]:
        """Insert; returns only the comments that were new."""
        new = []
        with self._lock:
            for c in comments:
                cur = self._db.execute(
                    "INSERT OR IGNORE INTO comments(id, thread_id, platform, user, ts, text, source_text, account, infra, simulated, ingested_at) "
                    "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                    (c["id"], c["thread_id"], c.get("platform"), c["user"], c.get("ts"), c.get("text"), c.get("source_text"),
                     json.dumps(c.get("account")) if c.get("account") else None, json.dumps(c.get("infra")) if c.get("infra") else None,
                     int(bool(c.get("simulated"))), time.time()))
                if cur.rowcount:
                    new.append(c)
            self._db.commit()
        return new

    def _row(self, r: sqlite3.Row) -> dict[str, Any]:
        d = dict(r)
        d["account"] = json.loads(d["account"]) if d.get("account") else None
        d["infra"] = json.loads(d["infra"]) if d.get("infra") else None
        d["simulated"] = bool(d.get("simulated"))
        return d

    def thread_comments(self, tid: str) -> list[dict]:
        with self._lock:
            rows = self._db.execute("SELECT * FROM comments WHERE thread_id=? ORDER BY ts", (tid,)).fetchall()
        return [self._row(r) for r in rows]

    def recent_comments(self, since_ms: float) -> list[dict]:
        with self._lock:
            rows = self._db.execute("SELECT id, thread_id, user, ts FROM comments WHERE ts>=?", (since_ms,)).fetchall()
        return [dict(r) for r in rows]

    # --- sources ---
    def add_source(self, kind: str, ref: str) -> dict:
        sid = f"{kind}:{ref}"
        with self._lock:
            self._db.execute("INSERT OR IGNORE INTO sources(id, kind, ref, added_at) VALUES(?,?,?,?)", (sid, kind, ref, time.time()))
            self._db.execute("UPDATE sources SET active=1 WHERE id=?", (sid,))
            self._db.commit()
        return {"id": sid, "kind": kind, "ref": ref}

    def sources(self, kind: str | None = None) -> list[dict]:
        with self._lock:
            q = "SELECT * FROM sources WHERE active=1" + (" AND kind=?" if kind else "")
            rows = self._db.execute(q, (kind,) if kind else ()).fetchall()
        return [dict(r) for r in rows]

    def set_cursor(self, sid: str, cursor: str) -> None:
        with self._lock:
            self._db.execute("UPDATE sources SET cursor=? WHERE id=?", (cursor, sid))
            self._db.commit()

    def remove_source(self, sid: str) -> None:
        with self._lock:
            self._db.execute("UPDATE sources SET active=0 WHERE id=?", (sid,))
            self._db.commit()

    # --- alerts ---
    def last_alert(self, tid: str) -> dict | None:
        with self._lock:
            r = self._db.execute("SELECT * FROM alerts WHERE thread_id=? ORDER BY sent_at DESC LIMIT 1", (tid,)).fetchone()
        return dict(r) if r else None

    def log_alert(self, tid: str, level: int, delivered: bool, text: str) -> None:
        with self._lock:
            self._db.execute("INSERT INTO alerts(thread_id, level, sent_at, delivered, text) VALUES(?,?,?,?,?)", (tid, level, time.time(), int(delivered), text))
            self._db.commit()

    def alerts(self, limit: int = 50) -> list[dict]:
        with self._lock:
            rows = self._db.execute("SELECT id, thread_id, level, sent_at, delivered, substr(text,1,400) AS preview FROM alerts ORDER BY sent_at DESC LIMIT ?", (limit,)).fetchall()
        return [dict(r) for r in rows]
