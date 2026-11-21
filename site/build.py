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

COORD = ("COPY", "SYNC", "LINK", "REPLY", "CO_THREAD")
SAFE_REASON = re.compile(r"^[^\"“”«»']*$")  # drop any reason that quotes comment text


def report_weights(path: Path, top: int = 14) -> list[dict]:
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


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True)
    ap.add_argument("--collect-log")
    a = ap.parse_args()
    out = Path(__file__).resolve().parent / "data"; out.mkdir(exist_ok=True)
    db = sqlite3.connect(a.db); db.row_factory = sqlite3.Row
    meta = json.loads((ROOT / "detector" / "model" / "model.json").read_text())

    threads = {r["id"]: dict(r) for r in db.execute("SELECT id, external_id, title, published FROM threads WHERE simulated=0")}
    comments = db.execute("SELECT id, thread_id, user, ts, account FROM comments WHERE simulated=0").fetchall()
    per_user_threads, per_user_n, per_thread_n = defaultdict(Counter), Counter(), Counter()
    acct_meta = {}
    for c in comments:
        per_user_threads[c["user"]][c["thread_id"]] += 1; per_user_n[c["user"]] += 1; per_thread_n[c["thread_id"]] += 1
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

    nodes, rels = [], []
    for u in sorted(keep):
        it = best.get(u, {})
        reasons = [x for k in ("tReasons", "lReasons", "nReasons", "gReasons") for x in it.get(k, [])
                   if not x.startswith("No ") and SAFE_REASON.match(x)][:4]
        s = risk.get(u, 0.0)
        nodes.append({"id": u, "labels": ["Account"] + (["Flagged"] if u in flagged else []), "props": {
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
            "transfer": f"Transfer check: applied to these {len(users):,} YouTube accounts, the Twitter-trained model flags {100 * flagged_by_model:.0f}% of them as bots. "
                        "Most YouTube commenters post once and have no follower data, which the Twitter model never saw. So this page does not label anyone a bot: "
                        "accounts are ranked by the coordination engine, and a Kosovo-labelled sample is the next step.",
        },
        "counts": {"comments": len(comments), "videos": len(threads), "accounts": len(users), "nodes": len(nodes), "rels": len(rels)},
    }
    (out / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps({**summary["counts"], "linked": len(linked), "flagged": len(flagged), "rel_types": dict(relc),
                      "levels": dict(levels), "model_flags_pct": round(100 * flagged_by_model, 1)}))


if __name__ == "__main__":
    main()
