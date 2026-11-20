-- SQLite reference schema for the Stitch redesign.
-- Matches a fresh Store database after its startup migrations.
-- Timestamps use Unix seconds except threads.published, comments.ts,
-- and the source cursor, which represent Unix milliseconds.
-- JSON is stored as TEXT in account, infra, payload, and snapshot.
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS threads (
  id TEXT PRIMARY KEY,
  platform TEXT,
  external_id TEXT,
  title TEXT,
  url TEXT,
  published REAL,
  simulated INTEGER DEFAULT 0,
  created_at REAL
);

CREATE TABLE IF NOT EXISTS comments (
  id TEXT PRIMARY KEY,
  thread_id TEXT,
  platform TEXT,
  user TEXT,
  ts REAL,
  text TEXT,
  source_text TEXT,
  account TEXT,
  infra TEXT,
  simulated INTEGER DEFAULT 0,
  ingested_at REAL,
  url TEXT,
  normalized INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS sources (
  id TEXT PRIMARY KEY,
  kind TEXT,
  ref TEXT,
  cursor TEXT,
  active INTEGER DEFAULT 1,
  added_at REAL,
  last_attempt REAL,
  last_success REAL,
  last_error TEXT,
  last_backfill REAL
);

CREATE TABLE IF NOT EXISTS alerts (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  thread_id TEXT,
  level INTEGER,
  sent_at REAL, -- Set when the alert is recorded, before delivery.
  delivered INTEGER,
  text TEXT,
  status TEXT,
  exercise INTEGER NOT NULL DEFAULT 0,
  attempts INTEGER NOT NULL DEFAULT 0,
  next_attempt REAL,
  acknowledged_at REAL
);

CREATE TABLE IF NOT EXISTS analyses (
  thread_id TEXT PRIMARY KEY,
  payload TEXT,
  dirty INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS cases (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  title TEXT NOT NULL,
  owner TEXT,
  status TEXT NOT NULL,
  simulated INTEGER NOT NULL,
  created_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS case_threads (
  case_id INTEGER NOT NULL REFERENCES cases(id),
  thread_id TEXT NOT NULL REFERENCES threads(id),
  PRIMARY KEY (case_id, thread_id)
);

CREATE TABLE IF NOT EXISTS case_reviews (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  case_id INTEGER NOT NULL REFERENCES cases(id),
  decision TEXT NOT NULL,
  reason TEXT NOT NULL,
  reviewer TEXT NOT NULL,
  created_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS case_evidence (
  case_id INTEGER NOT NULL REFERENCES cases(id),
  thread_id TEXT NOT NULL REFERENCES threads(id),
  snapshot TEXT NOT NULL,
  PRIMARY KEY (case_id, thread_id)
);

CREATE INDEX IF NOT EXISTS ix_comments_thread ON comments(thread_id, ts);
CREATE INDEX IF NOT EXISTS ix_comments_ts ON comments(ts);
CREATE INDEX IF NOT EXISTS ix_comments_user ON comments(user, thread_id);
CREATE INDEX IF NOT EXISTS ix_case_threads_thread ON case_threads(thread_id);
