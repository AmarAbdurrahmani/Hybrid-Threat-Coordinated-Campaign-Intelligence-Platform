"""Kosovo view: the top pro-Serbian narrative pushes in real YouTube comments, as a static page.

  python -m detector.kosovo_view [--db detector/data/youtube_10k.db] [--out frontend/kosovo.html]

A "push" is a narrative (matched in English, Serbian Latin and Serbian Cyrillic). For each push: accounts, how many
posted word-for-word copies, same-text bursts (≥2 accounts within 10 minutes), share of brand-new channels
(< 180 days old when commenting), targeted videos and example texts. Nodes are shaded by a COORDINATION score, not a
BOT %: the bot model is validated on the benchmark, not yet on YouTube.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sqlite3
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
CYR = str.maketrans({"а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "ђ": "đ", "е": "e", "ж": "ž", "з": "z", "и": "i", "ј": "j",
                     "к": "k", "л": "l", "љ": "lj", "м": "m", "н": "n", "њ": "nj", "о": "o", "п": "p", "р": "r", "с": "s", "т": "t",
                     "ћ": "ć", "у": "u", "ф": "f", "х": "h", "ц": "c", "ч": "č", "џ": "dž", "ш": "š"})
PUSHES = {
    "Kosovo independence denial": r"kosovo\s*(?:je|is|=)\s*(?:srbija|serbia|srpsko|serbian)|^\W*kosovo\s+(?:srbija|serbia)\W*$|kosovo i metohija je srbija",
    "Territorial claim on Kosovo": r"(?:part|piece|heart)\s+of\s+serbia|srce\s+srbije|deo\s+srbije|dio\s+srbije|kim\s+je\s+srbija",
    "Russia / Putin as protector": r"\b(?:putin\w*|rusij\w*|russia\w*|rusi)\b",
    "Anti-Albanian hostility": r"[šs]iptar|teroris|terroris|okupator|occupier|albanian\s+(?:mafia|terror)",
    "NATO aggression / 1999 bombing": r"nato\s+(?:agres|bomb|aggress)|agresij|bombing\s+of\s+(?:serbia|yugoslavia)|\b1999\b",
    "Religious-historical claim": r"svet[ae]?\s+zemlj|holy\s+land|kolevk|cradle\s+of\s+serb|manastir|monaster|\bzavet",
    "Statehood delegitimisation": r"(?:not|isn'?t)\s+a\s+(?:real\s+)?country|fake\s+(?:country|state)|la[žz]na\s+dr[žz]ava|nije\s+dr[žz]ava|nije\s+zemlja",
    "Sovereignty rejection rhetoric": r"nikad[a]?\s+(?:ga\s+)?(?:ne\s+)?(?:damo|dati|predati|odreći|odreci)|never\s+give\s+(?:up\s+)?kosovo|ne\s+damo\s+kosov",
    "West’s double standards / Crimea": r"double\s+standard|dvostruk|crimea|\bkrim\b",
    "Reconquest rhetoric": r"(?:kosovo|kim)\s+(?:će|ce)\s+(?:biti|se\s+vratiti)|will\s+return\s+to\s+serbia|we\s+will\s+be\s+back|vrati(?:ćemo|cemo)",
}


# Motive clades: a fixed taxonomy, measured (not assumed) in the comments. Matched on lower-cased text with Serbian
# Cyrillic transliterated to Latin. A comment can carry more than one motive.
MOTIVES = {
    "Kosovo independence denial": (r"kosovo\s*(?:je|is|=)\s*(?:srbija|serbia|srpsk|serbian)|(?:part|piece|heart)\s+of\s+serbia|srce\s+srbije|"
                                   r"de?o\s+srbije|nikad[a]?\s+(?:ga\s+)?(?:ne\s+)?(?:damo|dati|predati)|kim\s+je\s+srbija|kosovo\s+i\s+metohija", "#991b1b"),
    "Russian / pan-Slavic brotherhood": (r"\b(?:putin\w*|rusij\w*|russia\w*|rusi|rusk\w*|bratsk\w*|bra[ćc]a\s+rus\w*|slaven\w*|slavic|pan-?slav\w*|"
                                         r"slovens\w*|majka\s+rusija)\b", "#1e3a8a"),
    "Anti-NATO / anti-West": (r"\bnato\b.*(?:agres|bomb|aggress|zlo[čc]in|crime|occup|okup)|agresij|double\s+standard|dvostruk|"
                              r"(?:usa|america|amerika|zapad|west)\w*\s+(?:is|je|su)?\s*(?:evil|zlo|imperij|kriv|guilty)", "#7c2d12"),
    "Anti-Albanian hostility": (r"[šs]iptar|teroris|terroris|okupator|occupier|albanian\s+(?:mafia|terror)", "#111827"),
    "Religious-historical claim": (r"svet[ae]?\s+zemlj|holy\s+land|kolevk|cradle\s+of\s+serb|manastir|monaster|\bzavet|pe[ćc]k\w*\s+patrijar", "#6b21a8"),
    "Statehood delegitimisation": (r"(?:not|isn'?t)\s+a\s+(?:real\s+)?country|fake\s+(?:country|state)|la[žz]na\s+dr[žz]ava|nije\s+dr[žz]ava|"
                                r"nije\s+zemlja|not\s+recogni[sz]", "#b45309"),
    "Islamist / Sharia advocacy": (r"\b(?:sharia\w*|sheriat\w*|[šs]erijat\w*|khilaf\w*|hilafet\w*|caliphat\w*|kalifat\w*|halifat\w*|"
                                   r"jihad\w*|d[žz]ihad\w*|xhihad\w*|ummah)\b", "#065f46"),
}


def _pseudo(a: str) -> str:
    return "yt_" + hashlib.blake2b(str(a).encode(), digest_size=4, key=b"htccip-kosovo").hexdigest()


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s]", "", str(s).lower())).strip()


def _lang(s: str) -> str:
    if re.search(r"[Ѐ-ӿ]", s):
        return "SR (Cyrillic)"
    if re.search(r"\b(je|nije|srbija|nikad|samo|sve|što|sto|kosovu|srbi)\b", s.lower()):
        return "SR (Latin)"
    if re.search(r"\b(është|eshte|nuk|dhe|shqip)\b", s.lower()):
        return "SQ"
    return "EN"


def bench_stats() -> dict:
    """Held-out benchmark: accuracy, and bot farms confirmed against the human labels."""
    import scipy.sparse as sp
    import scipy.sparse.csgraph as cg
    meta = json.loads((HERE / "model" / "model.json").read_text(encoding="utf-8"))
    d = HERE / "data" / "scores_cresci2017_test" / "neo4j"
    out = {"f1": meta["metrics_random_split"]["f1"], "ci": meta.get("f1_ci"), "ece": meta["metrics_random_split"]["ece"],
           "unseen": [min(meta["lofo_f1"].values()), max(meta["lofo_f1"].values())]}
    if not (d / "nodes_account.csv").exists():
        return out
    n = pd.read_csv(d / "nodes_account.csv", dtype={"account_id": str})
    r = pd.read_csv(d / "rels_account.csv", dtype={"src": str, "dst": str})
    r = r[r.type.isin(["COPY", "SYNC"])]
    ix = {a: i for i, a in enumerate(n.account_id)}
    m = sp.coo_matrix((np.ones(len(r)), (r.src.map(ix), r.dst.map(ix))), shape=(len(n), len(n)))
    _, comp = cg.connected_components(m + m.T, directed=False)
    n["comp"] = comp
    g = n.groupby("comp").agg(size=("account_id", "size"), true_bot=("label", "mean"))
    farms = g[(g["size"] >= 5) & (g.true_bot >= 0.9)]
    inside = n[n.comp.isin(farms.index)]
    pred, lab = n.verdict == "BOT", n.label == 1
    out.update({"n": int(len(n)), "correct": int((pred == lab).sum()), "fp": int((pred & ~lab).sum()), "fn": int((~pred & lab).sum()),
                "bots": int(lab.sum()), "farms": int(len(farms)), "farm_accounts": int(farms["size"].sum()),
                "farm_true_bots": int(inside.label.sum()), "farm_caught": int(((inside.verdict == "BOT") & (inside.label == 1)).sum()),
                "farm_sizes": [int(x) for x in sorted(farms["size"], reverse=True)]})
    # confidence tiers on held-out accounts, and how often each tier was really a bot
    sc = pd.read_csv(HERE / "data" / "scores_cresci2017_test" / "scores.csv")
    tiers = []
    for lo, hi, nm in [(99.5, 101, "100% (≥ 99.5%)"), (70, 99.5, "70 – 99.5%"), (50, 70, "50 – 70%"), (0, 50, "below 50% (not bot)")]:
        t = sc[(sc.bot_probability >= lo) & (sc.bot_probability < hi)]
        tiers.append({"name": nm, "n": int(len(t)), "true": round(100 * float(t.label.mean()), 1) if len(t) else None, "bots": int(t.label.sum())})
    hi70 = sc[sc.bot_probability >= 70]
    out.update({"tiers": tiers, "n70": int(len(hi70)), "true70": round(100 * float(hi70.label.mean()), 1)})
    # how much data stands behind the model
    try:
        from . import datasets, evaluate as E
        acc, posts = datasets.load("cresci2017")
        acc = acc[acc.account_id.isin(set(posts.account_id))]
        _, _, te = E.split_stratified(acc, test=0.2, val=0.1, seed=0)
        out.update({"train_posts": int(len(posts)), "train_accounts": int(len(acc)), "test_posts": int(posts.account_id.isin(set(te)).sum())})
    except Exception:
        pass
    return out


def build(db: Path, out: Path, max_nodes: int = 2400) -> Path:
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    p = pd.read_sql("select c.user, c.text, c.ts, c.thread_id, c.account, t.title from comments c "
                    "left join threads t on t.id = c.thread_id where coalesce(c.simulated, 0) = 0", con)
    p["ts"] = np.where(p.ts > 1e11, p.ts / 1000, p.ts)

    def created(a):
        try:
            return pd.Timestamp(json.loads(a).get("created")).timestamp()
        except Exception:
            return np.nan
    p["created"] = p.account.map(created)
    p["age_days"] = (p.ts - p.created) / 86400
    p["t"] = p.text.fillna("").str.lower().map(lambda s: s.translate(CYR))
    p["norm"] = p.text.map(_norm)
    shared = p[p.norm.str.len() >= 6].groupby("norm").user.nunique()
    p["copied"] = p.norm.map(shared).fillna(1) > 1
    # bursts: same normalised text by ≥2 different accounts within 10 minutes
    p = p.sort_values("ts")
    burst = set()
    for _, g in p[p.copied].groupby("norm"):
        t, u, idx = g.ts.to_numpy(), g.user.to_numpy(), g.index.to_numpy()
        for i in range(1, len(t)):
            if t[i] - t[i - 1] <= 600 and u[i] != u[i - 1]:
                burst.update([idx[i], idx[i - 1]])
    p["burst"] = p.index.isin(burst)
    pushes, members = [], {}
    for name, rx in PUSHES.items():
        g = p[p.t.str.contains(rx, regex=True)]
        if g.empty:
            continue
        vc = g.groupby("norm").user.nunique().sort_values(ascending=False)
        top = [(g[g.norm == k].text.iloc[0][:110], int(v)) for k, v in vc.head(3).items()]
        acc = g.groupby("user").agg(copied=("copied", "max"), burst=("burst", "max"), age=("age_days", "min"))
        new = float((acc.age < 180).mean())
        pushes.append({"name": name, "comments": int(len(g)), "accounts": int(acc.shape[0]),
                       "copy_accounts": int(acc.copied.sum()), "burst_accounts": int(acc.burst.sum()),
                       "videos": int(g.thread_id.nunique()), "new_share": round(new, 3),
                       "langs": g.text.map(_lang).value_counts().to_dict(), "examples": top,
                       "targets": [(k[:70], int(v)) for k, v in g.title.value_counts().head(3).items()]})
        members[name] = acc
    pushes.sort(key=lambda d: -d["accounts"])
    pushes = pushes[:10]
    # graph: accounts per push (copy-paste accounts first), edges = posted the identical text
    nodes, idx = [], {}
    budget = max_nodes  # all accounts of every push
    for k, pu in enumerate(pushes):
        acc = members[pu["name"]].copy()
        acc["score"] = 100 * (0.55 * acc.copied + 0.30 * acc.burst + 0.15 * (acc.age < 180))
        for u, r in acc.sort_values("score", ascending=False).head(budget).iterrows():
            if u in idx:
                continue
            idx[u] = len(nodes)
            nodes.append({"id": _pseudo(u), "k": k, "s": round(float(r.score), 0), "cp": bool(r.copied), "b": bool(r.burst),
                          "new": bool(r.age < 180) if r.age == r.age else None, "u": u})
    # node details: the account's comments, profile, the pushes it is in, and how many accounts share its texts
    in_push = {name: set(members[name].index) for name in members}
    pk = {pu["name"]: k for k, pu in enumerate(pushes)}
    def fol_of(a):
        try:
            return json.loads(a).get("followers")
        except Exception:
            return None
    for nd in nodes:
        u = nd.pop("u")
        mine = p[p.user == u].sort_values("ts", ascending=False)
        nd["age"] = None if mine.age_days.isna().all() else int(mine.age_days.min())
        f = mine.account.map(fol_of).dropna()
        nd["fol"] = None if f.empty else int(f.max())
        nd["cm"] = [[str(r.text)[:220], pd.to_datetime(r.ts, unit="s").strftime("%Y-%m-%d %H:%M"), str(r.title or "")[:60],
                     int(shared.get(r.norm, 1))] for r in mine.head(5).itertuples()]
        nd["n"] = int(len(mine))
        nd["pk"] = [pk[name] for name in pk if u in in_push.get(name, ())]
        nd["partners"] = int(max([int(shared.get(x, 1)) for x in mine.norm] + [1]) - 1)
        vids = int(mine.thread_id.nunique())
        copies = nd["partners"] + 1
        comps = []
        if copies > 1:
            comps.append(["Word-for-word copies", round(40 * min(1.0, np.log(copies) / np.log(51)), 1),
                          f"its text was posted by {copies} accounts"])
        if nd["b"]:
            comps.append(["Burst with other accounts", 25.0, "same text from another account within 10 minutes"])
        a = nd["age"]
        if a is not None and a < 365:
            pts = 15.0 if a < 30 else 9.0 if a < 180 else 4.0
            comps.append(["Fresh channel", pts, f"channel {a} days old when it commented"])
        if nd["fol"] == 0:
            comps.append(["No audience", 5.0, "0 subscribers"])
        if vids > 1:
            comps.append(["Repeated across videos", round(min(10.0, 2.5 * (vids - 1)), 1), f"commented under {vids} different videos"])
        if len(nd["pk"]) >= 2:
            comps.append(["Several narratives", 5.0, f"part of {len(nd['pk'])} pushes"])
        nd["comps"] = comps
        nd["s"] = round(min(100.0, sum(c[1] for c in comps)), 1)
    links, seen = [], set()
    def add(a, b, t):
        key = (min(a, b), max(a, b), t)
        if a != b and key not in seen:
            seen.add(key); links.append({"s": a, "t": b, "y": t})
    # identical text: wire each copy group fully (groups capped at 14 accounts for readability)
    for _, g in p[p.user.isin(idx) & p.copied].groupby("norm"):
        us = list(dict.fromkeys(g.user))[:14]
        for x in range(len(us)):
            for y in range(x + 1, len(us)):
                add(idx[us[x]], idx[us[y]], "c")
    # same video within 30 minutes: link each comment to its nearest following comments by other accounts
    q = p[p.user.isin(idx)].sort_values("ts")
    for _, g in q.groupby("thread_id"):
        t, u = g.ts.to_numpy(), g.user.to_numpy()
        for x in range(len(t)):
            k = 0
            for y in range(x + 1, len(t)):
                if t[y] - t[x] > 1800 or k >= 3:
                    break
                if u[y] != u[x]:
                    add(idx[u[x]], idx[u[y]], "v"); k += 1
    # Kosovo: coordinated groups = same pro-Serbian text posted by >= 3 different accounts
    serb = p[p.t.str.contains("|".join(f"(?:{r})" for r in PUSHES.values()), regex=True)]
    grp = serb[serb.norm.str.len() >= 6].groupby("norm").user.nunique()
    kgroups = grp[grp >= 3]
    kosovo = {"groups": int(len(kgroups)), "group_accounts": int(serb[serb.norm.isin(kgroups.index)].user.nunique()),
              "largest": int(kgroups.max()) if len(kgroups) else 0,
              "bursts": int(serb[serb.burst].user.nunique())}
    # motive clades: share of all comments, coordinated (copy-paste) part, and a daily timeline
    p["day"] = pd.to_datetime(p.ts, unit="s").dt.strftime("%Y-%m-%d")
    days = sorted(p.day.dropna().unique())[-45:]
    def followers(a):
        try:
            return json.loads(a).get("followers")
        except Exception:
            return None
    p["followers"] = p.account.map(followers)
    p["copies"] = p.norm.map(shared).fillna(1).astype(int)
    M = np.column_stack([p.t.str.contains(rx, regex=True).to_numpy() for rx, _ in MOTIVES.values()])
    anyrow = M.any(axis=1)
    # volatility 1: 95% range of each motive's share (bootstrap over comments that carry a motive)
    rng = np.random.default_rng(0)
    Mm = M[anyrow]
    boot = np.array([Mm[rng.integers(0, len(Mm), len(Mm))].sum(0) / len(Mm) for _ in range(500)]) if len(Mm) else np.zeros((1, M.shape[1]))
    lo, hi = np.percentile(boot, 2.5, axis=0), np.percentile(boot, 97.5, axis=0)
    # volatility 2: day-to-day swing of the share, on days with enough motive comments
    pd_any = p[anyrow].groupby("day").size()
    busy = pd_any[pd_any >= 15].index
    motives, any_m = [], np.zeros(len(p), bool)
    for j, (name, (rx, col)) in enumerate(MOTIVES.items()):
        mm = M[:, j]
        any_m |= mm
        g = p[mm]
        daily_share = (g.groupby("day").size().reindex(busy).fillna(0) / pd_any.reindex(busy)) if len(busy) else pd.Series(dtype=float)
        cv = float(daily_share.std() / daily_share.mean()) if len(daily_share) > 2 and daily_share.mean() > 0 else None
        acc = g.groupby("user").agg(age=("age_days", "min"), fol=("followers", "max"))
        ex = g.sort_values(["copies", "ts"], ascending=[False, False]).drop_duplicates("norm").head(3)
        evidence = [{"who": _pseudo(r.user), "text": (str(r.text)[:117].rstrip() + "…") if len(str(r.text)) > 120 else str(r.text), "age": None if r.age_days != r.age_days else int(r.age_days),
                     "fol": None if r.followers is None or r.followers != r.followers else int(r.followers), "copies": int(r.copies),
                     "day": r.day, "video": str(r.title or "")[:70]} for r in ex.itertuples()]
        profile = {"new180": round(float((acc.age < 180).mean()), 3) if len(acc) else 0, "new365": round(float((acc.age < 365).mean()), 3) if len(acc) else 0,
                   "median_age_y": round(float(acc.age.median()) / 365, 1) if acc.age.notna().any() else None,
                   "median_fol": int(acc.fol.median()) if acc.fol.notna().any() else None,
                   "zero_fol": round(float((acc.fol == 0).mean()), 3) if acc.fol.notna().any() else None}
        motives.append({"name": name, "color": col, "comments": int(mm.sum()), "accounts": int(g.user.nunique()),
                        "coordinated": int(g.copied.sum()), "pct_all": round(100 * mm.mean(), 2),
                        "daily": [int(((g.day == d)).sum()) for d in days],
                        "example": (g[g.copied].text.iloc[0] if g.copied.any() else (g.text.iloc[0] if len(g) else ""))[:120],
                        "ci": [round(100 * float(lo[j]), 1), round(100 * float(hi[j]), 1)], "cv": None if cv is None else round(cv, 2),
                        "evidence": evidence, "profile": profile})
    tot = int(any_m.sum())
    for mo in motives:
        mo["pct_motive"] = round(100 * mo["comments"] / tot, 1) if tot else 0.0
    motives.sort(key=lambda m: -m["comments"])
    clade_block = {"motives": motives, "days": days, "with_motive": tot, "total": int(len(p)), "busy_days": int(len(busy))}
    data = {"pushes": pushes, "nodes": nodes, "links": links[:12000], "kosovo": kosovo, "bench": bench_stats(), "clades": clade_block,
            "stats": {"comments": int(len(p)), "accounts": int(p.user.nunique()), "videos": int(p.thread_id.nunique()),
                      "in_pushes": int(sum(pu["accounts"] for pu in pushes)),
                      "copy": int(sum(pu["copy_accounts"] for pu in pushes))}}
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(TEMPLATE.replace("__DATA__", json.dumps(data, ensure_ascii=False)), encoding="utf-8")
    return out


TEMPLATE = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<link rel="icon" href="data:image/svg+xml,%3Csvg%20viewBox%3D%220%200%2040%2044%22%20aria-label%3D%22Angry%20Birds%20logo%22%20role%3D%22img%22%3E%3Cpath%20d%3D%22M20%202%20L36%208%20V21%20C36%2031%2029%2038.5%2020%2042%20C11%2038.5%204%2031%204%2021%20V8%20Z%22%20fill%3D%22%230b0f19%22/%3E%3Cpath%20d%3D%22M20%205.5%20L33%2010.4%20V21%20C33%2029.3%2027.4%2035.6%2020%2038.7%20C12.6%2035.6%207%2029.3%207%2021%20V10.4%20Z%22%20fill%3D%22none%22%20stroke%3D%22%23ffffff%22%20stroke-opacity%3D%22.16%22/%3E%3Cline%20x1%3D%2213%22%20y1%3D%2217%22%20x2%3D%2227%22%20y2%3D%2215%22%20stroke%3D%22%23cbd5e1%22%20stroke-width%3D%221.6%22/%3E%3Cline%20x1%3D%2213%22%20y1%3D%2217%22%20x2%3D%2220%22%20y2%3D%2229%22%20stroke%3D%22%23cbd5e1%22%20stroke-width%3D%221.6%22/%3E%3Cline%20x1%3D%2227%22%20y1%3D%2215%22%20x2%3D%2220%22%20y2%3D%2229%22%20stroke%3D%22%23ef4444%22%20stroke-width%3D%221.8%22/%3E%3Ccircle%20cx%3D%2213%22%20cy%3D%2217%22%20r%3D%223.6%22%20fill%3D%22%23e5e7eb%22/%3E%3Ccircle%20cx%3D%2227%22%20cy%3D%2215%22%20r%3D%223.6%22%20fill%3D%22%23e5e7eb%22/%3E%3Ccircle%20cx%3D%2220%22%20cy%3D%2229%22%20r%3D%224.8%22%20fill%3D%22%23dc2626%22/%3E%3Ccircle%20cx%3D%2220%22%20cy%3D%2229%22%20r%3D%227.6%22%20fill%3D%22none%22%20stroke%3D%22%23dc2626%22%20stroke-opacity%3D%22.45%22%20stroke-width%3D%221.4%22/%3E%3C/svg%3E"><title>Angry Birds @ Republika e Kosovës</title>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&family=JetBrains+Mono:wght@500&display=swap" rel="stylesheet">
<script src="https://cdnjs.cloudflare.com/ajax/libs/d3/7.9.0/d3.min.js"></script>
<style>
:root{--bg:#f4f6fa;--cream:#ffffff;--cream2:#e3e8f1;--sky:#f3f6fb;--card:#fff;--line:#e5e7eb;--ink:#161616;--mut:#6b7280;--kblue:#04408b;--kgold:#d2ab67;--knavy:#032f66}
*{box-sizing:border-box}html{scroll-behavior:smooth}body{overflow-x:hidden}html,body{margin:0;background:var(--bg);color:var(--ink);font:14px/1.45 Inter,system-ui,sans-serif;-webkit-font-smoothing:antialiased}
header{position:sticky;top:0;z-index:5;height:72px;border-top:3px solid var(--kgold);background:#ffffff !important;border-bottom:1px solid #e3e8f1;display:flex;align-items:center;gap:22px;padding:0 26px;background:var(--card);border-bottom:1px solid var(--line)}
h1{font-size:16px;margin:0;font-weight:700;letter-spacing:-.015em;white-space:nowrap}.sub{color:var(--mut);font-size:12px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;max-width:520px}
.tabs{display:flex;gap:6px;margin-left:18px}.tabs a{white-space:nowrap;font:600 12.5px Inter;padding:8px 12px;border-radius:8px;color:#374151;text-decoration:none;border:1px solid var(--line)}
.tabs a.on{background:#0b0f19;color:#fff;border-color:#0b0f19}
.kpis{display:flex;gap:26px;margin-left:auto}.kpi{text-align:right}.kpi b{display:block;font:700 19px/1.1 Inter;letter-spacing:-.02em}
.kpi span{font-size:10.5px;color:var(--mut);text-transform:uppercase;letter-spacing:.06em;white-space:nowrap}
main{display:grid;grid-template-columns:minmax(0,1fr) 440px;height:calc(100vh - 150px);min-height:520px;border-bottom:1px solid var(--line)}
.card .q,.card .tg{display:none}.card.on .q,.card.on .tg{display:block}.card .more{font-size:11.5px;color:#6b7280;margin-top:4px}.card.on .more{display:none}
.scrollcue{position:absolute;left:50%;bottom:14px;transform:translateX(-50%);background:#04408b;color:#fff;font:600 12px Inter;padding:8px 14px;border-radius:999px;text-decoration:none;box-shadow:0 6px 18px #0b0f1930}
.zoomhint{position:absolute;right:16px;top:12px;font-size:11.5px;color:#dbe6f7;background:#ffffff14;border:1px solid #ffffff26;border-radius:7px;padding:4px 8px}
section.s{max-width:1180px;margin:0 auto;padding:64px 28px 8px;scroll-margin-top:84px;content-visibility:auto;contain-intrinsic-size:auto 1100px}section.s h2{font-size:28px;letter-spacing:-.025em;margin:0 0 6px}
section.s .lead{color:var(--mut);font-size:15px;max-width:760px;margin:0 0 26px}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(250px,1fr));gap:14px}
.tile{background:#fff;border:1px solid var(--line);border-radius:14px;padding:20px 22px}
.tile .n{font:800 44px/1 Inter;letter-spacing:-.04em;font-variant-numeric:tabular-nums}.tile .l{font-weight:650;margin-top:8px}
.tile .d{color:var(--mut);font-size:13px;margin-top:6px}.tile.dark{background:#04408b;color:#fff;border-color:#04408b}.tile.dark .d{color:#9ca3af}
.tag{display:inline-block;font:700 10.5px Inter;letter-spacing:.07em;text-transform:uppercase;padding:4px 8px;border-radius:6px;margin-bottom:10px}
.ok{background:#d1fae5;color:#065f46}.warn{background:#fef3c7;color:#92400e}
.plain{display:grid;grid-template-columns:repeat(auto-fit,minmax(330px,1fr));gap:14px}
.plain .tile b.k{display:block;font-size:13px;text-transform:uppercase;letter-spacing:.06em;color:var(--mut);margin-bottom:6px}
.sec li{margin:8px 0}
#secnav a{font:600 12.5px Inter;color:#374151;text-decoration:none;padding:8px 11px;border-radius:8px;white-space:nowrap}
#secnav a:hover{background:#f1f4fa;color:#04408b}#secnav a.on{background:#04408b;color:#fff}
#totop{position:fixed;right:18px;bottom:18px;z-index:6;background:#04408b;color:#fff;font:600 12px Inter;text-decoration:none;padding:9px 13px;border-radius:999px;opacity:0;pointer-events:none;transition:opacity .2s}
#totop.show{opacity:.92;pointer-events:auto}
@media(max-width:1100px){#secnav{display:none}}section.band{padding-bottom:8px}.tile{border-color:#e3e8f1}section.s h2{color:#04408b}section.s h2::after{content:'';display:block;width:46px;height:3px;background:#d2ab67;border-radius:2px;margin-top:10px}section.blue{background:#04408b;max-width:none;width:100%;padding:64px max(28px,calc((100% - 1124px)/2)) 56px;margin-top:40px}section.blue h2{color:#fff}section.blue .lead{color:#c9d7ee}section.blue .tile{border-color:transparent}section.blue .tile.dark{background:#d2ab67;color:#161616}section.blue .tile.dark .d{color:#3b2f12}.chipx{background:#f3f4f6;border-radius:999px;padding:4px 10px;color:#374151}footer{max-width:1180px;margin:0 auto;padding:40px 28px 60px;color:var(--mut);font-size:12.5px}
#stage{position:relative;overflow:hidden;background:radial-gradient(ellipse at 42% 32%,#0b5bb8 0%,#04408b 42%,#022a5c 100%)}svg#g{width:100%;height:100%;display:block}
.hlabel{font:800 13px Inter;fill:#ffffff;paint-order:stroke;stroke:#03306a;stroke-width:4px}.hsub{font:600 11px Inter;fill:#c9d7ee;paint-order:stroke;stroke:#03306a;stroke-width:4px}
aside{background:#ffffff;border-left:1px solid #e3e8f1;overflow:auto;padding:18px 22px}
.card{background:#fff;border:1px solid #e3e8f1;border-radius:12px;padding:12px 14px;margin:0 0 10px;cursor:pointer;transition:border-color .15s}
.card:hover,.card.on{border-color:#04408b}
.rank{font:800 20px Inter;color:#d2ab67;margin-right:10px;vertical-align:-2px}.nm{font-weight:700;font-size:14px}
.facts{display:flex;flex-wrap:wrap;gap:6px 14px;margin:8px 0 6px;font-size:12px;color:#374151}.facts b{font-variant-numeric:tabular-nums}
.stats{display:grid;grid-template-columns:repeat(3,1fr);gap:6px;margin:12px 0 4px}.stats div{background:#f6f8fc;border:1px solid #e3e8f1;border-radius:9px;padding:7px 9px}
.stats b{display:block;font:800 24px/1.05 Inter;letter-spacing:-.02em;font-variant-numeric:tabular-nums}.stats span{font-size:10.5px;color:#6b7280;text-transform:uppercase;letter-spacing:.04em}
.q{font-size:12.5px;background:#f9fafb;border-left:3px solid #dc2626;padding:5px 9px;margin:5px 0;border-radius:0 6px 6px 0}
.q span{color:var(--mut);font-size:11px;margin-left:6px}.tg{font-size:11.5px;color:var(--mut);margin-top:4px}
.legend{position:absolute;right:14px;bottom:12px;display:flex;align-items:center;gap:7px;background:#ffffff14;border:1px solid #ffffff26;border-radius:999px;padding:5px 11px;font:500 10.5px Inter;color:#dbe6f7}
.ramp{width:90px;height:6px;border-radius:4px;margin:0;background:linear-gradient(90deg,#6f86b0,#f9a8a8 40%,#ef4444 70%,#ff2d2d)}
.note{font-size:12px;color:var(--mut);background:#f4f7fc;border:1px solid #dbe4f3;border-radius:10px;padding:10px 12px;margin-bottom:14px}
</style></head><body>
<header><div style="display:flex;align-items:center;gap:11px"><svg class="kodlogo" viewBox="0 0 40 44" width="48" height="53" aria-label="Angry Birds logo" role="img"><path d="M20 2 L36 8 V21 C36 31 29 38.5 20 42 C11 38.5 4 31 4 21 V8 Z" fill="#04408b"/><path d="M20 5.5 L33 10.4 V21 C33 29.3 27.4 35.6 20 38.7 C12.6 35.6 7 29.3 7 21 V10.4 Z" fill="none" stroke="#ffffff" stroke-opacity=".16"/><line x1="13" y1="17" x2="27" y2="15" stroke="#cbd5e1" stroke-width="1.6"/><line x1="13" y1="17" x2="20" y2="29" stroke="#cbd5e1" stroke-width="1.6"/><line x1="27" y1="15" x2="20" y2="29" stroke="#ef4444" stroke-width="1.8"/><circle cx="13" cy="17" r="3.6" fill="#e5e7eb"/><circle cx="27" cy="15" r="3.6" fill="#e5e7eb"/><circle cx="20" cy="29" r="4.8" fill="#dc2626"/><circle cx="20" cy="29" r="7.6" fill="none" stroke="#dc2626" stroke-opacity=".45" stroke-width="1.4"/></svg><div><h1 style="font:800 26px/1 Inter;letter-spacing:-.02em;margin:0;color:#04408b;white-space:nowrap">Angry Birds</h1></div></div><div style="margin-left:4px;display:flex;align-items:center;gap:14px"><span style="width:1px;height:36px;background:#dbe2ee"></span><img src="mod-logo.png" alt="Republika e Kosovës · Ministria e Mbrojtjes" style="height:44px;width:auto;display:block"></div>

<nav id="secnav" style="margin-left:auto;display:flex;gap:4px;flex-wrap:nowrap">
 <a href="#top">Pushes</a><a href="#numbers">Numbers</a><a href="#motives">Motives</a><a href="#farms">Bot farms</a><a href="#accuracy">Accuracy</a><a href="#theatres">Theatres</a></nav>
</header><a id="totop" href="#top" aria-label="Back to top">↑ top</a>
<main id="top" style="scroll-margin-top:84px"><div id="stage"><svg id="g"></svg><a class="scrollcue" href="#numbers">Scroll for motives, bot farms &amp; accuracy ↓</a><div class="zoomhint">Ctrl + scroll to zoom · drag to pan</div>
<div class="legend" style="right:auto;left:14px"><i style="width:18px;height:2px;background:#ff4d4d;display:inline-block"></i>same text<i style="width:18px;height:1px;background:#cfe0ff;display:inline-block;margin-left:6px"></i>same video ≤ 30 min</div><div class="legend" title="Coordination likelihood per account: copies, bursts, fresh channel, no audience, repetition across videos and narratives. Lines join accounts that posted the identical text."><span>coordination</span><div class="ramp"></div><span>0–100</span></div></div>
<aside id="side"></aside></main>
<section class="s blue" id="numbers"><h2>By the numbers</h2>
<p class="lead">A trained model, not a rule list. It learns every weight from labelled accounts and is tested on accounts it has never seen.</p>
<div class="grid" id="numTiles"></div>
<div class="grid" style="grid-template-columns:minmax(0,1.2fr) minmax(0,1fr);margin-top:14px">
<div class="tile"><b class="k" style="display:block;font-size:13px;text-transform:uppercase;letter-spacing:.06em;color:var(--mut);margin-bottom:12px">How many bots, how sure, how much to trust it</b><div id="tiers"></div>
<div class="d" style="margin-top:10px">"Truly bots" = checked against human labels on held-out accounts. The higher the confidence, the more often it is right: that is what a calibrated model looks like.</div></div>
<div class="tile"><b class="k" style="display:block;font-size:13px;text-transform:uppercase;letter-spacing:.06em;color:var(--mut);margin-bottom:12px">Gets better with every labelled batch</b>
<ol style="margin:0;padding-left:18px;font-size:13.5px;line-height:1.6"><li><b>Collect</b> public comments through the official APIs.</li><li><b>Label</b>: two analysts mark a blind sample bot / human.</li>
<li><b>Retrain</b> LightGBM + the graph network on all labels so far.</li><li><b>Prove</b> on held-out accounts. A new model replaces the old one only if it scores better.</li></ol>
<div class="d" style="margin-top:10px">The Kosovo labelling sheet is the next batch, which turns Kosovo leads into confirmed numbers.</div></div></div>
<div class="tile" style="margin-top:14px"><b class="k" style="display:block;font-size:13px;text-transform:uppercase;letter-spacing:.06em;color:var(--mut);margin-bottom:10px">Main sources</b><div id="sources" class="plain"></div></div></section>
<section class="s band" id="motives"><h2>Motive clades · what is being pushed</h2>
<p class="lead" id="mlead"></p>
<div class="grid" style="grid-template-columns:minmax(0,1.15fr) minmax(0,1fr)"><div class="tile"><b class="k" style="display:block;font-size:13px;text-transform:uppercase;letter-spacing:.06em;color:var(--mut);margin-bottom:10px">Share of comments that carry a motive</b><div id="mbars"></div>
<div class="d" style="margin-top:10px">Dark part of each bar = posted word-for-word by other accounts too (coordinated). A comment can carry more than one motive.</div></div>
<div class="tile"><b class="k" style="display:block;font-size:13px;text-transform:uppercase;letter-spacing:.06em;color:var(--mut);margin-bottom:10px">Motive comments per day</b><svg id="mtime" width="100%" height="300"></svg><div id="mleg" class="d"></div></div></div>
<h3 style="font-size:19px;margin:34px 0 4px;letter-spacing:-.01em">Inside each motive: the real comments and who posts them</h3>
<p class="lead" style="font-size:13.5px;margin-bottom:16px">Real public comments, most-copied first. Avatars are generated from each author's pseudonym: the same account always gets the same avatar, and no real photo or name is shown.
<b>Range</b> = where the motive's share stays in 95% of 500 re-draws of the comments. <b>Day-to-day</b> = how much the share swings between busy days.</p>
<div id="mcards" style="display:grid;grid-template-columns:repeat(auto-fit,minmax(520px,1fr));gap:14px"></div></section>
<section class="s" id="farms"><h2>Bot farms</h2><p class="lead">Where the truth is known we confirm. Where it is not yet known we detect, and we say which is which.</p><div class="grid" id="farmTiles"></div></section>
<section class="s band" id="accuracy"><h2>How sure are we? In plain words</h2><p class="lead">Tested on accounts the model had never seen, against labels checked by humans.</p><div class="plain" id="plain"></div></section>

<section class="s band" id="theatres" style="max-width:1320px">
<h2 style="font-size:34px">Same method, other theatres</h2>
<p class="lead" style="font-size:16px">Built and proven on Kosovo. The same trained detector, account graph and motive clades apply wherever coordinated accounts fight for opinion.</p>
<div id="thCards" style="display:grid;grid-template-columns:repeat(auto-fit,minmax(290px,1fr));gap:14px"></div>
<div class="tile" style="margin-top:16px;padding:22px 24px">
 <div style="display:flex;align-items:baseline;gap:12px;flex-wrap:wrap"><b style="font-size:16px">How each theatre shows up in our signals</b>
 <span class="d" style="margin:0">Lines = the signal that reveals that theatre’s campaigns · hover a theatre · <b style="color:#065f46">live</b> = working today · <b style="color:#92400e">next</b> = on the roadmap</span></div>
 <svg id="thGraph" width="100%" height="460" style="display:block;margin-top:10px"></svg></div>
</section>
<footer>Angry Birds @ Ministria e Mbrojtjes · hackathon prototype · one question per account: is it a bot? · trained model: LightGBM + relational graph network · benchmark: Cresci-2017 (human-labelled) · Kosovo data: public YouTube comments via the official API, authors pseudonymised. It measures automation and coordination; it never attributes activity to a state, group or person.</footer>
<script>
const D = __DATA__, S = D.stats;
const shade = d3.scaleLinear().domain([0, 30, 55, 80, 100]).range(["#e5e7eb", "#fca5a5", "#dc2626", "#7f1d1d", "#120202"]).clamp(true);
(document.getElementById("sub") || {}).textContent = `${S.comments.toLocaleString()} real comments · ${S.accounts.toLocaleString()} accounts · ${S.videos} Kosovo/Serbia videos · authors pseudonymised`;
(document.getElementById("kpis") || {}).innerHTML = [[S.in_pushes.toLocaleString(), "accounts pushing"], [S.copy.toLocaleString(), "copy-paste accounts"], [D.pushes.length, "pushes found"]]
  .map(([v, l]) => `<div class="kpi"><b>${v}</b><span>${l}</span></div>`).join("");
const svg = d3.select("#g"), root = svg.append("g"), W = () => svg.node().clientWidth, H = () => svg.node().clientHeight;
const zoom = d3.zoom().scaleExtent([0.3, 5]).filter((e) => e.type !== "wheel" || e.ctrlKey || e.metaKey)
  .on("zoom", (e) => root.attr("transform", e.transform)); svg.call(zoom);
const gH = root.append("g"), gL = root.append("g"), gN = root.append("g"), gT = root.append("g");
const n = D.pushes.length, cx = (k) => W() / 2 + Math.cos(2 * Math.PI * k / n - 1.57) * W() * 0.33, cy = (k) => H() / 2 + Math.sin(2 * Math.PI * k / n - 1.57) * H() * 0.34;
const nodes = D.nodes.map((d) => ({ ...d })), links = D.links.map((l) => ({ source: l.s, target: l.t, y: l.y }));
const nshade = d3.scaleLinear().domain([0, 30, 60, 85, 100]).range(["#6f86b0", "#f9a8a8", "#ef4444", "#ff2d2d", "#ff1a1a"]).clamp(true);
svg.append("defs").html('<filter id="glow" x="-80%" y="-80%" width="260%" height="260%"><feGaussianBlur stdDeviation="2.6" result="b"/><feMerge><feMergeNode in="b"/><feMergeNode in="SourceGraphic"/></feMerge></filter>');
const LOP = (l) => l.y === "c" ? 0.55 : 0.14;
const link = gL.selectAll("line").data(links).join("line").attr("stroke", (l) => l.y === "c" ? "#ff4d4d" : "#cfe0ff").attr("stroke-opacity", LOP).attr("stroke-width", (l) => l.y === "c" ? 1.3 : 0.7);
const node = gN.selectAll("circle").data(nodes).join("circle").attr("r", (d) => 4.5 + d.s / 14).attr("fill", (d) => nshade(d.s)).attr("stroke", (d) => d.s >= 60 ? "#ffffff" : "#ffffff55").attr("stroke-width", (d) => d.s >= 60 ? 1.4 : 0.8)
  .attr("filter", (d) => d.s >= 60 ? "url(#glow)" : null)
  .style("cursor", "pointer").on("click", (e, d) => { e.stopPropagation(); showAccount(d); });
const nlab = gN.selectAll("text.nl").data(nodes.filter((d) => d.s >= 60)).join("text").attr("class", "nl").attr("text-anchor", "middle")
  .attr("font-size", 9).attr("font-weight", 800).attr("font-family", "Inter").attr("fill", "#ffffff").attr("paint-order", "stroke").attr("stroke", "#03306a").attr("stroke-width", 3)
  .style("pointer-events", "none").text((d) => Math.round(d.s) + "%");
d3.forceSimulation(nodes).force("link", d3.forceLink(links).distance((l) => l.y === "c" ? 12 : 22).strength((l) => l.source.k !== l.target.k ? 0 : l.y === "c" ? 0.2 : 0.02))
  .force("charge", d3.forceManyBody().strength(-10)).force("x", d3.forceX((d) => cx(d.k)).strength(0.2)).force("y", d3.forceY((d) => cy(d.k)).strength(0.2))
  .force("collide", d3.forceCollide((d) => 5.5 + d.s / 14)).on("tick", tick).on("end", fit);

const ICO = {
  cross: '<svg viewBox="0 0 24 24" width="100%" height="100%"><rect x="10.5" y="3" width="3" height="18" rx="1" fill="#04408b"/><rect x="6" y="7" width="12" height="2.6" rx="1" fill="#04408b"/><rect x="7.5" y="13.5" width="9" height="2.2" rx="1" transform="rotate(-14 12 14.6)" fill="#04408b"/></svg>',
  warn: '<svg viewBox="0 0 24 24" width="100%" height="100%"><path d="M12 3 22 20H2Z" fill="#dc2626"/><rect x="11" y="9" width="2" height="6" fill="#fff"/><rect x="11" y="16.5" width="2" height="2" fill="#fff"/></svg>',
  target: '<svg viewBox="0 0 24 24" width="100%" height="100%"><circle cx="12" cy="12" r="9" fill="none" stroke="#7c2d12" stroke-width="2.4"/><circle cx="12" cy="12" r="4.5" fill="none" stroke="#7c2d12" stroke-width="2.4"/><circle cx="12" cy="12" r="1.6" fill="#7c2d12"/></svg>',
  nostate: '<svg viewBox="0 0 24 24" width="100%" height="100%"><rect x="4" y="5" width="16" height="13" rx="2" fill="#04408b"/><path d="M9 9h6v5H9z" fill="#d2ab67"/><path d="M3 21 21 3" stroke="#dc2626" stroke-width="2.6" stroke-linecap="round"/></svg>',
  scale: '<svg viewBox="0 0 24 24" width="100%" height="100%"><rect x="11" y="4" width="2" height="15" fill="#374151"/><rect x="7" y="19" width="10" height="2" rx="1" fill="#374151"/><path d="M4 7h16" stroke="#374151" stroke-width="2"/><path d="M4 7 1.5 13h5Z M20 7l-2.5 6h5Z" fill="#9ca3af"/></svg>'};
const FLAG = (cc) => `<img src="https://flagcdn.com/h40/${cc}.png" style="width:100%;height:100%;object-fit:cover;display:block">`;
const EMBLEM = (size) => `<img src="kosovo-emblem.png" alt="Republika e Kosovës" title="Republika e Kosovës" style="height:${size}px;width:auto;display:block;flex:none;filter:drop-shadow(0 3px 8px rgba(0,0,0,.35))">`;
const badge = (name, size = 30) => name === "Kosovo independence denial" ? EMBLEM(size) : "";
function tick() {
  link.attr("x1", (l) => l.source.x).attr("y1", (l) => l.source.y).attr("x2", (l) => l.target.x).attr("y2", (l) => l.target.y);
  node.attr("cx", (d) => d.x).attr("cy", (d) => d.y);
  nlab.attr("x", (d) => d.x).attr("y", (d) => d.y - (4.5 + d.s / 14) - 3);
  const g = d3.group(nodes, (d) => d.k);
  gH.selectAll("path").data(D.pushes).join("path").attr("fill", "#ffffff08").attr("stroke", "#d2ab6766").attr("stroke-width", 1.2)
    .attr("d", (p, k) => { const ps = (g.get(k) || []).flatMap((d) => [[d.x - 12, d.y - 12], [d.x + 12, d.y - 12], [d.x - 12, d.y + 12], [d.x + 12, d.y + 12]]); const h = ps.length > 2 && d3.polygonHull(ps); return h ? "M" + h.join("L") + "Z" : null; });
  gT.selectAll("g").data(D.pushes).join((en) => { const t = en.append("g");
      t.filter((p) => badge(p.name)).append("foreignObject").attr("x", -34).attr("y", -96).attr("width", 68).attr("height", 76).html((p) => `<div xmlns="http://www.w3.org/1999/xhtml" style="width:68px;height:76px;display:grid;place-items:center">${badge(p.name, 64)}</div>`);
      t.append("text").attr("class", "hlabel"); t.append("text").attr("class", "hsub").attr("dy", 15); return t; })
    .attr("transform", (p, k) => { const ps = g.get(k) || []; return ps.length ? `translate(${d3.mean(ps, (d) => d.x)},${d3.min(ps, (d) => d.y) - 24})` : "translate(-999,-999)"; })
    .call((t) => { t.select(".hlabel").attr("text-anchor", "middle").text((p, k) => `#${k + 1} ${p.name}`);
      t.select(".hsub").attr("text-anchor", "middle").text((p) => `${p.accounts} accounts · ${p.copy_accounts} copy-paste`); });
}
function fit() { const b = root.node().getBBox(); const k = Math.min(W() / (b.width + 80), H() / (b.height + 80), 1.4);
  svg.transition().duration(700).call(zoom.transform, d3.zoomIdentity.translate(W() / 2 - k * (b.x + b.width / 2), H() / 2 - k * (b.y + b.height / 2)).scale(k)); }
const pct = (x) => Math.round(100 * x) + "%";
document.getElementById("side").innerHTML = `<div class="note"><b style="color:#111827">How to read this.</b> Each card is a narrative pushed in comments under Kosovo/Serbia videos, ranked by how many accounts push it. <b>Copy-paste</b> = accounts that posted the identical text as other accounts. <b>Bursts</b> = same text from different accounts within 10 minutes. These are coordination signals for an analyst; the page does not attribute them to any state, and it does not show a bot % because the bot model is validated on the benchmark, not yet on YouTube.</div>` +
  D.pushes.map((p, k) => `<div class="card" data-k="${k}"><div style="display:flex;align-items:center;gap:10px">${badge(p.name, 46)}<span class="rank">${k + 1}</span><span class="nm" style="font-size:15px">${p.name}</span></div>
  <div class="stats">${[[p.accounts, "accounts", 0], [p.comments, "comments", 0], [p.copy_accounts, "copy-paste", 1], [p.burst_accounts, "in bursts", 1], [pct(p.new_share), "brand-new channels", 0], [p.videos, "videos", 0]]
    .map(([v, l, hot]) => `<div><b style="color:${hot && +String(v).replace(/\D/g, "") > 0 ? "#b91c1c" : "#04408b"}">${typeof v === "number" ? v.toLocaleString() : v}</b><span>${l}</span></div>`).join("")}</div>
  ${p.examples.map(([t, c]) => `<div class="q">${t.replace(/</g, "&lt;")}<span>${c} account${c > 1 ? "s" : ""}</span></div>`).join("")}
  <div class="tg">Aimed at: ${p.targets.map(([t, c]) => `${t.replace(/</g, "&lt;")} (${c})`).join(" · ")}</div>
  <div class="tg">Languages: ${Object.entries(p.langs).map(([l, c]) => `${l} ${c}`).join(" · ")}</div><div class="more">Click for examples and targeted videos</div></div>`).join("");

svg.on("click", () => goHome());


(() => {
  const B = D.bench, fmt = (x) => (x ?? 0).toLocaleString();
  document.getElementById("numTiles").innerHTML = [
    [fmt(B.train_posts), "posts the model learned from", `${fmt(B.train_accounts)} human-labelled accounts`],
    [fmt(B.test_posts), "posts in the blind test", `${fmt(B.n)} accounts it had never seen`],
    [fmt(S.comments), "real Kosovo/Serbia comments analysed", `${fmt(S.accounts)} accounts · ${S.videos} videos`],
    [`${fmt(B.n70)}`, "bots found at 70%+ confidence", `${B.true70}% of them truly bots`]]
    .map(([n, l, d], i) => `<div class="tile${i === 3 ? " dark" : ""}"><div class="n">${n}</div><div class="l">${l}</div><div class="d">${d}</div></div>`).join("");
  document.getElementById("tiers").innerHTML = `<div style="display:grid;grid-template-columns:150px 90px 1fr 70px;gap:8px 12px;align-items:center;font-size:13px">
    <div class="d" style="margin:0">Model says</div><div class="d" style="margin:0">Accounts</div><div class="d" style="margin:0">Truly bots (trust)</div><div></div>` +
    B.tiers.map((t) => `<div style="font-weight:650">${t.name}</div><div style="font:600 13px 'JetBrains Mono'">${t.n.toLocaleString()}</div>
     <div style="height:14px;background:#f3f4f6;border-radius:4px;overflow:hidden"><div style="height:100%;width:${t.true ?? 0}%;background:${shade(t.true ?? 0)}"></div></div>
     <div style="font:600 13px 'JetBrains Mono';text-align:right">${t.true == null ? "–" : t.true.toFixed(1) + "%"}</div>`).join("") + `</div>`;
  document.getElementById("sources").innerHTML = [
    ["Cresci-2017 bot benchmark", "Indiana University Bot Repository. 14,368 human-labelled accounts: genuine users plus fake followers, social spambots and traditional spambots. Training and blind test."],
    ["YouTube Data API v3", "Official API, public comments only. 69 Kosovo/Serbia videos, 10,275 comments, authors pseudonymised before storage."],
    ["Account-profile network", "Our second model (5 public profile counts), trained on 11 Bot Repository datasets with 3 kept fully separate for testing; live at /api/bot/account."],
    ["Narrative lexicon", "Our Albanian, Serbian (Latin + Cyrillic) and English patterns for the motive clades on this page."]]
    .map(([t, d]) => `<div><div style="font-weight:700;font-size:13.5px">${t}</div><div class="d">${d}</div></div>`).join("");
})();
const C = D.clades, mx = d3.max(C.motives, (m) => m.pct_motive) || 1;
document.getElementById("mlead").innerHTML = `${C.with_motive.toLocaleString()} of ${C.total.toLocaleString()} real comments (${(100 * C.with_motive / C.total).toFixed(1)}%) carry at least one motive. Each clade is a fixed definition, measured in the data: if a motive barely appears, it shows as barely there.`;
document.getElementById("mbars").innerHTML = C.motives.map((m) => {
  const w = 100 * m.pct_motive / mx, cw = m.comments ? w * m.coordinated / m.comments : 0;
  return `<div style="display:grid;grid-template-columns:190px 1fr 92px;gap:10px;align-items:center;margin:9px 0" title="${m.example.replace(/"/g, "&quot;")}">
   <div style="font-size:12.5px;font-weight:600">${m.name}</div>
   <div style="height:16px;background:#f3f4f6;border-radius:4px;position:relative;overflow:hidden"><div style="position:absolute;inset:0 auto 0 0;width:${w}%;background:${m.color};opacity:.35"></div><div style="position:absolute;inset:0 auto 0 0;width:${cw}%;background:${m.color}"></div></div>
   <div style="font:600 12.5px 'JetBrains Mono';text-align:right">${m.pct_motive.toFixed(1)}%<span style="color:#9ca3af;font-weight:500"> · ${m.comments}</span></div></div>`; }).join("");
(() => {
  const el = d3.select("#mtime"), Wt = el.node().clientWidth || 480, Ht = 300, mL = 34, mB = 26, top = C.motives.filter((m) => m.comments).slice(0, 6);
  const rows = C.days.map((d, i) => Object.fromEntries([["day", d], ...top.map((m) => [m.name, m.daily[i]])]));
  const st = d3.stack().keys(top.map((m) => m.name))(rows), x = d3.scaleBand().domain(C.days).range([mL, Wt - 6]).padding(0.15);
  const y = d3.scaleLinear().domain([0, d3.max(st, (l) => d3.max(l, (v) => v[1])) || 1]).nice().range([Ht - mB, 8]);
  el.append("g").attr("transform", `translate(${mL},0)`).call(d3.axisLeft(y).ticks(5).tickSize(-(Wt - mL - 6))).call((g) => g.selectAll("line").attr("stroke", "#eef0f3")).call((g) => g.select(".domain").remove()).call((g) => g.selectAll("text").attr("fill", "#6b7280").attr("font-size", 10));
  el.append("g").selectAll("g").data(st).join("g").attr("fill", (l) => top.find((m) => m.name === l.key).color)
    .selectAll("rect").data((l) => l).join("rect").attr("x", (v) => x(v.data.day)).attr("width", x.bandwidth()).attr("y", (v) => y(v[1])).attr("height", (v) => y(v[0]) - y(v[1])).attr("rx", 1.5);
  const ticks = C.days.filter((d, i) => i % Math.ceil(C.days.length / 6) === 0);
  el.append("g").attr("transform", `translate(0,${Ht - mB})`).call(d3.axisBottom(x).tickValues(ticks).tickFormat((d) => d.slice(5))).call((g) => g.select(".domain").attr("stroke", "#e5e7eb")).call((g) => g.selectAll("text").attr("fill", "#6b7280").attr("font-size", 10));
  document.getElementById("mleg").innerHTML = top.map((m) => `<span style="display:inline-flex;align-items:center;gap:5px;margin:6px 12px 0 0"><i style="width:10px;height:10px;border-radius:3px;background:${m.color};display:inline-block"></i>${m.name}</span>`).join("");
})();

function avatar(seed, size = 34) {           // identicon from the pseudonym: deterministic, not a real photo
  let h = 2166136261; for (const ch of seed) { h ^= ch.charCodeAt(0); h = Math.imul(h, 16777619) >>> 0; }
  const hue = h % 360, fg = `hsl(${hue} 55% 46%)`, bg = `hsl(${hue} 60% 94%)`; let cells = "";
  for (let y = 0; y < 5; y++) for (let x = 0; x < 3; x++) { if ((h >> (y * 3 + x)) & 1) {
    cells += `<rect x="${x}" y="${y}" width="1" height="1" fill="${fg}"/>` + (x < 2 ? `<rect x="${4 - x}" y="${y}" width="1" height="1" fill="${fg}"/>` : ""); } }
  return `<svg viewBox="-1 -1 7 7" width="${size}" height="${size}" style="border-radius:50%;background:${bg};flex:none">${cells}</svg>`;
}
const ageTxt = (d) => d == null ? "age unknown" : d < 31 ? `${d} days old` : d < 365 ? `${Math.round(d / 30.4)} months old` : `${(d / 365).toFixed(1)} years old`;
const vol = (cv) => cv == null ? ["not enough days", "#6b7280", "#f3f4f6"] : cv < 0.35 ? ["stable", "#065f46", "#d1fae5"] : cv < 0.7 ? ["moderate swing", "#92400e", "#fef3c7"] : ["volatile", "#991b1b", "#fee2e2"];
document.getElementById("mcards").innerHTML = D.clades.motives.filter((m) => m.comments).map((m) => {
  const [vl, vc, vb] = vol(m.cv), pr = m.profile;
  return `<div class="tile" style="padding:18px 20px">
   <div style="display:flex;align-items:center;gap:10px;flex-wrap:wrap"><span style="width:12px;height:12px;border-radius:3px;background:${m.color}"></span>
    <b style="font-size:15.5px">${m.name}</b><span style="margin-left:auto;font:700 18px Inter">${m.pct_motive.toFixed(1)}%</span></div>
   <div style="display:flex;gap:8px;flex-wrap:wrap;margin:10px 0 12px;font-size:12px">
    <span class="chipx">range <b>${m.ci[0]}–${m.ci[1]}%</b></span>
    <span class="chipx" style="background:${vb};color:${vc}">day-to-day: <b>${vl}</b>${m.cv != null ? ` (${Math.round(100 * m.cv)}%)` : ""}</span>
    <span class="chipx"><b>${m.comments}</b> comments · <b>${m.accounts}</b> accounts</span><span class="chipx"><b>${m.coordinated}</b> copy-pasted</span></div>
   <div style="display:grid;grid-template-columns:repeat(4,1fr);gap:8px;margin-bottom:12px">
    ${[[Math.round(100 * pr.new180) + "%", "channels < 6 months"], [Math.round(100 * pr.new365) + "%", "channels < 1 year"],
       [pr.median_age_y == null ? "–" : pr.median_age_y + " y", "median channel age"], [pr.zero_fol == null ? "–" : Math.round(100 * pr.zero_fol) + "%", "0 subscribers"]]
      .map(([v, l]) => `<div style="background:#f9fafb;border:1px solid #eef0f3;border-radius:9px;padding:8px 10px"><div style="font:700 17px Inter">${v}</div><div style="font-size:11px;color:#6b7280">${l}</div></div>`).join("")}</div>
   ${m.evidence.map((e) => `<div style="display:flex;gap:10px;padding:9px 0;border-top:1px solid #f3f4f6">${avatar(e.who)}
     <div style="min-width:0;flex:1"><div style="font-size:11.5px;color:#6b7280;display:flex;gap:8px;flex-wrap:wrap"><b style="color:#111827;font-family:'JetBrains Mono'">${e.who}</b>
       <span style="color:${e.age != null && e.age < 180 ? "#b91c1c" : "#6b7280"}">channel ${ageTxt(e.age)}</span><span>${e.fol == null ? "subscribers hidden" : e.fol.toLocaleString() + " subscribers"}</span><span>${e.day}</span></div>
     <div style="font-size:13.5px;margin-top:3px;overflow-wrap:anywhere">${e.text.replace(/</g, "&lt;")}</div>
     <div style="font-size:11.5px;color:#6b7280;margin-top:3px">${e.copies > 1 ? `<b style="color:#b91c1c">posted word-for-word by ${e.copies} accounts</b> · ` : ""}under “${e.video.replace(/</g, "&lt;")}”</div></div></div>`).join("")}
  </div>`; }).join("");

const sideHome = document.getElementById("side").innerHTML;
function bindCards() {
  document.querySelectorAll(".card").forEach((el) => el.onclick = () => { const k = +el.dataset.k, open = !el.classList.contains("on");
    document.querySelectorAll(".card").forEach((c) => c.classList.toggle("on", open && c === el));
    if (!open) { node.attr("opacity", 1); link.attr("stroke-opacity", LOP); return; }
    node.attr("opacity", (d) => d.k === k ? 1 : 0.12); link.attr("stroke-opacity", (l) => l.source.k === k ? 0.5 : 0.03); });
}
function showAccount(d) {
  const nb = new Set([d]); links.forEach((l) => { if (l.source === d) nb.add(l.target); if (l.target === d) nb.add(l.source); });
  node.attr("opacity", (n) => nb.has(n) ? 1 : 0.15).attr("stroke", (n) => n === d ? "#d2ab67" : "#ffffff").attr("stroke-width", (n) => n === d ? 3 : 1);
  link.attr("stroke-opacity", (l) => l.source === d || l.target === d ? 0.7 : 0.03);
  const COL = { "Word-for-word copies": "#991b1b", "Burst with other accounts": "#dc2626", "Fresh channel": "#d2ab67", "No audience": "#9ca3af", "Repeated across videos": "#04408b", "Several narratives": "#6b21a8" };
  const level = d.s >= 70 ? ["HIGH coordination", "#991b1b", "#fee2e2"] : d.s >= 40 ? ["MEDIUM coordination", "#92400e", "#fef3c7"] : d.s > 0 ? ["LOW coordination", "#065f46", "#d1fae5"] : ["NO coordination signal", "#374151", "#f3f4f6"];
  const ageTxt2 = d.age == null ? "unknown" : d.age < 31 ? `${d.age} days` : d.age < 365 ? `${Math.round(d.age / 30.4)} months` : `${(d.age / 365).toFixed(1)} years`;
  document.getElementById("side").innerHTML = `<a href="#" id="home" style="font:600 12.5px Inter;color:#04408b;text-decoration:none">← all pushes</a>
   <div style="display:flex;gap:12px;align-items:center;margin:14px 0 6px">${avatar(d.id, 46)}<div><div style="font:700 15px 'JetBrains Mono'">${d.id}</div>
   <div class="muted" style="font-size:12.5px;color:#6b7280">pseudonymised YouTube author · ${d.n} comment${d.n > 1 ? "s" : ""} in the data</div></div></div>
   <div style="margin:16px 0 6px;display:flex;align-items:flex-end;gap:12px"><div style="font:800 84px/0.85 Inter;letter-spacing:-.05em;color:${shade(Math.max(d.s, 45))}">${d.s.toFixed(1)}%</div>
    <span style="font:700 11px Inter;letter-spacing:.06em;padding:5px 9px;border-radius:7px;background:${level[2]};color:${level[1]};margin-bottom:6px">${level[0]}</span></div>
   <div style="font-size:12.5px;color:#6b7280;margin-bottom:10px">coordination likelihood: the sum of the weighted criteria below</div>
   <div style="display:flex;height:16px;border-radius:5px;overflow:hidden;background:#f3f4f6">${d.comps.map(([l, v]) => `<div title="${l} +${v}" style="width:${v}%;background:${COL[l] || "#6b7280"}"></div>`).join("")}</div>
   <div style="margin:10px 0 4px">${d.comps.length ? d.comps.map(([l, v, why]) => `<div style="display:grid;grid-template-columns:12px 1fr auto;gap:8px;align-items:start;font-size:13px;margin:7px 0">
     <span style="width:10px;height:10px;border-radius:3px;margin-top:4px;background:${COL[l] || "#6b7280"}"></span><div><b>${l}</b><div style="font-size:11.5px;color:#6b7280">${why}</div></div>
     <b style="font-family:'JetBrains Mono';color:#991b1b">+${v}</b></div>`).join("") + `<div style="display:flex;justify-content:space-between;border-top:1px solid #e5e7eb;padding-top:7px;font-weight:700;font-size:13px"><span>Total</span><span style="font-family:'JetBrains Mono'">${d.s.toFixed(1)}%</span></div>`
     : '<div style="font-size:13px;color:#6b7280">None of the coordination criteria apply to this account.</div>'}</div>
   <div style="display:grid;grid-template-columns:repeat(3,1fr);gap:8px;margin:12px 0">
    ${[[d.n, "comments in the data"], [ageTxt2, "channel age"], [d.fol == null ? "hidden" : d.fol.toLocaleString(), "subscribers"]].map(([v, l]) => `<div style="background:#f9fafb;border:1px solid #eef0f3;border-radius:9px;padding:9px 10px"><div style="font:700 18px Inter">${v}</div><div style="font-size:11px;color:#6b7280">${l}</div></div>`).join("")}</div>
   <div style="font-size:12px;color:#6b7280;margin-top:4px">${d.partners ? `Shares a text with <b style="color:#111827">${d.partners}</b> other account${d.partners > 1 ? "s" : ""}.` : "No other account posted its texts."}</div>
   <div style="font:600 11px Inter;text-transform:uppercase;letter-spacing:.07em;color:#6b7280;margin:16px 0 8px">Pushes it is part of</div>
   <div style="display:flex;flex-wrap:wrap;gap:6px">${d.pk.map((k) => `<span class="chipx">#${k + 1} ${D.pushes[k].name}</span>`).join("")}</div>
   <div style="font:600 11px Inter;text-transform:uppercase;letter-spacing:.07em;color:#6b7280;margin:16px 0 4px">Its comments</div>
   ${d.cm.map(([t, when, vid, c]) => `<div style="border-top:1px solid #f3f4f6;padding:9px 0"><div style="font-size:11.5px;color:#6b7280">${when} · under “${vid.replace(/</g, "&lt;")}”</div>
     <div style="font-size:13.5px;margin-top:3px;overflow-wrap:anywhere">${t.replace(/</g, "&lt;")}</div>${c > 1 ? `<div style="font-size:11.5px;color:#b91c1c;font-weight:600;margin-top:2px">posted word-for-word by ${c} accounts</div>` : ""}</div>`).join("")}
   <p style="font-size:11.5px;color:#6b7280;margin-top:14px">Weights: copies up to 40 (grows with the number of copying accounts), burst 25, fresh channel up to 15, repeated across videos up to 10, no audience 5, several narratives 5. Coordination is a lead for an analyst, not proof that the account is a bot: the bot model is validated on the benchmark, not yet on YouTube.</p>`;
  document.getElementById("home").onclick = (e) => { e.preventDefault(); goHome(); };
}
function goHome() { document.getElementById("side").innerHTML = sideHome; bindCards(); node.attr("opacity", 1).attr("stroke", (d) => d.s >= 60 ? "#ffffff" : "#ffffff55").attr("stroke-width", (d) => d.s >= 60 ? 1.4 : 0.8); link.attr("stroke-opacity", LOP); }
bindCards();

(() => {  // highlight the section in view; show "top" once past the map
  const links = [...document.querySelectorAll("#secnav a")], byId = new Map(links.map((a) => [a.getAttribute("href").slice(1), a]));
  const io = new IntersectionObserver((es) => es.forEach((e) => { if (e.isIntersecting) { links.forEach((a) => a.classList.remove("on")); byId.get(e.target.id)?.classList.add("on"); } }),
    { rootMargin: "-45% 0px -50% 0px" });
  byId.forEach((a, id) => { const el = document.getElementById(id); if (el) io.observe(el); });
  const tt = document.getElementById("totop");
  addEventListener("scroll", () => tt.classList.toggle("show", scrollY > innerHeight * 0.8), { passive: true });
})();
const B = D.bench, K = D.kosovo, P = (x, d = 1) => (100 * x).toFixed(d) + "%";
(() => {
  const F = (cc, h = 26) => `<img src="https://flagcdn.com/h40/${cc}.png" alt="${cc.toUpperCase()}" style="height:${h}px;border-radius:4px;box-shadow:0 0 0 1px #0b0f1922">`;
  const TH = [
    { id: "xk", name: "Kosovo – Serbia", sub: "proven here", flags: ["xk", "rs"], col: "#04408b",
      text: `${D.kosovo.groups} coordinated pro-Serbian copy groups and ${D.pushes.length} narrative pushes found in ${D.stats.comments.toLocaleString()} real comments.`, sig: ["copy", "sync", "fresh", "motive"] },
    { id: "ua", name: "Ukraine – Russia", sub: "“Doppelganger” type", flags: ["ua", "ru"], col: "#0057b7",
      text: "Cloned-media and bot-amplification networks.", sig: ["links", "copy", "paid", "cross"] },
    { id: "tw", name: "Taiwan – China", sub: "“Spamouflage” style", flags: ["tw", "cn"], col: "#c81d25",
      text: "Mass accounts pushing the same lines across platforms.", sig: ["fresh", "copy", "cross", "sync"] },
    { id: "md", name: "Moldova, Romania", sub: "referendums & elections", flags: ["md", "ro"], col: "#d9a400",
      text: "Paid and coordinated accounts around referendums and elections.", sig: ["paid", "sync", "fresh", "cross"] },
    { id: "wb", name: "Western Balkans", sub: "BiH · ME · MK · RS", flags: ["ba", "me", "mk", "rs"], col: "#6b21a8",
      text: "Bosnia and Herzegovina, Montenegro, North Macedonia, Serbia.", sig: ["copy", "motive", "fresh", "sync"] }];
  const SG = [{ id: "copy", name: "Copy-paste groups", live: 1 }, { id: "sync", name: "Synchronised bursts", live: 1 }, { id: "fresh", name: "Fresh-account waves", live: 1 },
    { id: "motive", name: "Motive clades", live: 1 }, { id: "links", name: "Shared / cloned links", live: 1 }, { id: "paid", name: "Paid amplification", live: 0 }, { id: "cross", name: "Cross-platform reposting", live: 0 }];
  document.getElementById("thCards").innerHTML = TH.map((t) => `<div class="tile" data-th="${t.id}" style="padding:20px 22px;border-top:4px solid ${t.col};cursor:default">
    <div style="display:flex;gap:6px;align-items:center;margin-bottom:12px">${t.flags.map((f, i) => F(f) + (i === 0 && t.flags.length === 2 ? '<span style="font:700 14px Inter;color:#9ca3af;margin:0 4px">vs</span>' : "")).join("")}</div>
    <div style="font:800 19px Inter;letter-spacing:-.015em">${t.name}</div><div style="font:600 12px Inter;color:${t.col};margin:2px 0 8px;text-transform:uppercase;letter-spacing:.05em">${t.sub}</div>
    <div class="d" style="font-size:13.5px;color:#374151">${t.text}</div>
    <div style="display:flex;flex-wrap:wrap;gap:6px;margin-top:12px">${t.sig.map((k) => `<span class="chipx" style="font-size:11.5px">${SG.find((x) => x.id === k).name}</span>`).join("")}</div></div>`).join("");
  const svg = d3.select("#thGraph"), W = svg.node().clientWidth || 1100, H = 460, lx = 250, rx = W - 270;
  const ty = (i) => 46 + i * ((H - 92) / (TH.length - 1)), sy = (i) => 30 + i * ((H - 60) / (SG.length - 1));
  const edges = TH.flatMap((t, i) => t.sig.map((k) => ({ t, i, j: SG.findIndex((x) => x.id === k) })));
  const path = (e) => `M${lx} ${ty(e.i)} C ${(lx + rx) / 2} ${ty(e.i)}, ${(lx + rx) / 2} ${sy(e.j)}, ${rx} ${sy(e.j)}`;
  const ed = svg.append("g").selectAll("path").data(edges).join("path").attr("d", path).attr("fill", "none").attr("stroke", (e) => e.t.col)
    .attr("stroke-width", (e) => e.t.id === "xk" ? 3.2 : 2).attr("stroke-opacity", 0.5).attr("stroke-dasharray", (e) => SG[e.j].live ? null : "5 5");
  const tg = svg.append("g").selectAll("g").data(TH).join("g").attr("transform", (t, i) => `translate(0,${ty(i)})`).style("cursor", "pointer");
  tg.append("foreignObject").attr("x", 0).attr("y", -15).attr("width", 92).attr("height", 30)
    .html((t) => `<div style="display:flex;gap:3px">${t.flags.map((f) => F(f, 22)).join("")}</div>`);
  tg.append("text").attr("x", lx - 14).attr("y", 5).attr("text-anchor", "end").attr("font-size", 14).attr("font-weight", 700).attr("fill", "#111827").text((t) => t.name);
  tg.append("circle").attr("cx", lx).attr("r", (t) => t.id === "xk" ? 9 : 7).attr("fill", (t) => t.col).attr("stroke", "#fff").attr("stroke-width", 2.5);
  const sgG = svg.append("g").selectAll("g").data(SG).join("g").attr("transform", (s, j) => `translate(${rx},${sy(j)})`);
  sgG.append("circle").attr("r", 8).attr("fill", (s) => s.live ? "#0b0f19" : "#fff").attr("stroke", "#0b0f19").attr("stroke-width", 2);
  sgG.append("text").attr("x", 18).attr("y", 5).attr("font-size", 14).attr("font-weight", 650).attr("fill", "#111827").text((s) => s.name);
  sgG.append("text").attr("x", 18).attr("y", 21).attr("font-size", 11).attr("font-weight", 700).attr("fill", (s) => s.live ? "#065f46" : "#92400e").text((s) => s.live ? "LIVE" : "NEXT");
  const hi = (id) => { ed.attr("stroke-opacity", (e) => !id || e.t.id === id ? 0.75 : 0.06);
    document.querySelectorAll("[data-th]").forEach((c) => c.style.boxShadow = id && c.dataset.th === id ? "0 6px 20px #0b0f191f" : ""); };
  tg.on("mouseenter", (e, t) => hi(t.id)).on("mouseleave", () => hi(null));
  document.querySelectorAll("[data-th]").forEach((c) => { c.onmouseenter = () => hi(c.dataset.th); c.onmouseleave = () => hi(null); });
})();

document.getElementById("farmTiles").innerHTML = (B.farms != null ? `
 <div class="tile dark"><span class="tag ok">Confirmed · ground truth</span><div class="n">${B.farms}</div><div class="l">bot farms confirmed</div>
  <div class="d">Groups of 5+ accounts that copy-paste or post in sync, at least 90% of them truly bots by human labels. Sizes: ${B.farm_sizes.join(", ")} accounts.</div></div>
 <div class="tile"><span class="tag ok">Confirmed · ground truth</span><div class="n">${B.farm_caught.toLocaleString()} / ${B.farm_true_bots.toLocaleString()}</div><div class="l">farm bots caught</div>
  <div class="d">Inside those farms the model flagged ${P(B.farm_caught / B.farm_true_bots)} of the real bots, on accounts it never saw in training.</div></div>` : "") + `
 <div class="tile"><span class="tag warn">Detected · Kosovo YouTube</span><div class="n">${K.groups}</div><div class="l">coordinated pro-Serbian groups</div>
  <div class="d">The same pro-Serbian text posted by 3+ different accounts. ${K.group_accounts} accounts involved; the largest group has ${K.largest}. ${K.bursts} accounts posted identical text within 10 minutes of each other.</div></div>
 <div class="tile"><span class="tag warn">Next: confirm</span><div class="n">2</div><div class="l">analysts to confirm the Kosovo groups</div>
  <div class="d">Two people label a blind sample; the tool then reports a Kosovo accuracy with its margin. Until then these are leads, not verdicts.</div></div>`;
document.getElementById("plain").innerHTML = `
 <div class="tile"><b class="k">How often is it right?</b><div class="n">${(B.correct ?? 0).toLocaleString()} / ${(B.n ?? 0).toLocaleString()}</div>
  <div class="d">accounts judged correctly. It wrongly called <b>${B.fp}</b> real people bots and missed <b>${B.fn}</b> bots. Score (F1): <b>${B.f1.toFixed(3)}</b>, where 1.000 is perfect.</div></div>
 <div class="tile"><b class="k">Margin for mistake</b><div class="n">${(100 * B.ci[0]).toFixed(1)}–${(100 * B.ci[1]).toFixed(1)}%</div>
  <div class="d">We re-tested on 1,000 random re-draws of the test accounts. In 95% of them the score stayed inside this range. A narrow range means a stable result, not luck.</div></div>
 <div class="tile"><b class="k">Does "80% bot" mean 80%?</b><div class="n">± ${(100 * B.ece).toFixed(1)} pts</div>
  <div class="d">That is the "calibration error". When the model says 80%, about 80 of 100 such accounts really are bots. On average it is off by less than one percentage point.</div></div>
 <div class="tile"><b class="k">New kinds of bots?</b><div class="n">${(100 * B.unseen[0]).toFixed(1)}–${(100 * B.unseen[1]).toFixed(1)}%</div>
  <div class="d">We hid one whole bot type during training and tested on it, five times over. It still scored this well on bots it had never seen. This is the honest number.</div></div>
`;
(document.getElementById("secure") || {}).innerHTML = `
 <div class="tile"><b class="k">People's privacy</b><div class="d" style="font-size:14px;color:#111827"><ul style="padding-left:18px;margin:0">
  <li>Every author is replaced by a one-way keyed code before storage; names are dropped.</li><li>Pages like this one never contain real account IDs.</li><li>Only public comments, through the official YouTube API.</li></ul></div></div>
 <div class="tile"><b class="k">Access</b><div class="d" style="font-size:14px;color:#111827"><ul style="padding-left:18px;margin:0">
  <li>Stored data and analyst tools need a secret analyst key; keys never live in the code.</li><li>Public endpoints only compute on data you send; they read nothing stored.</li><li>Everything runs on our own model: no data is sent to outside AI services.</li></ul></div></div>
 <div class="tile"><b class="k">Decisions</b><div class="d" style="font-size:14px;color:#111827"><ul style="padding-left:18px;margin:0">
  <li>The tool says "bot or not" with a probability and its reasons; a human decides what happens next.</li><li>It never names a state, group or person behind an account.</li>
  <li>Known gaps, said openly: no rate limiting on public endpoints yet; the free hosting sleeps when idle.</li></ul></div></div>`;

</script></body></html>
"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(HERE / "data" / "youtube_10k.db"))
    ap.add_argument("--out", default=str(HERE.parent / "frontend" / "kosovo.html"))
    a = ap.parse_args()
    print(build(Path(a.db), Path(a.out)))


if __name__ == "__main__":
    main()
