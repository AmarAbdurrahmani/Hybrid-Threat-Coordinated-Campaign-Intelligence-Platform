"""Build site/data/{graph,summary}.json from the real collection DB and the trained detector model.

  python site/build.py --db detector/data/youtube_10k.db [--collect-log run.json]

Publishes no comment text, display names or channel IDs: accounts appear only as keyed-hash IDs.
"""
from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
import time
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from detector import datasets, model as M  # noqa: E402
sys.path.insert(0, str(Path(__file__).resolve().parent))
from themes import THEMES, tag as tag_theme  # noqa: E402

COORD = ("COPY", "SYNC", "LINK", "REPLY", "CO_THREAD")
SAFE_REASON = re.compile(r"^[^\"“”«»']*$")  # drop any reason that quotes comment text


def report_weights(path: Path, top: int = 100) -> list[dict]:
    rows, on = [], False
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith("## Learned indicator weights"):
            on = True; continue
        if on and line.startswith("## "):
            break
        if on and line.startswith("| ") and not line.startswith("| Indicator") and not line.startswith("|---"):
            c = [x.strip() for x in line.strip("|").split("|")]
            rows.append({"name": c[0], "group": c[1], "pct": float(c[2].rstrip("%")), "pushes_bot_pct": float(c[3].split("%")[0])})
    return sorted(rows, key=lambda r: -r["pct"])[:top]


ICON = {"contests_independence": "🗺️", "supports_independence": "🏛️", "religion": "⛪", "nato": "🛡️", "russia": "🇷🇺",
        "war": "⚔️", "albania": "🇦🇱", "eu_us": "🇪🇺", "politicians": "👤"}


def plain(reason: str) -> str | None:
    m = re.search(r"(\d+) comments in its 15-min window vs ([\d.]+) expected \(×([\d.]+)", reason)
    if m:
        return f"Posted in a burst: {m.group(1)} comments in 15 minutes, {round(float(m.group(3)))}× normal"
    m = re.search(r"Near-duplicate of (\d+) comment", reason)
    if m:
        return f"Same text as {m.group(1)} other comment{'s' if m.group(1) != '1' else ''}"
    m = re.search(r"(\d+) comments within ±2 min", reason)
    if m:
        return f"{m.group(1)} comments within 2 minutes"
    m = re.search(r"Low follower count \((\d+)\)", reason)
    if m:
        return f"Channel has only {m.group(1)} subscribers"
    return None


def showcase(best, risk, cluster_of, group_name, user_themes, threads, per_user_n, n=10) -> list[dict]:
    """The 10 most notable accounts: highest coordination risk, with at most 4 from any one group so the picture shows
    several groups. Published with a letter label only (no name, no ID); the comment is shortened to 160 characters."""
    picked, per_group = [], Counter()
    for u in sorted(risk, key=lambda x: (-risk[x], -per_user_n[x])):
        g = cluster_of.get(u, 0)
        it = best[u]
        if not it.get("c", {}).get("text") or (g and per_group[g] >= 4):
            continue
        per_group[g] += 1
        picked.append(u)
        if len(picked) == n:
            break
    out = []
    for i, u in enumerate(picked):
        it, c = best[u], best[u]["c"]
        th = [t for t, _ in user_themes[u].most_common()]
        reasons = [p for p in (plain(x) for k in ("tReasons", "nReasons", "gReasons", "lReasons") for x in it.get(k, [])) if p][:3]
        text = re.sub(r"\s+", " ", c["text"]).strip()
        g = cluster_of.get(u, 0)
        out.append({"label": f"Account {chr(65 + i)}", "risk": round(risk[u]), "group": g,
                    "group_name": group_name.get(g, "Not linked to a group").split(" · ")[0] if g else "Not linked to a group",
                    "theme": THEMES[th[0]]["short"] if th else "Other talk", "icon": ICON.get(th[0], "💬") if th else "💬",
                    "comment": text[:160] + ("…" if len(text) > 160 else ""),
                    "video": (threads.get(c["thread_id"], {}) or {}).get("title") or "", "video_id": (threads.get(c["thread_id"], {}) or {}).get("external_id"),
                    "date": datetime.fromtimestamp(c["ts"] / 1000, timezone.utc).strftime("%d %b %Y, %H:%M UTC") if c.get("ts") else "",
                    "why": reasons or ["Several weak signals together"]})
    return out


def clades(groups, risk, cluster_of, user_texts, norm_orig, user_ts, per_user_threads, threads, pair_rels, best) -> list[dict]:
    """Every coordination group (3+ linked accounts), with its main theme and what the members have in common."""
    links = defaultdict(Counter)
    for s_, d_, rel, _ in pair_rels:
        links[cluster_of.get(s_, 0)][rel] += 1
    day = lambda ms: datetime.fromtimestamp(ms / 1000, timezone.utc).strftime("%d %b %Y")
    out = []
    for g in groups:
        mem = g["members"]; n = len(mem)
        top = [(t, c) for t, c in g["themes"].most_common() if c / n >= 0.15]
        key = top[0][0] if top else "none"
        phrases = Counter()
        for u in mem:
            phrases.update(set(user_texts.get(u, [])))
        phrase, pn = phrases.most_common(1)[0] if phrases else ("", 0)
        shared = pn >= 2
        if shared:
            text = norm_orig[phrase]
        else:
            bu = max(mem, key=lambda u: risk.get(u, 0))
            text = re.sub(r"\s+", " ", (best.get(bu, {}).get("c", {}) or {}).get("text") or "").strip()
        vids = Counter()
        for u in mem:
            vids.update(per_user_threads[u].keys())
        tv = threads.get(vids.most_common(1)[0][0], {}) if vids else {}
        ts = [t for u in mem for t in user_ts.get(u, [])]
        rs = [risk.get(u, 0) for u in mem]
        out.append({"gi": g["gi"], "key": key, "theme": THEMES[key]["label"] if key != "none" else "Other copied lines", "icon": ICON.get(key, "💬"),
                    "accounts": n, "avg_risk": round(sum(rs) / n), "max_risk": round(max(rs)), "flagged": sum(1 for r in rs if r >= 35),
                    "shared": shared, "repeated_by": pn if shared else 0, "text": text[:120] + ("…" if len(text) > 120 else ""),
                    "themes": [{"label": THEMES[t]["short"], "value": round(100 * c / n)} for t, c in g["themes"].most_common(4)],
                    "copy": links[g["gi"]].get("COPY", 0), "co_thread": links[g["gi"]].get("CO_THREAD", 0), "sync": links[g["gi"]].get("SYNC", 0),
                    "videos": len(vids), "video": tv.get("title") or "", "video_id": tv.get("external_id") or "",
                    "from": day(min(ts)) if ts else "", "to": day(max(ts)) if ts else ""})
    return out


def confidence_table(path: Path) -> dict:
    """What a stated BOT % actually meant on held-out labelled accounts."""
    import pandas as pd
    if not path.exists():
        return {}
    d = pd.read_csv(path)
    bands = [(0, 10, "under 10%"), (10, 50, "10–49%"), (50, 70, "50–69%"), (70, 80, "70–79%"), (80, 90, "80–89%"), (90, 99, "90–98%"), (99, 100.01, "99–100%")]
    rows = []
    for lo, hi, lab in bands:
        s = d[(d.bot_probability >= lo) & (d.bot_probability < hi)]
        if len(s):
            rows.append({"band": lab, "accounts": int(len(s)), "bots": int(s.label.sum()), "bot_rate": round(100 * float(s.label.mean()), 1)})
    correct = int(((d.verdict == "BOT") == (d.label == 1)).sum())
    return {"rows": rows, "n": int(len(d)), "correct": correct, "accuracy": round(100 * correct / len(d), 1),
            "fp": int(((d.verdict == "BOT") & (d.label == 0)).sum()), "fn": int(((d.verdict != "BOT") & (d.label == 1)).sum())}


def tested_totals(report: Path, meta: dict) -> list[list]:
    lofo = 0
    on = False
    for line in report.read_text(encoding="utf-8").splitlines():
        on = on or line.startswith("## Leave-one-family-out")
        if on and line.startswith("| ") and line[2:3].isalpha() and not line.startswith("| Held-out"):
            lofo += int(line.strip("|").split("|")[-2])
        if on and line.startswith("## Notes"):
            break
    return [["Cresci-2017 held-out test (random split)", 2039],
            ["Cresci-2017 unseen-bot-type tests (5 runs)", lofo],
            ["Account-profile model: held-out test (Botometer sets)", 9157],
            ["Account-profile model: external sets never used in training", 515 + 62 + 1983]]


def stress_test(path: Path) -> dict:
    if not path.exists():
        return {}
    d = json.loads(path.read_text(encoding="utf-8"))
    if "profile youtube" not in str(d.get("dataset", "")):
        return {}
    p = d["protocols"]["random_split"]
    return {"dataset": d["dataset"], "f1": round(p["f1"], 3), "precision": round(p["precision"], 3), "recall": round(p["recall"], 3), "n": p["support"]["n"]}


def stage_stats(col, threads, tmeta, per_thread_n, per_user_n, best, risk, relc, levels, weights, meta) -> dict:
    """Small real-number series behind each pipeline stage, for the clickable pipeline graph."""
    def hist(values, edges, labels):
        out = [0] * len(labels)
        for v in values:
            for i in range(len(edges) - 1):
                if edges[i] <= v < edges[i + 1]:
                    out[i] += 1; break
        return [{"label": l, "value": n} for l, n in zip(labels, out)]
    top = sorted(threads, key=lambda t: -per_thread_n[t])[:8]
    flagged = [best[u] for u, s in risk.items() if s >= 35]
    avg = lambda k: round(sum(it.get(k, 0) for it in flagged) / max(1, len(flagged)), 1)
    return {
        "collect": {"series": [{"label": "candidate videos found", "value": col.get("videos_found", 0)},
                               {"label": "videos with comments used", "value": len(threads)},
                               {"label": "quota units spent", "value": col.get("quota_units", 0)}],
                    "queries": col.get("queries", [])},
        "protect": {"series": hist(list(per_user_n.values()), [1, 2, 3, 6, 11, 10**9], ["1 comment", "2", "3–5", "6–10", "11+"])},
        "store": {"series": [{"label": (threads[t]["title"] or "")[:48], "value": per_thread_n[t]} for t in top]},
        "score": {"series": hist(list(risk.values()), [0, 15, 35, 60, 101], ["0–14 organic", "15–34 low", "35–59 medium", "60+ high"]),
                  "signals": [{"label": "Timing", "value": avg("temporal")}, {"label": "Language", "value": avg("linguistic")},
                              {"label": "Talking points", "value": avg("narrative")}, {"label": "Network", "value": avg("network")}]},
        "link": {"series": [{"label": k, "value": v} for k, v in relc.most_common()]},
        "learn": {"series": [{"label": w["name"], "value": w["pct"]} for w in weights[:8]],
                  "lofo": [{"label": k.replace("_", " "), "value": round(v, 3)} for k, v in sorted(meta.get("lofo_f1", {}).items(), key=lambda kv: kv[1])]},
        "decide": {"series": [{"label": f"Level {k}", "value": levels.get(k, 0)} for k in (1, 2, 3, 4)]},
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True)
    ap.add_argument("--collect-log")
    a = ap.parse_args()
    out = Path(__file__).resolve().parent / "data"; out.mkdir(exist_ok=True)
    db = sqlite3.connect(a.db); db.row_factory = sqlite3.Row
    meta = json.loads((ROOT / "detector" / "model" / "model.json").read_text())

    threads = {r["id"]: dict(r) for r in db.execute("SELECT id, external_id, title, published FROM threads WHERE simulated=0")}
    comments = db.execute("SELECT id, thread_id, user, ts, account, text FROM comments WHERE simulated=0").fetchall()
    per_user_threads, per_user_n, per_thread_n = defaultdict(Counter), Counter(), Counter()
    user_themes, theme_comments, theme_users = defaultdict(Counter), Counter(), defaultdict(set)
    acct_meta = {}
    user_texts, user_ts, norm_orig = defaultdict(list), defaultdict(list), {}
    for c in comments:
        per_user_threads[c["user"]][c["thread_id"]] += 1; per_user_n[c["user"]] += 1; per_thread_n[c["thread_id"]] += 1
        if c["text"]:
            orig = re.sub(r"\s+", " ", c["text"]).strip(); norm = orig.casefold()
            if len(norm) >= 6:
                user_texts[c["user"]].append(norm); norm_orig.setdefault(norm, orig)
        if c["ts"]:
            user_ts[c["user"]].append(c["ts"])
        for th in tag_theme(c["text"]):
            user_themes[c["user"]][th] += 1; theme_comments[th] += 1; theme_users[th].add(c["user"])
        if c["account"] and c["user"] not in acct_meta:
            acct_meta[c["user"]] = json.loads(c["account"])

    best, tmeta = {}, {}
    for r in db.execute("SELECT thread_id, payload FROM analyses WHERE payload IS NOT NULL"):
        an = json.loads(r["payload"]); t = an["thread"]
        tmeta[r["thread_id"]] = t
        for it in an["items"]:
            u = it["c"]["user"]
            if u not in best or it["score"] > best[u]["score"]:
                best[u] = it

    # account graph exactly as the detector builds it (shared text, synchronised posting, links, replies, same threads)
    acc_df, posts_df = datasets.load_backend_db(a.db)
    acc_df = acc_df.drop_duplicates("account_id")
    X, graph = M.design(acc_df, posts_df)
    ids = list(graph.ids)
    gates = meta.get("relation_gates", {})
    pair_rels = []
    for rel, mat in graph.relations.items():
        co = mat.tocoo()
        for i, j, w in zip(co.row, co.col, co.data):
            if i < j and w:
                pair_rels.append((ids[i], ids[j], rel, float(w)))

    # transfer check: what the Twitter-trained model says about these YouTube accounts
    m = M.load()
    pred = M.predict(m, X, graph)["ensemble"]
    flagged_by_model = float(np.mean(np.clip(pred, 0, 1) >= m["threshold"]))

    # clusters over coordination links
    parent = {}
    def find(x):
        parent.setdefault(x, x)
        while parent[x] != x:
            parent[x] = parent[parent[x]]; x = parent[x]
        return x
    for s, d, _, _ in pair_rels:
        parent[find(s)] = find(d)
    comp = defaultdict(list)
    for x in list(parent):
        comp[find(x)].append(x)
    ranked = sorted(comp.values(), key=len, reverse=True)
    cluster_of = {u: k + 1 for k, members in enumerate(ranked) for u in members}

    risk = {u: float(it["score"]) for u, it in best.items()}
    linked = set(cluster_of)
    flagged = {u for u, s in risk.items() if s >= 35}
    keep = linked | flagged

    def age_days(u):
        cr = (acct_meta.get(u) or {}).get("created")
        if not cr:
            return None
        return int((time.time() - datetime.fromisoformat(cr.replace("Z", "+00:00")).timestamp()) // 86400)

    # name each coordination group after what its members talk about most
    group_name, groups = {}, []
    for gi, members in enumerate(ranked, start=1):
        if len(members) < 3:
            continue
        tc = Counter()
        for u in members:
            tc.update(set(user_themes[u]))
        tagged_members = sum(1 for u in members if user_themes[u])
        top = [(t, n) for t, n in tc.most_common(3) if n / len(members) >= 0.15]
        name = (" + ".join(THEMES[t]["short"] for t, _ in top[:2]) if top else "No clear theme") + f" · group {gi}"
        group_name[gi] = name
        vids = Counter(); lt = Counter()
        for u in members:
            vids.update(per_user_threads[u].keys())
        groups.append({"gi": gi, "name": name, "members": members, "themes": tc, "tagged": tagged_members, "videos": len(vids)})

    nodes, rels = [], []
    for u in sorted(keep):
        it = best.get(u, {})
        reasons = [x for k in ("tReasons", "lReasons", "nReasons", "gReasons") for x in it.get(k, [])
                   if not x.startswith("No ") and SAFE_REASON.match(x)][:4]
        s = risk.get(u, 0.0)
        ut = [THEMES[t]["short"] for t, _ in user_themes[u].most_common()]
        nodes.append({"id": u, "labels": ["Account"] + (["Flagged"] if u in flagged else []), "props": {
            "main_theme": ut[0] if ut else "no tagged theme", "themes": ut, "group": group_name.get(cluster_of.get(u, 0), "not in a group"),
            "account_id": u, "risk": round(s), "tier": "high" if s >= 60 else "medium" if s >= 35 else "low",
            "comments": per_user_n[u], "videos": len(per_user_threads[u]), "cluster": cluster_of.get(u, 0),
            "timing": it.get("temporal", 0), "language": it.get("linguistic", 0), "talking_points": it.get("narrative", 0), "network": it.get("network", 0),
            "account_age_days": age_days(u), "evidence": reasons}})
    for tid, t in threads.items():
        tm = tmeta.get(tid, {})
        nodes.append({"id": tid, "labels": ["Video"], "props": {
            "title": t["title"] or t["external_id"], "video_id": t["external_id"], "comments": per_thread_n[tid],
            "level": tm.get("level"), "level_name": tm.get("levelName"), "flagged_pct": round(100 * (tm.get("pct") or 0), 1),
            "largest_cluster": tm.get("largestCluster"),
            "published": datetime.fromtimestamp(t["published"] / 1000, timezone.utc).strftime("%Y-%m-%d") if t["published"] else None}})
    k = 0
    for s, d, rel, w in pair_rels:
        if s in keep and d in keep:
            k += 1; rels.append({"id": f"r{k}", "type": rel, "start": s, "end": d, "props": {"weight": round(w, 3), "learned_gate": round(float(gates.get(rel, 0)), 3)}})
    for u in sorted(keep):
        for tid, n in per_user_threads[u].items():
            if tid in threads:
                k += 1; rels.append({"id": f"r{k}", "type": "POSTED_ON", "start": u, "end": tid, "props": {"comments": n}})

    # Theme and Group nodes
    for key, th in THEMES.items():
        nodes.append({"id": f"theme:{key}", "labels": ["Theme"], "props": {"name": th["short"], "key": key, "description": th["label"],
            "comments": theme_comments[key], "accounts": len(theme_users[key]), "keywords": th["patterns"]}})
    for g in groups[:80]:
        mem = g["members"]; risks = [risk.get(u, 0) for u in mem]
        nodes.append({"id": f"group:{g['gi']}", "labels": ["Group"], "props": {"name": g["name"], "accounts": len(mem),
            "accounts_with_theme": g["tagged"], "videos": g["videos"], "avg_risk": round(sum(risks) / len(risks), 1), "max_risk": round(max(risks)),
            "medium_or_high_risk": sum(1 for r in risks if r >= 35),
            "themes": [f"{THEMES[t]['short']} {round(100 * n / len(mem))}%" for t, n in g["themes"].most_common(5)]}})
        for t, n in g["themes"].most_common():
            if n / len(mem) >= 0.10:
                k += 1; rels.append({"id": f"r{k}", "type": "ABOUT", "start": f"group:{g['gi']}", "end": f"theme:{t}",
                                     "props": {"share_pct": round(100 * n / len(mem)), "accounts": n}})
        for u in mem:
            if u in keep:
                k += 1; rels.append({"id": f"r{k}", "type": "MEMBER_OF", "start": u, "end": f"group:{g['gi']}", "props": {}})
    for u in sorted(keep):
        for t in user_themes[u]:
            k += 1; rels.append({"id": f"r{k}", "type": "TALKS_ABOUT", "start": u, "end": f"theme:{t}", "props": {"comments": user_themes[u][t]}})

    weights = report_weights(ROOT / "detector" / "REPORT.md")
    nodes.append({"id": "verdict:BOT", "labels": ["Verdict"], "props": {"name": "BOT", "threshold": round(m["threshold"], 3)}})
    for w in weights:
        nid = "ind:" + re.sub(r"\W+", "_", w["name"]).strip("_").lower()
        nodes.append({"id": nid, "labels": ["Indicator"], "props": {"name": w["name"], "group": w["group"], "importance_pct": w["pct"], "pushes_bot_pct": w["pushes_bot_pct"]}})
        k += 1; rels.append({"id": f"r{k}", "type": "WEIGHT", "start": nid, "end": "verdict:BOT", "props": {"importance_pct": w["pct"], "pushes_bot_pct": w["pushes_bot_pct"]}})

    (out / "graph.json").write_text(json.dumps({"nodes": nodes, "rels": rels}, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")

    # ---- summary ----
    col = json.loads(Path(a.collect_log).read_text(encoding="utf-8")) if a.collect_log else {}
    levels = Counter(t.get("level") for t in tmeta.values())
    ts = [c["ts"] for c in comments if c["ts"]]
    d0 = datetime.fromtimestamp(min(ts) / 1000, timezone.utc).strftime("%d %b %Y"); d1 = datetime.fromtimestamp(max(ts) / 1000, timezone.utc).strftime("%d %b %Y")
    relc = Counter(r[2] for r in pair_rels)
    users = set(per_user_n)
    rs = meta["metrics_random_split"]; lofo = meta.get("lofo_f1", {})
    worst = min(lofo.items(), key=lambda kv: kv[1]) if lofo else ("n/a", 0)
    summary = {
        "built": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        "kpis": [
            {"value": len(comments), "label": f"real comments · {d0} – {d1}"}, {"value": len(threads), "label": "Kosovo–Serbia videos"},
            {"value": len(users), "label": "accounts, pseudonymised"}, {"value": len(linked), "label": "accounts linked to others"},
            {"value": len(flagged), "label": "medium/high-risk accounts"}, {"value": levels.get(2, 0) + levels.get(3, 0), "label": "videos raised for review"},
        ],
        "pipeline": [
            {"stage": "collect", "title": "YouTube Data API", "value": len(threads), "unit": "videos with comments",
             "detail": f"{len(col.get('queries', [])) or 'several'} Albanian, Serbian and English queries · <code>search.list</code>, <code>commentThreads.list</code>, <code>channels.list</code>" + (f" · {col.get('quota_units')} quota units" if col.get("quota_units") else "")},
            {"stage": "protect", "title": "Pseudonymise", "value": len(users), "unit": "accounts as keyed hashes",
             "detail": "HMAC-SHA256 with a server secret. Channel IDs and names are dropped before storage."},
            {"stage": "store", "title": "SQLite store", "value": len(comments), "unit": "comments, deduplicated",
             "detail": "Scoped comment IDs; re-fetching a video adds nothing twice."},
            {"stage": "score", "title": "Coordination engine", "value": len(tmeta), "unit": "threads analysed",
             "detail": "Timing bursts, language (Geg/standard doublets, calques), shared talking points, network signals."},
            {"stage": "link", "title": "Account graph", "value": len(pair_rels), "unit": "links between accounts",
             "detail": " · ".join(f"{k} {v}" for k, v in relc.most_common()) or "none"},
            {"stage": "learn", "title": "Bot model", "value": f"F1 {rs['f1']:.3f}", "unit": f"on {meta['n_accounts']:,} labelled accounts",
             "detail": "LightGBM + relational GNN, calibrated. Learned weights, no hand-set rules."},
            {"stage": "decide", "title": "Analyst", "value": len(flagged), "unit": "accounts to review",
             "detail": f"{levels.get(2, 0)} videos at Level 2, {levels.get(3, 0)} at Level 3, {levels.get(4, 0)} quiet. A human decides."},
        ],
        "model": {
            "dataset": "Cresci-2017, Twitter", "n_accounts": meta["n_accounts"], "n_features": len(meta.get("features", [])) or len(M.FEATURE_ORDER),
            "weights": [{"name": w["name"], "pct": w["pct"]} for w in weights],
            "bench": [["Held-out F1 (seen bot types)", f"{rs['f1']:.3f}"], ["95% interval", " – ".join(f"{x:.3f}" for x in meta["f1_ci"])],
                      ["Precision / recall", f"{rs['precision']:.3f} / {rs['recall']:.3f}"], ["PR-AUC", f"{rs['pr_auc']:.3f}"],
                      ["Calibration error (ECE)", f"{rs['ece']:.3f}"],
                      ["F1 on bot types never seen in training", f"{min(lofo.values()):.3f} – {max(lofo.values()):.3f} ({len(lofo)} tests)" if lofo else "n/a"],
                      ["Weakest unseen type", f"{worst[0].replace('_', ' ')} · {worst[1]:.3f}"]],
            "gates": [{"relation": r, "weight": float(gates.get(r, 0)), "trained": bool(meta.get("relation_trained", {}).get(r, True))} for r in COORD if r in gates],
            "confidence": confidence_table(ROOT / "detector" / "data" / "scores_cresci2017_test" / "scores.csv"),
            "tested": tested_totals(ROOT / "detector" / "REPORT.md", meta),
            "stress": stress_test(ROOT / "detector" / "data" / "processed" / "train_results.json"),
            "transfer": f"Transfer check: applied to these {len(users):,} YouTube accounts, the Twitter-trained model flags {100 * flagged_by_model:.0f}% of them as bots. "
                        "Most YouTube commenters post once and have no follower data, which the Twitter model never saw. So this page does not label anyone a bot: "
                        "accounts are ranked by the coordination engine, and a Kosovo-labelled sample is the next step.",
        },
        "counts": {"comments": len(comments), "videos": len(threads), "accounts": len(users), "nodes": len(nodes), "rels": len(rels)},
        "findings": {
            "groups_3": len(groups), "groups_5": sum(1 for g in groups if len(g["members"]) >= 5),
            "groups_10": sum(1 for g in groups if len(g["members"]) >= 10), "largest_group": len(ranked[0]) if ranked else 0,
            "accounts_in_groups_5": sum(len(g["members"]) for g in groups if len(g["members"]) >= 5),
            "strong": sum(1 for s in risk.values() if s >= 60), "moderate": sum(1 for s in risk.values() if 35 <= s < 60),
            "weak": sum(1 for s in risk.values() if 15 <= s < 35), "none": sum(1 for s in risk.values() if s < 15),
            "accounts": len(risk),
        },
        "clades": clades(groups, risk, cluster_of, user_texts, norm_orig, user_ts, per_user_threads, threads, pair_rels, best),
        "showcase": showcase(best, risk, cluster_of, group_name, user_themes, threads, per_user_n),
        "stats": stage_stats(col, threads, tmeta, per_thread_n, per_user_n, best, risk, relc, levels, weights, meta),
    }
    review = sum(levels.get(k, 0) for k in (1, 2, 3))
    summary["kpis"][5] = {"value": review, "label": "videos raised for review"}
    summary["pipeline"][6]["detail"] = (f"{levels.get(1, 0)} videos at Level 1, {levels.get(2, 0)} at Level 2, {levels.get(3, 0)} at Level 3, "
                                        f"{levels.get(4, 0)} quiet. A human decides.")
    (out / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps({**summary["counts"], "linked": len(linked), "flagged": len(flagged), "rel_types": dict(relc),
                      "levels": dict(levels), "model_flags_pct": round(100 * flagged_by_model, 1)}))


if __name__ == "__main__":
    main()
