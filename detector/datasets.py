"""Data loaders for the bot detector.

Two sources, one schema (see SPEC.md):
  * load_cresci2017(zip_path)   labelled Twitter bot / genuine accounts (public research dataset)
  * load_backend_db(db_path)    the platform's own SQLite store (YouTube comments), unlabelled

Processed tables are written as CSV.gz under detector/data/processed/ by save() and read back by load().
"""

from __future__ import annotations

import io
import os
import json
import logging
import math
import re
import sqlite3
import sys
import time
import zipfile
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

log = logging.getLogger(__name__)

HERE = Path(__file__).resolve().parent
PROCESSED_DIR = HERE / "data" / "processed"

ACCOUNT_COLUMNS = [
    "account_id", "created_ts", "followers", "following", "posts_total", "label", "family", "source",
]
POST_COLUMNS = ["post_id", "account_id", "ts", "text", "thread_id", "parent_id", "urls"]
_ACCOUNT_STR = ["account_id", "family", "source"]
_POST_STR = ["post_id", "account_id", "text", "thread_id", "parent_id", "urls"]
_ACCOUNT_NUM = ["created_ts", "followers", "following", "posts_total", "label"]
_POST_NUM = ["ts"]

# Epoch values above this are milliseconds (1e11 s would be year 5138; 1e11 ms is 1973).
_MS_THRESHOLD = 1e11
_URL_RE = re.compile(r"https?://[^\s<>\"']+", re.IGNORECASE)
_TWITTER_TIME_RE = re.compile(r"^[A-Za-z]{3} [A-Za-z]{3} \d{1,2} \d{2}:\d{2}:\d{2} [+-]\d{4} \d{4}$")


# ----------------------------------------------------------------------------- helpers

def _parse_time_string(text: str) -> float:
    """One time string -> epoch seconds (NaN if unparseable). Naive times are taken as UTC."""
    text = text.strip()
    if not text:
        return math.nan
    try:
        if _TWITTER_TIME_RE.match(text):  # e.g. "Tue Mar 03 08:21:38 +0000 2009"
            return datetime.strptime(text, "%a %b %d %H:%M:%S %z %Y").timestamp()
        ts = pd.Timestamp(text)
    except (ValueError, TypeError, OverflowError):
        return math.nan
    if pd.isna(ts):
        return math.nan
    if ts.tzinfo is None:
        ts = ts.tz_localize("UTC")
    return float(ts.timestamp())


def _to_epoch(values) -> np.ndarray:
    """Mixed column (epoch numbers in s or ms, ISO 8601 or Twitter strings, None) -> float64 epoch seconds."""
    s = pd.Series(list(values), dtype=object)
    out = np.full(len(s), np.nan, dtype=float)
    if len(s) == 0:
        return out
    num = pd.to_numeric(s, errors="coerce").to_numpy(dtype=float)
    is_num = ~np.isnan(num)
    out[is_num] = np.where(num[is_num] > _MS_THRESHOLD, num[is_num] / 1000.0, num[is_num])
    rest = np.flatnonzero(~is_num & s.notna().to_numpy())
    if rest.size:
        strs = s.iloc[rest].map(lambda v: str(v).strip())
        cache = {u: _parse_time_string(u) for u in pd.unique(strs)}
        out[rest] = strs.map(cache).to_numpy(dtype=float)
    return out


def _extract_urls(text) -> str:
    if not isinstance(text, str) or not text:
        return ""
    found = (u.rstrip(".,;:!?)]}") for u in _URL_RE.findall(text))
    return " ".join(u for u in found if u)


def _parse_json_object(raw) -> dict:
    if raw is None or (isinstance(raw, float) and math.isnan(raw)):
        return {}
    if isinstance(raw, dict):
        return raw
    try:
        obj = json.loads(raw)
    except (TypeError, ValueError):
        return {}
    return obj if isinstance(obj, dict) else {}


def _empty_frames() -> tuple[pd.DataFrame, pd.DataFrame]:
    return pd.DataFrame(columns=ACCOUNT_COLUMNS), pd.DataFrame(columns=POST_COLUMNS)


# ----------------------------------------------------------------------------- backend (SQLite)

def load_backend_db(db_path, include_simulated: bool = False) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Read the backend SQLite DB (read-only) into (accounts, posts) per SPEC.md.

    One account per distinct comment user. Simulated rows are skipped unless include_simulated.
    Account metadata (created, followers) comes from the account JSON; first non-null value per user wins.
    """
    path = Path(db_path).resolve()
    if not path.is_file():
        raise FileNotFoundError(path)
    con = sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True)
    try:
        where = "" if include_simulated else "WHERE COALESCE(simulated, 0) = 0"
        rows = pd.read_sql_query(
            "SELECT id, thread_id, platform, user, ts, text, account FROM comments "
            f"{where} ORDER BY ts, id",
            con,
        )
    finally:
        con.close()

    rows = rows[rows["user"].notna()].copy()
    if rows.empty:
        return _empty_frames()
    rows["user"] = rows["user"].astype(str)
    meta = rows["account"].map(_parse_json_object)
    rows["created_ts"] = _to_epoch(meta.map(lambda m: m.get("created")))
    rows["followers"] = pd.to_numeric(
        pd.Series([m.get("followers") for m in meta], index=rows.index, dtype=object), errors="coerce"
    )
    rows["post_ts"] = _to_epoch(rows["ts"])

    first = rows.groupby("user", sort=True).agg(
        created_ts=("created_ts", "first"),
        followers=("followers", "first"),
        platform=("platform", "first"),
    )
    accounts = pd.DataFrame({
        "account_id": first.index.astype(str),
        "created_ts": first["created_ts"].astype(float).to_numpy(),
        "followers": first["followers"].astype(float).to_numpy(),
        "following": np.nan,
        "posts_total": np.nan,
        "label": np.nan,
        "family": first["platform"].fillna("unknown").str.lower().to_numpy(),
        "source": "backend",
    })[ACCOUNT_COLUMNS]

    posts = pd.DataFrame({
        "post_id": rows["id"].astype(str).to_numpy(),
        "account_id": rows["user"].to_numpy(),
        "ts": rows["post_ts"].to_numpy(dtype=float),
        "text": rows["text"].fillna("").astype(str).to_numpy(),
        "thread_id": rows["thread_id"].fillna("").astype(str).to_numpy(),
        "parent_id": "",
        "urls": rows["text"].map(_extract_urls).to_numpy(),
    })[POST_COLUMNS]
    return accounts.reset_index(drop=True), posts.reset_index(drop=True)


# ----------------------------------------------------------------------------- save / load

def save(name: str, accounts: pd.DataFrame, posts: pd.DataFrame) -> tuple[Path, Path]:
    """Write <name>_accounts.csv.gz and <name>_posts.csv.gz under detector/data/processed/."""
    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    acc_path = PROCESSED_DIR / f"{name}_accounts.csv.gz"
    post_path = PROCESSED_DIR / f"{name}_posts.csv.gz"
    accounts[ACCOUNT_COLUMNS].to_csv(acc_path, index=False, compression="gzip")
    posts[POST_COLUMNS].to_csv(post_path, index=False, compression="gzip")
    return acc_path, post_path


def load(name: str) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Read back what save(name, ...) wrote. Empty strings stay '' (not NaN); empty numbers stay NaN."""
    acc_path = PROCESSED_DIR / f"{name}_accounts.csv.gz"
    post_path = PROCESSED_DIR / f"{name}_posts.csv.gz"
    accounts = pd.read_csv(
        acc_path, compression="gzip", keep_default_na=False,
        dtype={c: str for c in _ACCOUNT_STR}, na_values={c: [""] for c in _ACCOUNT_NUM},
    )
    posts = pd.read_csv(
        post_path, compression="gzip", keep_default_na=False,
        dtype={c: str for c in _POST_STR}, na_values={c: [""] for c in _POST_NUM},
    )
    return accounts[ACCOUNT_COLUMNS], posts[POST_COLUMNS]


# ----------------------------------------------------------------------------- cresci-2017 (zip)

_CRESCI_GENUINE = "genuine_accounts"
_CHUNK_ROWS = 200_000
_TWEET_COLS = ["id", "user_id", "text", "created_at", "in_reply_to_status_id"]
_NULL_IDS = {"", "0", "NULL", "nan", "None"}


def _clean_id(values: pd.Series) -> pd.Series:
    """Id column read as str -> clean string ('' for missing, '0' or NULL)."""
    s = values.fillna("").astype(str).str.strip().str.replace(r"\.0$", "", regex=True)
    return s.where(~s.isin(_NULL_IDS), "")


def _num_col(df: pd.DataFrame, name: str) -> np.ndarray:
    if name in df.columns:
        return pd.to_numeric(df[name], errors="coerce").to_numpy(dtype=float)
    return np.full(len(df), np.nan)


def _csv_member(zf: zipfile.ZipFile, base: str):
    """Find a member by basename, ignoring macOS metadata (__MACOSX/, ._ files)."""
    for info in zf.infolist():
        if info.is_dir() or "__MACOSX" in info.filename:
            continue
        if info.filename.replace("\\", "/").rsplit("/", 1)[-1] == base:
            return info
    return None


def _read_cresci_users(fileobj, family: str, label: float) -> pd.DataFrame:
    users = pd.read_csv(
        fileobj, dtype=str, encoding="utf-8", encoding_errors="replace",
        on_bad_lines="skip", low_memory=False,
    )
    users = users[users["id"].notna()].copy()
    users["account_id"] = _clean_id(users["id"])
    users = users[users["account_id"] != ""].drop_duplicates("account_id", keep="first")
    created = users["created_at"] if "created_at" in users else pd.Series(np.nan, index=users.index, dtype=object)
    return pd.DataFrame({
        "account_id": users["account_id"].to_numpy(),
        "created_ts": _to_epoch(created),
        "followers": _num_col(users, "followers_count"),
        "following": _num_col(users, "friends_count"),
        "posts_total": _num_col(users, "statuses_count"),
        "label": label,
        "family": family,
        "source": "cresci-2017",
    })


def _read_cresci_tweets(fileobj, user_ids: set, max_posts: int, rng: np.random.Generator,
                        header_cols: list) -> pd.DataFrame:
    """Stream tweets.csv in chunks, keeping only the `max_posts` most recent tweets per account."""
    usecols = [c for c in _TWEET_COLS if c in header_cols]
    kept = pd.DataFrame(columns=["post_id", "account_id", "ts", "text", "parent_id", "_key", "_tie"])
    parts = []
    reader = pd.read_csv(
        fileobj, usecols=usecols, dtype=str, encoding="utf-8", encoding_errors="replace",
        on_bad_lines="skip", low_memory=False, chunksize=_CHUNK_ROWS,
    )
    with reader:
        for chunk in reader:
            chunk = chunk[chunk["id"].notna() & chunk["user_id"].isin(user_ids)]
            if chunk.empty:
                continue
            ts = _to_epoch(chunk["created_at"]) if "created_at" in chunk else np.full(len(chunk), np.nan)
            part = pd.DataFrame({
                "post_id": _clean_id(chunk["id"]).to_numpy(),
                "account_id": _clean_id(chunk["user_id"]).to_numpy(),
                "ts": ts,
                "text": chunk["text"].fillna("").astype(str).to_numpy() if "text" in chunk else "",
                "parent_id": (_clean_id(chunk["in_reply_to_status_id"]).to_numpy()
                              if "in_reply_to_status_id" in chunk else ""),
            })
            part["_key"] = np.where(np.isnan(ts), -np.inf, ts)  # undated tweets rank oldest
            part["_tie"] = rng.random(len(part))
            # trim inside the chunk, merge once at the end (re-sorting everything per chunk is quadratic)
            parts.append(part.sort_values(["account_id", "_key", "_tie"], kind="mergesort")
                             .groupby("account_id", sort=False).tail(max_posts))
    if parts:
        kept = (pd.concat(parts, ignore_index=True).sort_values(["account_id", "_key", "_tie"], kind="mergesort")
                  .groupby("account_id", sort=False).tail(max_posts))
    return kept.drop(columns=["_key", "_tie"]).drop_duplicates("post_id", keep="first")


def load_cresci2017(zip_path, max_posts_per_account: int = 200, seed: int = 0) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Read the Cresci-2017 archive (nested zip per group) into (accounts, posts) per SPEC.md.

    family = group name without '.csv' ('genuine_accounts' is written as 'genuine', as in SPEC.md);
    label = 0 for genuine, 1 for every bot group. Posts are the `max_posts_per_account` most recent tweets
    per account (ties broken by a seeded random key). Only CSVs are read; nothing from the archive is executed.
    Accounts without tweets are kept with posts_total from statuses_count.
    """
    rng = np.random.default_rng(seed)
    account_parts, post_parts = [], []
    no_tweets, skipped = [], []
    with zipfile.ZipFile(zip_path) as zf:
        groups = []
        for info in zf.infolist():
            base = info.filename.replace("\\", "/").rsplit("/", 1)[-1]
            if info.is_dir() or "__MACOSX" in info.filename or base.startswith("._"):
                continue
            if base.lower().endswith(".csv.zip"):
                groups.append((base[: -len(".csv.zip")], info))
            else:
                skipped.append(base)
        for group, info in groups:
            t0 = time.time()
            family = "genuine" if group == _CRESCI_GENUINE else group
            label = 0.0 if group == _CRESCI_GENUINE else 1.0
            with zipfile.ZipFile(io.BytesIO(zf.read(info))) as inner:
                users_info = _csv_member(inner, "users.csv")
                if users_info is None:
                    skipped.append(f"{group} (no users.csv)")
                    continue
                with inner.open(users_info) as fh:
                    users = _read_cresci_users(fh, family, label)
                user_ids = set(users["account_id"])
                tweets_info = _csv_member(inner, "tweets.csv")
                if tweets_info is None:
                    no_tweets.append(family)
                    posts = pd.DataFrame(columns=POST_COLUMNS)
                else:
                    with inner.open(tweets_info) as fh:
                        header = pd.read_csv(fh, nrows=0).columns.tolist()
                    with inner.open(tweets_info) as fh:
                        posts = _read_cresci_tweets(fh, user_ids, max_posts_per_account, rng, header)
                    posts = posts.assign(thread_id="", urls="")  # archive has no expanded-URL column
            account_parts.append(users)
            post_parts.append(posts[POST_COLUMNS])
            log.info("cresci %-24s accounts=%-7d posts=%-7d %.1fs",
                     family, len(users), len(posts), time.time() - t0)

    if not account_parts:
        raise ValueError(f"no account groups found in {zip_path}")
    accounts = pd.concat(account_parts, ignore_index=True)
    dup_accounts = int(accounts["account_id"].duplicated().sum())
    accounts = accounts.drop_duplicates("account_id", keep="first")
    posts = pd.concat(post_parts, ignore_index=True)
    posts = posts[posts["account_id"].isin(set(accounts["account_id"]))]
    posts = posts.drop_duplicates("post_id", keep="first")
    log.info("cresci: %d duplicate account ids dropped; groups without tweets.csv: %s; skipped: %s",
             dup_accounts, no_tweets, skipped)
    return accounts[ACCOUNT_COLUMNS].reset_index(drop=True), posts[POST_COLUMNS].reset_index(drop=True)


# ----------------------------------------------------------------------------- CLI

def _summary(accounts: pd.DataFrame, posts: pd.DataFrame) -> str:
    per_account = posts.groupby("account_id").size()
    acc = accounts.assign(_posts=accounts["account_id"].map(per_account).fillna(0).astype(int))
    lines = [f"{'family':<26}{'accounts':>10}{'with_posts':>12}{'posts':>10}   label"]
    for family, g in acc.groupby("family", sort=True):
        labels = sorted({"nan" if pd.isna(x) else str(int(x)) for x in g["label"]})
        lines.append(f"{family:<26}{len(g):>10}{int((g['_posts'] > 0).sum()):>12}"
                     f"{int(g['_posts'].sum()):>10}   {','.join(labels)}")
    lines.append(f"{'TOTAL':<26}{len(acc):>10}{int((acc['_posts'] > 0).sum()):>12}{len(posts):>10}")
    balance = accounts["label"].map(lambda x: "unlabelled" if pd.isna(x) else int(x)).value_counts()
    lines.append("label balance (accounts): " + ", ".join(f"{k}={v}" for k, v in balance.items()))
    return "\n".join(lines)


def _main(argv: list) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    usage = ("usage: python -m detector.datasets cresci <zip> [name]\n"
             "       python -m detector.datasets backend <db> [name]")
    if len(argv) < 2 or argv[0] not in ("cresci", "backend"):
        print(usage)
        return 2
    kind, path = argv[0], argv[1]
    name = argv[2] if len(argv) > 2 else ("cresci2017" if kind == "cresci" else "youtube_local")
    t0 = time.time()
    if kind == "cresci":
        accounts, posts = load_cresci2017(path, max_posts_per_account=int(os.environ.get('MAX_POSTS', '100')))
    else:
        accounts, posts = load_backend_db(path)
    acc_path, post_path = save(name, accounts, posts)
    print(_summary(accounts, posts))
    print(f"saved {acc_path}\n      {post_path}\nruntime {time.time() - t0:.1f}s")
    return 0


if __name__ == "__main__":
    sys.exit(_main(sys.argv[1:]))
