"""Weight distribution for the bot detector.

Turns LightGBM / GNN outputs into the percentage view the product shows:

* ``percent_contributions``: per-account signed share of every indicator (|shares| sum to 100).
* ``global_importance``: model-wide importance of every indicator.
* ``relation_weights``: learned gate of every graph relation.
* ``export_neo4j``: CSV files and an ``import.cypher`` script for Neo4j 5.

Indicators in the ``context`` group are model inputs only. They are folded into one row labelled
"Context weighting" and never shown by their own names.
"""
from __future__ import annotations

import re
from collections.abc import Mapping
from pathlib import Path

import numpy as np
import pandas as pd

from .features import FEATURES
from .graph import NETWORK_FEATURES, Graph

CONTEXT_GROUP = "context"
CONTEXT_LABEL = "Context weighting"
CONTEXT_FEATURE = "context_weighting"
UNKNOWN_GROUP = "unknown"
MINUS = "−"
TOP_PER_ACCOUNT = 8
_REL_NAME_OK = re.compile(r"^[A-Z][A-Z0-9_]*$")

PCT_COLUMNS = ["row", "indicator", "feature", "group", "percent", "direction"]


def _meta(name: str, labels: Mapping[str, str] | None) -> tuple[str, str]:
    if name in FEATURES:
        label, group = FEATURES[name]
    elif name in NETWORK_FEATURES:
        label, group = NETWORK_FEATURES[name]
    else:
        label, group = name, UNKNOWN_GROUP
    if labels and name in labels:
        label = labels[name]
    return label, group


def _fold(contrib, feature_names, labels):
    """Drop the bias column, fold context features into one column. Returns (meta, M) with M n x k."""
    names = list(feature_names)
    C = np.asarray(contrib, dtype=float)
    if C.ndim != 2:
        raise ValueError("contrib must be a 2-D array (n x (features + 1))")
    f = len(names)
    if C.shape[1] == f + 1:  # LightGBM pred_contrib: last column is the bias / expected value
        C = C[:, :f]
    elif C.shape[1] != f:
        raise ValueError(f"contrib has {C.shape[1]} columns, expected {f} or {f + 1}")

    meta, cols, ctx = [], [], []
    for j, name in enumerate(names):
        label, group = _meta(name, labels)
        if group == CONTEXT_GROUP:
            ctx.append(j)
            continue
        meta.append((label, name, group))
        cols.append(C[:, j])
    if ctx:
        meta.append((CONTEXT_LABEL, CONTEXT_FEATURE, CONTEXT_GROUP))
        cols.append(C[:, ctx].sum(axis=1))
    M = np.column_stack(cols) if cols else np.zeros((C.shape[0], 0))
    return meta, M


def _tenths(weights) -> np.ndarray:
    """Non-negative weights -> integer tenths of a percent that sum to exactly 1000 (largest remainder)."""
    w = np.abs(np.asarray(weights, dtype=float))
    tot = w.sum()
    if tot <= 0 or not np.isfinite(tot):
        return np.zeros(len(w), dtype=int)
    a = w * 1000.0 / tot
    fl = np.floor(a).astype(int)
    rem = 1000 - int(fl.sum())
    if rem > 0:
        fl[np.argsort(-(a - np.floor(a)), kind="stable")[:rem]] += 1
    return fl


def percent_contributions(contrib, feature_names, labels: Mapping[str, str] | None = None) -> pd.DataFrame:
    """Per-row signed percentage contribution of every indicator.

    contrib: ndarray n x (f+1) from ``booster.predict(X, pred_contrib=True)`` (last column dropped) or n x f.
    feature_names: the f feature names, in column order.
    labels: optional {feature name: display label} overrides.

    Returns a long DataFrame with columns row, indicator, feature, group, percent, direction. Row i is row i of
    ``contrib`` (the caller keeps the account order). Per row the |percent| sum is 100 (or 0 if all contributions
    are zero). Sorted by |percent| descending within each row. direction is '+' (raises BOT probability) or
    '−' (lowers it).
    """
    meta, M = _fold(contrib, feature_names, labels)
    out_row, out_ind, out_feat, out_grp, out_pct, out_dir = [], [], [], [], [], []
    for r in range(M.shape[0]):
        vals = M[r]
        fl = _tenths(np.abs(vals))
        for j in np.argsort(-fl, kind="stable"):
            sign = -1.0 if vals[j] < 0 else 1.0
            pct = 0.0 if fl[j] == 0 else sign * fl[j] / 10.0
            label, feat, grp = meta[j]
            out_row.append(r)
            out_ind.append(label)
            out_feat.append(feat)
            out_grp.append(grp)
            out_pct.append(pct)
            out_dir.append(MINUS if pct < 0 else "+")
    df = pd.DataFrame({
        "row": np.asarray(out_row, dtype=int),
        "indicator": out_ind,
        "feature": out_feat,
        "group": out_grp,
        "percent": np.asarray(out_pct, dtype=float),
        "direction": out_dir,
    }, columns=PCT_COLUMNS)
    return df


def top_indicators(df_long: pd.DataFrame, row: int, k: int | None = None) -> list[dict]:
    """The indicators of one row, largest |percent| first: [{indicator, percent, direction}, ...]."""
    sub = df_long[df_long["row"] == row]
    sub = sub.assign(_a=sub["percent"].abs()).sort_values("_a", ascending=False, kind="stable")
    if k is not None:
        sub = sub.head(int(k))
    return [
        {"indicator": r.indicator, "percent": float(r.percent), "direction": r.direction}
        for r in sub.itertuples(index=False)
    ]


def global_importance(contrib, feature_names, labels: Mapping[str, str] | None = None) -> pd.DataFrame:
    """Model-wide view: importance_pct = mean |contribution| normalised to 100; pushes_bot_pct = share of rows
    where the indicator pushed towards BOT. Context features folded into one row, as in percent_contributions."""
    meta, M = _fold(contrib, feature_names, labels)
    n = M.shape[0]
    mean_abs = np.abs(M).mean(axis=0) if n else np.zeros(len(meta))
    imp = _tenths(mean_abs) / 10.0
    pushes = (M > 0).mean(axis=0) * 100.0 if n else np.zeros(len(meta))
    df = pd.DataFrame({
        "indicator": [m[0] for m in meta],
        "feature": [m[1] for m in meta],
        "group": [m[2] for m in meta],
        "importance_pct": imp.astype(float),
        "pushes_bot_pct": np.round(pushes, 1).astype(float),
    })
    return df.sort_values("importance_pct", ascending=False, kind="stable").reset_index(drop=True)


def relation_weights(state: Mapping[str, float]) -> pd.DataFrame:
    """Learned relation gates -> relation, weight, weight_pct (normalised to 100 over the positive weights)."""
    names = list(state)
    vals = np.array([float(state[k]) for k in names], dtype=float)
    pos = np.clip(vals, 0.0, None)
    pct = _tenths(pos) / 10.0
    df = pd.DataFrame({"relation": names, "weight": vals, "weight_pct": pct.astype(float)})
    return df.sort_values("weight", ascending=False, kind="stable").reset_index(drop=True)


_CYPHER_HEAD = """// Neo4j 5 import for the bot detector.
// 1. Copy the CSV files written next to this file into Neo4j's import/ folder.
// 2. Run this script in Neo4j Browser (statements separated by ';').

// ---- constraints
CREATE CONSTRAINT account_id_unique IF NOT EXISTS FOR (a:Account) REQUIRE a.account_id IS UNIQUE;
CREATE CONSTRAINT indicator_name_unique IF NOT EXISTS FOR (i:Indicator) REQUIRE i.name IS UNIQUE;
CREATE CONSTRAINT verdict_name_unique IF NOT EXISTS FOR (v:Verdict) REQUIRE v.name IS UNIQUE;

// ---- accounts
LOAD CSV WITH HEADERS FROM 'file:///nodes_account.csv' AS row
MERGE (a:Account {account_id: row.account_id})
SET a.bot_probability = toFloat(row.bot_probability),
    a.verdict = row.verdict,
    a.observed_posts = toInteger(row.observed_posts),
    a.model_version = row.model_version,
    a.family = row.family,
    a.label = CASE WHEN row.label = '' THEN null ELSE toInteger(row.label) END;

// ---- indicators (global model view) and their weight on the BOT verdict
LOAD CSV WITH HEADERS FROM 'file:///nodes_indicator.csv' AS row
MERGE (i:Indicator {name: row.indicator})
SET i.group = row.group, i.pushes_bot_pct = toFloat(row.pushes_bot_pct)
MERGE (v:Verdict {name: 'BOT'})
MERGE (i)-[w:WEIGHT]->(v)
SET w.importance_pct = toFloat(row.importance_pct);

// ---- account relations (one block per type: Cypher needs a literal relationship type without APOC)
"""

_CYPHER_REL = """LOAD CSV WITH HEADERS FROM 'file:///rels_account.csv' AS row
WITH row WHERE row.type = '{t}'
MERGE (a:Account {{account_id: row.src}})
MERGE (b:Account {{account_id: row.dst}})
MERGE (a)-[r:{t}]->(b)
SET r.weight = toFloat(row.weight), r.learned_weight = toFloat(row.learned_weight);

"""

_CYPHER_TAIL = """// ---- per-account evidence
LOAD CSV WITH HEADERS FROM 'file:///rels_contrib.csv' AS row
MERGE (a:Account {account_id: row.account_id})
MERGE (i:Indicator {name: row.indicator})
MERGE (a)-[e:EVIDENCE]->(i)
SET e.percent = toFloat(row.percent), e.direction = row.direction;

// ---- example queries (uncomment to run)
//
// Example 1: the most bot-like accounts with the indicators behind each verdict
// MATCH (a:Account {verdict: 'BOT'})-[e:EVIDENCE]->(i:Indicator)
// RETURN a.account_id AS account, a.bot_probability AS bot_pct, i.name AS indicator,
//        e.percent AS percent, e.direction AS direction
// ORDER BY bot_pct DESC, abs(percent) DESC LIMIT 100;
//
// Example 2: the coordinated group around one account (up to 2 hops over coordination links)
// MATCH p = (a:Account {account_id: 'ACCOUNT_ID'})-[:COPY|SYNC|LINK|REPLY|CO_THREAD*1..2]-(b:Account)
// RETURN p LIMIT 200;
//
// Example 3: global indicator weight view
// MATCH (i:Indicator)-[w:WEIGHT]->(:Verdict {name: 'BOT'})
// RETURN i.name AS indicator, w.importance_pct AS importance_pct
// ORDER BY importance_pct DESC;
"""


def _gate_map(relation_w) -> dict[str, float]:
    if isinstance(relation_w, pd.DataFrame):
        return {str(r): float(w) for r, w in zip(relation_w["relation"], relation_w["weight"])}
    return {str(k): float(v) for k, v in dict(relation_w).items()}


def export_neo4j(out_dir, scored: pd.DataFrame, graph: Graph, contrib_long: pd.DataFrame,
                 global_imp: pd.DataFrame, relation_w, top_k: int = TOP_PER_ACCOUNT) -> dict[str, str]:
    """Write nodes_account.csv, rels_account.csv, nodes_indicator.csv, rels_contrib.csv and import.cypher.

    scored: one row per account with account_id, bot_probability (0-100), verdict, family, label. Row i of
        ``scored`` must correspond to row i of the contrib matrix that produced ``contrib_long``.
    relation_w: the DataFrame from ``relation_weights`` or a {relation: gate} dict.
    Returns {file name: absolute path}.
    """
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    sc = scored.reset_index(drop=True)
    missing = [c for c in ("account_id", "bot_probability", "verdict") if c not in sc.columns]
    if missing:
        raise ValueError(f"scored is missing columns: {missing}")

    def _blank(series: pd.Series) -> pd.Series:
        return series.where(series.notna(), "").astype(str)

    def _label(series: pd.Series) -> pd.Series:
        def one(x):
            if pd.isna(x) or x == "":
                return ""
            try:
                return str(int(float(x)))
            except (TypeError, ValueError):
                return str(x)
        return series.map(one)

    family = sc["family"] if "family" in sc.columns else pd.Series("", index=sc.index)
    label = sc["label"] if "label" in sc.columns else pd.Series(np.nan, index=sc.index)
    nodes_account = pd.DataFrame({
        "account_id": sc["account_id"].astype(str),
        "bot_probability": pd.to_numeric(sc["bot_probability"], errors="coerce").round(1),
        "verdict": sc["verdict"].astype(str),
        "family": _blank(family),
        "label": _label(label),
    })

    for key in ("observed_posts", "model_version"):
        if key in sc:
            nodes_account[key] = sc[key]
    gates = _gate_map(relation_w)
    ids = list(graph.ids)
    rel_rows = []
    for rel, M in graph.relations.items():
        if not _REL_NAME_OK.match(rel):
            raise ValueError(f"relation name {rel!r} is not a safe Cypher relationship type")
        C = M.tocoo()
        keep = (C.row < C.col) & (C.data != 0)
        for i, j, w in sorted(zip(C.row[keep], C.col[keep], C.data[keep]), key=lambda t: (t[0], t[1])):
            rel_rows.append((ids[i], ids[j], rel, float(w), float(w) * gates.get(rel, 0.0)))
    rels_account = pd.DataFrame(rel_rows, columns=["src", "dst", "type", "weight", "learned_weight"])

    nodes_indicator = global_imp[["indicator", "group", "importance_pct", "pushes_bot_pct"]].copy()

    acc_ids = sc["account_id"].astype(str).to_numpy()
    cl = contrib_long[contrib_long["row"] < len(acc_ids)].copy()
    cl["_a"] = cl["percent"].abs()
    cl = cl.sort_values(["row", "_a"], ascending=[True, False], kind="stable").groupby("row", sort=True).head(top_k)
    rels_contrib = pd.DataFrame({
        "account_id": acc_ids[cl["row"].to_numpy(dtype=int)],
        "indicator": cl["indicator"].to_numpy(),
        "percent": cl["percent"].to_numpy(dtype=float),
        "direction": cl["direction"].to_numpy(),
    })

    files = {
        "nodes_account.csv": nodes_account,
        "rels_account.csv": rels_account,
        "nodes_indicator.csv": nodes_indicator,
        "rels_contrib.csv": rels_contrib,
    }
    paths = {}
    for name, df in files.items():
        p = out / name
        df.to_csv(p, index=False, encoding="utf-8", lineterminator="\n")
        paths[name] = str(p.resolve())

    rel_types = [r for r in graph.relations if _REL_NAME_OK.match(r)]
    cypher = _CYPHER_HEAD + "".join(_CYPHER_REL.format(t=t) for t in rel_types) + _CYPHER_TAIL
    cp = out / "import.cypher"
    cp.write_text(cypher, encoding="utf-8", newline="\n")
    paths["import.cypher"] = str(cp.resolve())
    return paths
