"""SQLite storage for monitored threads, analysis, analyst cases and delivery."""
from __future__ import annotations

import json
import sqlite3
import threading
import time

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
CREATE INDEX IF NOT EXISTS ix_comments_user ON comments(user, thread_id);
CREATE TABLE IF NOT EXISTS sources (
  id TEXT PRIMARY KEY, kind TEXT, ref TEXT, cursor TEXT, active INTEGER DEFAULT 1, added_at REAL
);
CREATE TABLE IF NOT EXISTS alerts (
  id INTEGER PRIMARY KEY AUTOINCREMENT, thread_id TEXT, level INTEGER, sent_at REAL, delivered INTEGER, text TEXT
);
CREATE TABLE IF NOT EXISTS analyses (
  thread_id TEXT PRIMARY KEY, payload TEXT, dirty INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS cases (
  id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT NOT NULL, owner TEXT, status TEXT NOT NULL,
  simulated INTEGER NOT NULL, created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS case_threads (
  case_id INTEGER NOT NULL REFERENCES cases(id), thread_id TEXT NOT NULL REFERENCES threads(id),
  PRIMARY KEY(case_id, thread_id)
);
CREATE TABLE IF NOT EXISTS case_reviews (
  id INTEGER PRIMARY KEY AUTOINCREMENT, case_id INTEGER NOT NULL REFERENCES cases(id),
  decision TEXT NOT NULL, reason TEXT NOT NULL, reviewer TEXT NOT NULL, created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS case_evidence (
  case_id INTEGER NOT NULL REFERENCES cases(id), thread_id TEXT NOT NULL REFERENCES threads(id),
  snapshot TEXT NOT NULL, PRIMARY KEY(case_id, thread_id)
);
CREATE INDEX IF NOT EXISTS ix_case_threads_thread ON case_threads(thread_id);
"""

DIRTY_SQL = ("INSERT INTO analyses(thread_id,dirty) VALUES(?,1) "
             "ON CONFLICT(thread_id) DO UPDATE SET dirty=1")


class Store:
    def __init__(self, path: str = "htccip.db"):
        self._db = sqlite3.connect(path, check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        with self._lock, self._db:
            self._db.execute("PRAGMA foreign_keys=ON")
            self._db.executescript(SCHEMA)
            for table, columns in {
                "comments": {"url": "TEXT", "normalized": "INTEGER NOT NULL DEFAULT 0", "likes": "INTEGER"},
                "sources": {"last_attempt": "REAL", "last_success": "REAL", "last_error": "TEXT", "last_backfill": "REAL"},
                "alerts": {"status": "TEXT", "exercise": "INTEGER NOT NULL DEFAULT 0",
                           "attempts": "INTEGER NOT NULL DEFAULT 0", "next_attempt": "REAL", "acknowledged_at": "REAL"},
            }.items():
                existing = {row["name"] for row in self._db.execute(f"PRAGMA table_info({table})")}
                for name, sql_type in columns.items():
                    if name not in existing:
                        self._db.execute(f"ALTER TABLE {table} ADD COLUMN {name} {sql_type}")

    def normalize_legacy_users(self, normalize, truncate_ip):
        with self._lock, self._db:
            rows = self._db.execute("SELECT * FROM comments WHERE normalized=0 AND "
                                    "(substr(thread_id,1,4)='ext:' OR substr(thread_id,1,3)='pt:')").fetchall()
            for row in rows:
                account = json.loads(row["account"]) if row["account"] else None
                user, account = normalize(row["platform"], row["user"], account)
                if row["thread_id"].startswith("pt:"):
                    user = row["user"]
                infra = json.loads(row["infra"]) if row["infra"] else None
                if not isinstance(infra, dict) or not isinstance(infra.get("node"), str):
                    infra = None
                if infra and isinstance(infra.get("ip"), str) and "/" not in infra["ip"]:
                    infra["ip"] = truncate_ip(infra["ip"])
                self._db.execute("UPDATE comments SET user=?,account=?,infra=?,normalized=1 WHERE id=?",
                                 (user, json.dumps(account), json.dumps(infra), row["id"]))
                self._db.execute(DIRTY_SQL, (row["thread_id"],))
                marker = "@"+row["user"]+" risk "
                self._db.execute("UPDATE alerts SET text=replace(text,?,?) WHERE thread_id=?",
                                 (marker, "@"+user+" risk ", row["thread_id"]))

    def _upsert_thread(self, t: dict) -> None:
        self._db.execute(
            "INSERT INTO threads(id,platform,external_id,title,url,published,simulated,created_at) VALUES(?,?,?,?,?,?,?,?) "
            "ON CONFLICT(id) DO UPDATE SET title=COALESCE(excluded.title,threads.title), "
            "url=COALESCE(excluded.url,threads.url), published=COALESCE(excluded.published,threads.published)",
            (t["id"], t.get("platform"), t.get("external_id"), t.get("title"), t.get("url"),
             t.get("published"), int(bool(t.get("simulated"))), time.time()),
        )

    def upsert_thread(self, thread: dict) -> None:
        with self._lock, self._db:
            self._upsert_thread(thread)

    def get_thread(self, tid: str) -> dict | None:
        with self._lock:
            row = self._db.execute("SELECT * FROM threads WHERE id=?", (tid,)).fetchone()
        return dict(row) if row else None

    def list_threads(self) -> list[dict]:
        with self._lock:
            rows = self._db.execute(
                "SELECT t.*, COUNT(c.id) AS n, MAX(c.ts) AS last_ts, "
                "CASE WHEN a.dirty=0 THEN json_extract(a.payload,'$.thread.level') END AS level, "
                "CASE WHEN a.dirty=0 THEN json_extract(a.payload,'$.thread.pct') END AS pct "
                "FROM threads t LEFT JOIN comments c ON c.thread_id=t.id AND c.simulated=t.simulated "
                "LEFT JOIN analyses a ON a.thread_id=t.id "
                "GROUP BY t.id ORDER BY COALESCE(MAX(c.ts),t.published) DESC"
            ).fetchall()
        return [dict(row) for row in rows]

    def _insert_comments(self, comments: list[dict]) -> tuple[list[dict], set[str]]:
        new = []
        changed = set()
        for c in comments:
            if c.get("legacy_id"):
                old = self._db.execute("SELECT id FROM comments WHERE id=? AND thread_id=? AND platform=?",
                                       (c["legacy_id"], c["thread_id"], c["platform"])).fetchone()
                if old:
                    if c.get("url") and self._db.execute("UPDATE comments SET url=? WHERE id=? AND url IS NULL",
                                                        (c["url"], old["id"])).rowcount:
                        changed.add(c["thread_id"])
                    continue
            inserted = self._db.execute(
                "INSERT OR IGNORE INTO comments(id,thread_id,platform,user,ts,text,source_text,account,infra,simulated,ingested_at,url,likes,normalized) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,1)",
                (c["id"], c["thread_id"], c.get("platform"), c["user"], c.get("ts"), c.get("text"), c.get("source_text"),
                 json.dumps(c.get("account")) if c.get("account") else None,
                 json.dumps(c.get("infra")) if c.get("infra") else None,
                 int(bool(c.get("simulated"))), time.time(), c.get("url"), c.get("likes")),
            ).rowcount
            if inserted:
                new.append(c)
                changed.add(c["thread_id"])
            elif c.get("url") and self._db.execute("UPDATE comments SET url=? WHERE id=? AND thread_id=? AND url IS NULL",
                                                  (c["url"], c["id"], c["thread_id"])).rowcount:
                changed.add(c["thread_id"])
        return new, changed

    def add_comments(self, comments: list[dict]) -> list[dict]:
        with self._lock, self._db:
            new, _ = self._insert_comments(comments)
        return new

    def ingest_batch(self, threads: list[dict], comments: list[dict]) -> list[dict]:
        with self._lock, self._db:
            changed = set()
            for thread in threads:
                current = self._db.execute("SELECT * FROM threads WHERE id=?", (thread["id"],)).fetchone()
                if not current or any(thread.get(k) is not None and thread[k] != current[k]
                                      for k in ("title", "url", "published")):
                    changed.add(thread["id"])
                self._upsert_thread(thread)
            new, updated = self._insert_comments(comments)
            changed.update(updated)
            for tid in changed:
                self._db.execute(DIRTY_SQL, (tid,))
                self._db.execute(
                    "INSERT INTO analyses(thread_id,dirty) SELECT DISTINCT c.thread_id,1 "
                    "FROM comments c JOIN threads t ON t.id=c.thread_id AND t.simulated=c.simulated "
                    "WHERE t.simulated=(SELECT simulated FROM threads WHERE id=?) "
                    "AND c.user IN (SELECT user FROM comments WHERE thread_id=?) "
                    "ON CONFLICT(thread_id) DO UPDATE SET dirty=1", (tid, tid),
                )
        return new

    def dirty_threads(self) -> list[str]:
        with self._lock:
            return [row[0] for row in self._db.execute("SELECT thread_id FROM analyses WHERE dirty=1 ORDER BY thread_id")]

    def saved_analysis(self, tid: str) -> dict | None:
        with self._lock:
            row = self._db.execute("SELECT payload FROM analyses WHERE thread_id=? AND dirty=0", (tid,)).fetchone()
        return json.loads(row[0]) if row and row[0] else None

    def save_analysis(self, tid: str, analysis: dict) -> None:
        with self._lock, self._db:
            self._db.execute("INSERT INTO analyses(thread_id,payload,dirty) VALUES(?,?,0) "
                             "ON CONFLICT(thread_id) DO UPDATE SET payload=excluded.payload,dirty=0",
                             (tid, json.dumps(analysis, allow_nan=False)))

    def score_snapshots(self, simulated: bool) -> list[dict]:
        with self._lock:
            rows = self._db.execute(
                "SELECT json_extract(a.payload,'$.scoreByUser') FROM analyses a JOIN threads t ON t.id=a.thread_id "
                "WHERE a.dirty=0 AND t.simulated=?", (int(simulated),),
            ).fetchall()
        return [json.loads(row[0]) for row in rows]

    def infrastructure_snapshots(self, simulated: bool, thread_id: str | None = None) -> list[dict]:
        where = " AND t.id=?" if thread_id else ""
        with self._lock:
            rows = self._db.execute(
                "SELECT t.id, t.platform, json_extract(a.payload, '$.thread.n') AS comments, "
                "json_extract(a.payload, '$.infra') AS nodes "
                "FROM analyses a JOIN threads t ON t.id=a.thread_id "
                "WHERE a.dirty=0 AND a.payload IS NOT NULL AND t.simulated=?" + where,
                (int(simulated), thread_id) if thread_id else (int(simulated),),
            ).fetchall()
        return [{"thread_id": row["id"], "platform": row["platform"], "comments": row["comments"] or 0,
                 "nodes": json.loads(row["nodes"] or "[]")} for row in rows]

    def thread_comments(self, tid: str) -> list[dict]:
        with self._lock:
            rows = self._db.execute(
                "SELECT c.* FROM comments c JOIN threads t ON t.id=c.thread_id AND t.simulated=c.simulated "
                "WHERE c.thread_id=? ORDER BY c.ts,c.id", (tid,),
            ).fetchall()
        result = []
        for row in rows:
            c = dict(row)
            c.pop("normalized")
            c["account"] = json.loads(c["account"]) if c["account"] else None
            c["infra"] = json.loads(c["infra"]) if c["infra"] else None
            c["simulated"] = bool(c["simulated"])
            if not c["url"] and c["platform"] == "youtube" and tid.startswith("yt:") and c["id"].startswith("yt:"):
                c["url"] = f"https://www.youtube.com/watch?v={tid[3:]}&lc={c['id'][3:]}"
            if not c["url"] and c["platform"] == "telegram" and tid.startswith("tg:") and c["id"].startswith(tid + ":"):
                _, handle, post = tid.split(":", 2)
                c["url"] = f"https://t.me/{handle}/{post}?comment={c['id'][len(tid) + 1:]}"
            result.append(c)
        return result

    def recent_comments(self, since_ms: float, simulated: bool = False) -> list[dict]:
        with self._lock:
            rows = self._db.execute(
                "SELECT c.id,c.thread_id,c.user,c.ts FROM comments c JOIN threads t ON t.id=c.thread_id "
                "WHERE c.ts>=? AND t.simulated=? AND c.simulated=?", (since_ms, int(simulated), int(simulated)),
            ).fetchall()
        return [dict(row) for row in rows]

    def add_source(self, kind: str, ref: str) -> dict:
        sid = f"{kind}:{ref}"
        with self._lock, self._db:
            self._db.execute("INSERT OR IGNORE INTO sources(id,kind,ref,added_at) VALUES(?,?,?,?)", (sid, kind, ref, time.time()))
        return self.source(sid)

    def source(self, sid: str) -> dict | None:
        with self._lock:
            row = self._db.execute("SELECT * FROM sources WHERE id=?", (sid,)).fetchone()
        return dict(row) if row else None

    def sources(self, kind: str | None = None, *, include_paused: bool = False) -> list[dict]:
        with self._lock:
            q = "SELECT * FROM sources WHERE (? OR active=1)" + (" AND kind=?" if kind else "") + " ORDER BY id"
            rows = self._db.execute(q, (include_paused, kind) if kind else (include_paused,)).fetchall()
        return [dict(row) for row in rows]

    def source_started(self, sid: str) -> None:
        with self._lock, self._db:
            self._db.execute("UPDATE sources SET last_attempt=? WHERE id=?", (time.time(), sid))

    def source_finished(self, sid: str, *, cursor: float | None = None, error: str | None = None,
                        backfill: bool = False) -> None:
        with self._lock, self._db:
            if error:
                self._db.execute("UPDATE sources SET last_error=? WHERE id=?", (error, sid))
            else:
                now = time.time()
                self._db.execute(
                    "UPDATE sources SET cursor=CASE WHEN ? > COALESCE(CAST(cursor AS REAL),0) THEN ? ELSE cursor END, "
                    "last_success=?, last_error=NULL, last_backfill=COALESCE(?,last_backfill) WHERE id=?",
                    (cursor, cursor, now, now if backfill else None, sid),
                )

    def set_source_active(self, sid: str, active: bool) -> bool:
        with self._lock, self._db:
            return bool(self._db.execute("UPDATE sources SET active=? WHERE id=?", (int(active), sid)).rowcount)

    def last_alert(self, tid: str) -> dict | None:
        with self._lock:
            row = self._db.execute("SELECT * FROM alerts WHERE thread_id=? ORDER BY id DESC LIMIT 1", (tid,)).fetchone()
        return dict(row) if row else None

    def queue_alert(self, tid: str, level: int, status: str, exercise: bool, report: str) -> int:
        with self._lock, self._db:
            now = time.time()
            return self._db.execute(
                "INSERT INTO alerts(thread_id,level,sent_at,delivered,text,status,exercise,next_attempt) VALUES(?,?,?,0,?,?,?,?)",
                (tid, level, now, report, status, int(exercise), now if status == "queued" else None),
            ).lastrowid

    def pending_alerts(self) -> list[dict]:
        with self._lock:
            rows = self._db.execute(
                "SELECT * FROM alerts WHERE status='queued' AND next_attempt<=? ORDER BY next_attempt,id LIMIT 20",
                (time.time(),),
            ).fetchall()
        return [dict(row) for row in rows]

    def record_delivery(self, alert_id: int, status: str, next_attempt: float | None = None) -> None:
        with self._lock, self._db:
            self._db.execute("UPDATE alerts SET delivered=?,status=?,attempts=attempts+1,next_attempt=? WHERE id=?",
                             (int(status == "delivered"), status, next_attempt, alert_id))

    def acknowledge_alert(self, alert_id: int) -> bool:
        with self._lock, self._db:
            return bool(self._db.execute("UPDATE alerts SET acknowledged_at=COALESCE(acknowledged_at,?) WHERE id=?",
                                         (time.time(), alert_id)).rowcount)

    def alerts(self, limit: int = 50) -> list[dict]:
        with self._lock:
            rows = self._db.execute(
                "SELECT id,thread_id,level,sent_at,delivered,COALESCE(status,CASE WHEN delivered THEN 'delivered' ELSE 'legacy' END) AS status, "
                "exercise,attempts,next_attempt,acknowledged_at,substr(text,1,400) AS preview "
                "FROM alerts ORDER BY id DESC LIMIT ?", (limit,),
            ).fetchall()
        return [dict(row) for row in rows]

    def create_case(self, title: str, owner: str | None, simulated: bool, evidence: dict[str, dict], *, status: str = "open") -> int:
        with self._lock, self._db:
            cid = self._db.execute("INSERT INTO cases(title,owner,status,simulated,created_at) VALUES(?,?,?,?,?)",
                                   (title, owner, status, int(simulated), time.time())).lastrowid
            self._link_evidence(cid, evidence)
        return cid

    def _link_evidence(self, cid: int, evidence: dict[str, dict]) -> None:
        for tid, snapshot in evidence.items():
            self._db.execute("INSERT OR IGNORE INTO case_threads(case_id,thread_id) VALUES(?,?)", (cid, tid))
            self._db.execute("INSERT OR IGNORE INTO case_evidence(case_id,thread_id,snapshot) VALUES(?,?,?)",
                             (cid, tid, json.dumps(snapshot, allow_nan=False)))

    def update_case(self, cid: int, changes: dict, evidence: dict[str, dict], unlink: list[str]) -> None:
        with self._lock, self._db:
            for field in ("title", "owner", "status"):
                if field in changes:
                    self._db.execute(f"UPDATE cases SET {field}=? WHERE id=?", (changes[field], cid))
            self._link_evidence(cid, evidence)
            self._db.executemany("DELETE FROM case_threads WHERE case_id=? AND thread_id=?", ((cid, tid) for tid in unlink))

    def cases(self, simulated: bool) -> list[dict]:
        with self._lock:
            rows = self._db.execute("SELECT * FROM cases WHERE simulated=? ORDER BY id DESC", (int(simulated),)).fetchall()
            return [{**dict(row), "thread_ids": self._case_thread_ids(row["id"])} for row in rows]

    def _case_thread_ids(self, cid: int) -> list[str]:
        return [row[0] for row in self._db.execute("SELECT thread_id FROM case_threads WHERE case_id=? ORDER BY thread_id", (cid,))]

    def case(self, cid: int) -> dict | None:
        with self._lock:
            row = self._db.execute("SELECT * FROM cases WHERE id=?", (cid,)).fetchone()
            if not row:
                return None
            evidence = {r["thread_id"]: json.loads(r["snapshot"]) for r in self._db.execute(
                "SELECT thread_id,snapshot FROM case_evidence WHERE case_id=? ORDER BY thread_id", (cid,))}
            reviews = [dict(r) for r in self._db.execute("SELECT * FROM case_reviews WHERE case_id=? ORDER BY id", (cid,))]
            return {**dict(row), "thread_ids": self._case_thread_ids(cid), "evidence": evidence, "reviews": reviews}

    def add_review(self, cid: int, decision: str, reason: str, reviewer: str) -> None:
        with self._lock, self._db:
            self._db.execute("INSERT INTO case_reviews(case_id,decision,reason,reviewer,created_at) VALUES(?,?,?,?,?)",
                             (cid, decision, reason, reviewer, time.time()))

    def review_counts(self, simulated: bool) -> dict[str, int]:
        with self._lock:
            rows = self._db.execute(
                "SELECT r.decision, COUNT(*) FROM case_reviews r JOIN cases c ON c.id=r.case_id "
                "WHERE c.simulated=? AND r.id=(SELECT MAX(id) FROM case_reviews WHERE case_id=r.case_id) "
                "GROUP BY r.decision", (int(simulated),),
            ).fetchall()
        return {row[0]: row[1] for row in rows}
