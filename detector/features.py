"""Account-level indicators. No weights here: every number is a raw measurement; the models learn the weights.

Groups: account, timing, repetition, text, network (from graph.py), context. `context` holds intent/narrative
signals. They are model inputs only and are never shown by name in the output.
"""
from __future__ import annotations

import hashlib
import math
import re
import sys
from pathlib import Path

import numpy as np
import pandas as pd

# Narrative lexicon from the backend (Balkan campaign tropes). Optional: the detector runs without it.
try:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))
    from lexicons import TROPES, fold  # type: ignore
except Exception:  # pragma: no cover
    TROPES, fold = {}, (lambda s: s.lower())

FEATURES: dict[str, tuple[str, str]] = {
    # account metadata
    "age_days_log": ("Account age (days, log)", "account"),
    "followers_log": ("Followers / subscribers (log)", "account"),
    "following_log": ("Accounts followed (log)", "account"),
    "follow_ratio": ("Followers vs. following balance", "account"),
    "posts_total_log": ("Lifetime post count (log)", "account"),
    "posts_per_day_log": ("Lifetime posts per day (log)", "account"),
    # timing
    "n_obs_log": ("Posts observed (log)", "timing"),
    "gap_median_log": ("Typical gap between posts (log s)", "timing"),
    "gap_cv": ("Irregularity of posting gaps", "timing"),
    "burstiness": ("Burstiness of posting", "timing"),
    "frac_gap_lt_60": ("Share of posts < 60 s apart", "timing"),
    "hour_entropy": ("Spread of activity over the day", "timing"),
    # repetition
    "self_dup_ratio": ("Repeats its own posts", "repetition"),
    "cross_dup_ratio": ("Near-duplicate posts shared with other accounts", "repetition"),
    "cross_dup_max_log": ("Size of the largest copy-paste group joined (log)", "repetition"),
    "url_ratio": ("Share of posts with links", "repetition"),
    "url_repeat_ratio": ("Re-posts the same links", "repetition"),
    # text style
    "mention_ratio": ("Share of posts with @mentions", "text"),
    "hashtag_ratio": ("Share of posts with hashtags", "text"),
    "len_mean_log": ("Typical post length (log)", "text"),
    "upper_ratio": ("Capital letters", "text"),
    "excl_ratio": ("Exclamation marks", "text"),
    "digit_ratio": ("Digits", "text"),
    "script_mix_ratio": ("Cyrillic/Latin mixed inside a word", "text"),
    "reply_ratio": ("Share of posts that are replies", "text"),
    # context: input-only, folded into "Context weighting" in the output
    "narrative_ratio": ("Narrative match", "context"),
    "narrative_kinds": ("Narrative variety", "context"),
}

_URL = re.compile(r"https?://\S+|www\.\S+", re.I)
_MENTION = re.compile(r"@\w+")
_HASHTAG = re.compile(r"#\w+")
_NONWORD = re.compile(r"[^\w\s]", re.U)
_DIGITS = re.compile(r"\d+")
_LAT, _CYR = re.compile(r"[A-Za-z]"), re.compile(r"[Ѐ-ӿ]")


def template(text: str) -> str:
    """Normalised text used to find copy-paste: no retweet prefix, links, mentions, digits or punctuation."""
    t = str(text or "").lower()
    t = re.sub(r"^rt\s+@\w+:?\s*", "", t)
    t = _URL.sub(" ", t); t = _MENTION.sub(" ", t); t = _DIGITS.sub("0", t); t = _NONWORD.sub(" ", t)
    t = " ".join(t.split())
    return hashlib.blake2b(t.encode(), digest_size=8).hexdigest() if len(t) >= 12 else ""


def _entropy(counts: np.ndarray) -> float:
    s = counts.sum()
    if s <= 0:
        return float("nan")
    p = counts[counts > 0] / s
    return float(-(p * np.log(p)).sum() / math.log(len(counts)))


def _narrative(text: str) -> set[str]:
    if not TROPES:
        return set()
    t = fold(str(text or ""))
    hits = set()
    for key, spec in TROPES.items():
        pats = [x for k, v in (spec.items() if isinstance(spec, dict) else []) if k not in ("exemplars", "label", "severity")
                and isinstance(v, list) for x in v if isinstance(x, str)]
        for pat in pats:
            try:
                if re.search(pat, t):
                    hits.add(key); break
            except re.error:
                continue
    return hits


def add_post_columns(posts: pd.DataFrame) -> pd.DataFrame:
    p = posts.copy()
    p["text"] = p["text"].fillna("").astype(str)
    p["urls"] = p.get("urls", "").fillna("").astype(str) if "urls" in p else ""
    p["parent_id"] = p.get("parent_id", "").fillna("").astype(str) if "parent_id" in p else ""
    p["tpl"] = p["text"].map(template)
    return p


def account_features(accounts: pd.DataFrame, posts: pd.DataFrame) -> pd.DataFrame:
    """One row per account_id with every FEATURES column except the network group (see graph.py)."""
    p = add_post_columns(posts)
    p = p.sort_values(["account_id", "ts"])
    tpl_accounts = p[p.tpl != ""].groupby("tpl")["account_id"].nunique()
    url_rows = p[p.urls != ""]
    rows = {}
    for aid, g in p.groupby("account_id", sort=False):
        ts = g["ts"].to_numpy(dtype=float)
        ts = ts[np.isfinite(ts)]
        gaps = np.diff(ts) if len(ts) > 1 else np.array([])
        texts = g["text"].tolist()
        n = max(1, len(texts))
        tpls = [t for t in g["tpl"] if t]
        shared = [tpl_accounts.get(t, 1) for t in tpls]
        urls = " ".join(g["urls"]).split()
        letters = sum(sum(ch.isalpha() for ch in t) for t in texts) or 1
        words = [w for t in texts for w in t.split()]
        narr = [_narrative(t) for t in texts]
        hours = np.bincount(((ts % 86400) // 3600).astype(int), minlength=24) if len(ts) else np.zeros(24)
        mu, sd = (gaps.mean(), gaps.std()) if len(gaps) else (float("nan"), float("nan"))
        rows[aid] = {
            "n_obs_log": math.log1p(len(texts)),
            "gap_median_log": math.log10(1 + float(np.median(gaps))) if len(gaps) else float("nan"),
            "gap_cv": float(sd / mu) if len(gaps) and mu > 0 else float("nan"),
            "burstiness": float((sd - mu) / (sd + mu)) if len(gaps) and (sd + mu) > 0 else float("nan"),
            "frac_gap_lt_60": float((gaps < 60).mean()) if len(gaps) else float("nan"),
            "hour_entropy": _entropy(hours) if len(ts) >= 3 else float("nan"),
            "self_dup_ratio": 1 - len(set(tpls)) / len(tpls) if tpls else 0.0,
            "cross_dup_ratio": float(np.mean([s > 1 for s in shared])) if shared else 0.0,
            "cross_dup_max_log": math.log1p(max(shared) - 1) if shared else 0.0,
            "url_ratio": float((g["urls"] != "").mean()),
            "url_repeat_ratio": 1 - len(set(urls)) / len(urls) if urls else 0.0,
            "mention_ratio": sum(bool(_MENTION.search(t)) for t in texts) / n,
            "hashtag_ratio": sum(bool(_HASHTAG.search(t)) for t in texts) / n,
            "len_mean_log": math.log1p(np.mean([len(t) for t in texts])) if texts else 0.0,
            "upper_ratio": sum(sum(ch.isupper() for ch in t) for t in texts) / letters,
            "excl_ratio": sum(t.count("!") for t in texts) / n,
            "digit_ratio": sum(sum(ch.isdigit() for ch in t) for t in texts) / letters,
            "script_mix_ratio": sum(bool(_LAT.search(w) and _CYR.search(w)) for w in words) / max(1, len(words)),
            "reply_ratio": float((g["parent_id"] != "").mean()),
            "narrative_ratio": sum(bool(s) for s in narr) / n,
            "narrative_kinds": float(len(set().union(*narr))) if narr else 0.0,
            "_last_ts": float(ts.max()) if len(ts) else float("nan"),
            "_first_ts": float(ts.min()) if len(ts) else float("nan"),
        }
    f = pd.DataFrame.from_dict(rows, orient="index")
    a = accounts.set_index("account_id").reindex(f.index)
    created = pd.to_numeric(a.get("created_ts"), errors="coerce")
    age = (f["_last_ts"] - created) / 86400
    f["age_days_log"] = np.log1p(age.clip(lower=0))
    for col, src in (("followers_log", "followers"), ("following_log", "following"), ("posts_total_log", "posts_total")):
        f[col] = np.log1p(pd.to_numeric(a.get(src), errors="coerce").clip(lower=0))
    f["follow_ratio"] = f["followers_log"] - f["following_log"]
    f["posts_per_day_log"] = np.log1p(pd.to_numeric(a.get("posts_total"), errors="coerce") / age.clip(lower=1))
    f = f.drop(columns=["_last_ts", "_first_ts"])
    f.index.name = "account_id"
    return f
