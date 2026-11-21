"""CLI.

  python -m detector train   [--data cresci2017] [--fast]      train + evaluate + save model + report
  python -m detector score   --data youtube_local [--out DIR]   BOT % per account, Neo4j export, blind labelling sheet
  python -m detector explain [ACCOUNT] [--data D]       one decision, every indicator %, learned graph neighbourhood
  python -m detector local-f1 --labels L.csv --scores S.csv      in-domain F1 from two annotators
"""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

import numpy as np
import pandas as pd

from . import datasets, evaluate as E, model as M
from .weights import export_neo4j, global_importance, percent_contributions, relation_weights

DATA = Path(__file__).resolve().parent / "data"


def _idx(ids, pos):
    return np.array([pos[a] for a in ids if a in pos], dtype=int)


def _val_from(train_ids, accounts, frac=0.15, seed=0):
    a = accounts[accounts.account_id.isin(train_ids)]
    rng = np.random.default_rng(seed); val = []
    for _, g in a.groupby("family"):
        k = max(1, int(round(len(g) * frac))) if len(g) > 3 else 0
        val += list(rng.choice(g.account_id.to_numpy(), size=k, replace=False)) if k else []
    vs = set(val)
    return [t for t in train_ids if t not in vs], val


def _part_metrics(y, parts, idx, thr):
    out = {}
    for k, p in parts.items():
        t = thr if k == "ensemble" else 0.5
        out[k] = E.metrics(y[idx], np.clip(p[idx], 0, 1), threshold=t)
    return out


def _model_dir(args):
    """Explicit --model-dir, else the YouTube-profile model for YouTube data when it exists, else the full model."""
    if getattr(args, "model_dir", None):
        return Path(args.model_dir)
    yt = M.MODEL_DIR.parent / "model_youtube"
    return yt if args.data.startswith("youtube") and (yt / "model.json").exists() else M.MODEL_DIR


def cmd_train(args):
    t0 = time.time()
    from uuid import uuid4
    run_dir = Path(args.model_dir) if args.model_dir else M.MODEL_DIR.parent / "runs" / uuid4().hex
    run_dir.mkdir(parents=True, exist_ok=False)
    accounts, posts = datasets.load(args.data)
    accounts = accounts.drop_duplicates("account_id")
    accounts = accounts[accounts.account_id.isin(set(posts.account_id))]  # accounts need observable behaviour
    feats = M.PROFILES[args.profile]
    if args.obs:  # observation-matched training: each account keeps a random 1..obs of its posts (like YouTube)
        rng = np.random.default_rng(0)
        k = dict(zip(accounts.account_id, rng.integers(1, args.obs + 1, len(accounts))))
        posts = posts.sample(frac=1, random_state=0)
        posts = posts[posts.groupby("account_id").cumcount() < posts.account_id.map(k)]
        print(f"observation-matched: ≤{args.obs} posts per account → {len(posts)} posts")
    print(f"{len(accounts)} accounts, {len(posts)} posts"); X, graph = M.design(accounts, posts)
    pos = {a: i for i, a in enumerate(graph.ids)}
    y = accounts.set_index("account_id").reindex(graph.ids)["label"].to_numpy(dtype=float)
    print(f"features + graph in {time.time() - t0:.0f}s; edges:", {r: int(m.nnz // 2) for r, m in graph.relations.items()})
    ep = 150 if args.fast else 400
    results = {"dataset": args.data + (f" (profile {args.profile}, ≤{args.obs} posts/account)" if args.obs or args.profile != "full" else ""),
               "model": "LightGBM + relational GNN, isotonic-calibrated ensemble", "protocols": {}, "notes": []}

    tr, va, te = E.split_stratified(accounts, test=0.2, val=0.1, seed=0)
    m = M.fit(X, graph, y, _idx(tr, pos), _idx(va, pos), gnn_epochs=ep, features=feats)
    ti = _idx(te, pos); pm = _part_metrics(y, m["parts"], ti, m["threshold"])
    ens = np.clip(m["parts"]["ensemble"], 0, 1)
    results["protocols"]["random_split"] = {**pm["ensemble"], "components": {k: {"f1": v["f1"], "pr_auc": v["pr_auc"]} for k, v in pm.items()},
        "f1_ci": list(E.bootstrap_ci(y[ti], ens[ti], lambda a, b: E.metrics(a, b, threshold=m["threshold"])["f1"], n=1000)),
        "calibration": E.calibration_table(y[ti], ens[ti])}
    print("random split:", {k: round(v["f1"], 4) for k, v in pm.items()})

    lofo = {}
    for fam, ftr, fte in E.leave_one_family_out(accounts):
        ftr, fva = _val_from(ftr, accounts)
        mf = M.fit(X, graph, y, _idx(ftr, pos), _idx(fva, pos), gnn_epochs=ep, features=feats)
        fi = _idx(fte, pos); pf = _part_metrics(y, mf["parts"], fi, mf["threshold"])
        e = np.clip(mf["parts"]["ensemble"], 0, 1)
        lofo[fam] = {**pf["ensemble"], "f1_ci": list(E.bootstrap_ci(y[fi], e[fi], lambda a, b: E.metrics(a, b, threshold=mf["threshold"])["f1"], n=300)),
                     "lightgbm_f1": pf["lightgbm"]["f1"], "gnn_f1": pf["gnn"]["f1"]}
        print(f"  unseen {fam}: F1 {pf['ensemble']['f1']:.3f} (LightGBM {pf['lightgbm']['f1']:.3f}, GNN {pf['gnn']['f1']:.3f})")
    results["protocols"]["leave_one_family_out"] = lofo

    pred = M.predict(m, X, graph)
    gi = global_importance(pred["contrib"], feats)
    rw = relation_weights(M.G.relation_gates(m["gnn"]))
    results["indicator_weights"] = gi.to_dict("records"); results["relation_weights"] = rw.to_dict("records")
    untrained = [r for r, t in m["gnn"]["relation_trained"].items() if not t]
    results["notes"] += [
        "Benchmark = Cresci-2017 (Twitter, human-labelled). These numbers are NOT Kosovo/YouTube accuracy; use `local-f1` with two annotators for that.",
        "Transductive graph: test accounts' features and edges are visible to the GNN, their labels are not.",
        "Leave-one-family-out = the bot type in the test set was never seen in training; it is the honest generalisation number.",
    ] + ([f"Relations with no edges in training data keep their initial weight (not learned): {', '.join(untrained)}."] if untrained else [])
    M.save(m, {"trained_on": args.data, "profile": args.profile, "obs": args.obs, "trained_at": time.strftime("%Y-%m-%d %H:%M"), "n_accounts": int(len(accounts)),
               "metrics_random_split": {k: pm["ensemble"][k] for k in ("precision", "recall", "f1", "pr_auc", "roc_auc", "brier", "ece")},
               "f1_ci": results["protocols"]["random_split"]["f1_ci"],
               "lofo_f1": {k: v["f1"] for k, v in lofo.items()}}, run_dir)
    report = run_dir / "REPORT.md"
    E.write_report(results, report)
    with open(report, "a", encoding="utf-8") as fh:
        fh.write("\n## Learned indicator weights (share of total evidence, test-time mean |SHAP|)\n\n| Indicator | Group | Weight | Pushes towards BOT |\n|---|---|---|---|\n")
        for r in results["indicator_weights"]:
            fh.write(f"| {r['indicator']} | {r['group']} | {r['importance_pct']:.1f}% | {r['pushes_bot_pct']:.0f}% of accounts |\n")
        fh.write("\n## Learned relation weights (GNN gates)\n\n| Relation | Weight | Share |\n|---|---|---|\n")
        for r in results["relation_weights"]:
            fh.write(f"| {r['relation']} | {r['weight']:.3f} | {r['weight_pct']:.1f}% |\n")
    (run_dir / "train_results.json").write_text(json.dumps(results, indent=1, default=float), encoding="utf-8")
    print(f"done in {time.time() - t0:.0f}s → {run_dir}")


def _load(args):
    accounts, posts = datasets.load(args.data)
    accounts = accounts.drop_duplicates("account_id")
    if getattr(args, "split", "all") == "test":  # held-out benchmark accounts (never trained on); true labels kept
        accounts = accounts[accounts.account_id.isin(set(posts.account_id))]
        _, _, te = E.split_stratified(accounts, test=0.2, val=0.1, seed=0)
        accounts = accounts[accounts.account_id.isin(set(te))]
        posts = posts[posts.account_id.isin(set(te))]
        print(f"held-out test split: {len(accounts)} accounts, {len(posts)} posts")
    return accounts, posts


def cmd_score(args):
    accounts, posts = _load(args)
    m = M.load(_model_dir(args))
    prior = None if args.prior < 0 else args.prior
    recs, scored, graph, contrib = M.score(accounts, posts, m, prior=prior)
    out = Path(args.out or DATA / f"scores_{args.data}{'_test' if args.split == 'test' else ''}"); out.mkdir(parents=True, exist_ok=True)
    (out / "scores.json").write_text(json.dumps(recs, indent=1, ensure_ascii=False), encoding="utf-8")
    scored.to_csv(out / "scores.csv", index=False)
    pct = percent_contributions(contrib, m["features"])
    export_neo4j(out / "neo4j", scored, graph, pct, global_importance(contrib, m["features"]),
                 relation_weights(m["meta"]["relation_gates"]))
    # blind labelling sheet for two annotators (no scores shown, sample comments for context)
    sample = posts.sort_values("ts").groupby("account_id")["text"].apply(lambda s: " ‖ ".join(map(str, s.head(5)))[:600])
    sheet = scored[["account_id"]].sample(frac=1, random_state=0)
    sheet["sample_posts"] = sheet.account_id.map(sample).fillna("")
    sheet["annotator_a"] = ""; sheet["annotator_b"] = ""
    sheet["allowed_labels"] = "0=human, 1=bot, unsure=insufficient evidence"
    sheet.to_csv(out / "label_sheet.csv", index=False, encoding="utf-8-sig")
    n_bot = int((scored.verdict == "BOT").sum())
    print(f"{len(scored)} accounts scored, {n_bot} BOT ({'base rate ' + format(100 * prior, '.0f') + '%, BOT if ≥ 50%' if prior is not None else 'threshold ' + format(100 * m['threshold'], '.1f') + '%'}) → {out}")
    for r in sorted(recs, key=lambda r: -r["bot_probability"])[: args.top]:
        print(f"  {r['account_id'][:24]:24} BOT {r['bot_probability']:5.1f}%  " + " · ".join(f"{i['indicator']} {i['direction']}{abs(i['percent']):.0f}%" for i in r["indicators"][:4]))


def cmd_explain(args):
    """Terminal view of one decision: BOT %, every indicator's share, and the learned graph neighbourhood."""
    accounts, posts = _load(args)
    m = M.load(_model_dir(args))
    X, graph = M.design(accounts, posts)
    out = M.predict(m, X, graph)
    pct = percent_contributions(out["contrib"], m["features"])
    ids = graph.ids
    i = ids.index(args.account) if args.account in ids else int(np.argmax(out["ensemble"]))
    p, thr = float(out["ensemble"][i]), m["threshold"]
    gates = m["meta"]["relation_gates"]
    bar = lambda v, w=30: "█" * int(round(abs(v) / 100 * w)) or "·"
    prior = None if args.prior < 0 else args.prior
    records, *_ = M.score(accounts, posts, m, prior=prior)
    record = records[i]
    print(f"\nAccount {ids[i]} | model {record['model_version']} | posts {record['observed_posts']}")
    lab = accounts.set_index("account_id")["label"].get(ids[i], float("nan"))
    truth = "" if lab != lab else f"   true label: {'BOT' if lab == 1 else 'HUMAN'}"
    print(f"  Model score {record['bot_probability']:.1f}%   → {record['verdict']} (threshold {record['threshold']:.1f}%){truth}")
    print(f"  model parts: indicator model {100 * out['lightgbm'][i]:.1f}% · graph network {100 * out['gnn'][i]:.1f}% · calibrated ensemble {100 * p:.1f}%\n")
    print("  Evidence (relative contribution to the LightGBM component)")
    for r in pct[pct.row == i].itertuples():
        print(f"   {r.direction}{abs(r.percent):5.1f}%  {bar(r.percent):30}  {r.indicator}")
    print("\n  Graph neighbourhood (learned relation weight × edge count)")
    any_edge = False
    for rel, A in graph.relations.items():
        row = A.getrow(i)
        for j, w in sorted(zip(row.indices, row.data), key=lambda t: -t[1])[:5]:
            any_edge = True
            print(f"   {rel:9} → {ids[j][:28]:28} edges {w:4.0f}  learned weight {gates.get(rel, 0):.2f}  neighbour BOT {100 * out['ensemble'][j]:5.1f}%")
    if not any_edge:
        print("   (no coordination edges)")
    print("\n  Learned relation weights:", ", ".join(f"{k} {v:.2f}" for k, v in gates.items()))


def cmd_local_f1(args):
    print(json.dumps(E.evaluate_in_domain(args.labels, args.scores), indent=1, default=float))


def main():
    import sys
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    ap = argparse.ArgumentParser(prog="python -m detector")
    sub = ap.add_subparsers(dest="cmd", required=True)
    t = sub.add_parser("train"); t.add_argument("--data", default="cresci2017"); t.add_argument("--fast", action="store_true")
    t.add_argument("--profile", choices=sorted(M.PROFILES), default="full"); t.add_argument("--obs", type=int, default=0, help="keep a random 1..N posts per account")
    t.add_argument("--model-dir", help="New output directory; defaults to a unique detector/runs folder"); t.set_defaults(fn=cmd_train)
    s = sub.add_parser("score"); s.add_argument("--data", default="youtube_kosovo"); s.add_argument("--out"); s.add_argument("--top", type=int, default=10); s.add_argument("--model-dir")
    s.add_argument("--split", choices=["all", "test"], default="all", help="test = held-out benchmark accounts only")
    s.add_argument("--prior", type=float, default=0.10, help="expected bot share in the scored population (base rate); -1 = benchmark threshold"); s.set_defaults(fn=cmd_score)
    x = sub.add_parser("explain"); x.add_argument("account", nargs="?", default=""); x.add_argument("--data", default="youtube_kosovo"); x.add_argument("--model-dir")
    x.add_argument("--split", choices=["all", "test"], default="all"); x.add_argument("--prior", type=float, default=0.10); x.set_defaults(fn=cmd_explain)
    l = sub.add_parser("local-f1"); l.add_argument("--labels", required=True); l.add_argument("--scores", required=True); l.set_defaults(fn=cmd_local_f1)
    a = ap.parse_args(); a.fn(a)


if __name__ == "__main__":
    main()
