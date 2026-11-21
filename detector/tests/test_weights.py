"""Synthetic tests for detector/weights.py: a tiny LightGBM model and a toy account graph."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
import lightgbm as lgb
import scipy.sparse as sp

from detector.features import FEATURES
from detector.graph import NETWORK_FEATURES, Graph
from detector import weights as W

N = 120
CONTEXT_NAMES = [k for k, (_, g) in FEATURES.items() if g == "context"]
CONTEXT_LABELS = [FEATURES[k][0] for k in CONTEXT_NAMES]
NAMES = list(FEATURES) + list(NETWORK_FEATURES)


def _toy_graph(n: int, seed: int = 0) -> Graph:
    rng = np.random.default_rng(seed)
    rels = {}
    for rel, m_edges in (("COPY", 40), ("SYNC", 25), ("LINK", 15), ("REPLY", 10), ("CO_THREAD", 30)):
        if rel == "REPLY":  # keep one relation empty on purpose
            rels[rel] = sp.csr_matrix((n, n))
            continue
        a = rng.integers(0, n, m_edges)
        b = rng.integers(0, n, m_edges)
        keep = a != b
        a, b = a[keep], b[keep]
        w = rng.integers(1, 4, a.size).astype(float)
        m = sp.coo_matrix((w, (a, b)), shape=(n, n))
        rels[rel] = (m + m.T).tocsr()
    return Graph(ids=[f"acc{i:04d}" for i in range(n)], relations=rels)


@pytest.fixture(scope="module")
def fixture():
    rng = np.random.default_rng(0)
    X = pd.DataFrame(rng.normal(size=(N, len(NAMES))), columns=NAMES)
    y = ((X["age_days_log"] + X["cross_dup_ratio"] + rng.normal(scale=0.5, size=N)) > 0).astype(int)
    booster = lgb.train(
        {"objective": "binary", "num_leaves": 7, "min_data_in_leaf": 5, "verbose": -1, "seed": 0},
        lgb.Dataset(X, label=y), num_boost_round=15,
    )
    contrib = booster.predict(X, pred_contrib=True)
    prob = booster.predict(X)
    graph = _toy_graph(N)
    scored = pd.DataFrame({
        "account_id": graph.ids,
        "bot_probability": np.round(prob * 100, 1),
        "verdict": np.where(prob >= 0.5, "BOT", "NOT BOT"),
        "family": rng.choice(["genuine", "social_spambots_1"], N),
        "label": np.where(rng.random(N) < 0.2, np.nan, y.to_numpy()),
    })
    return {"contrib": contrib, "graph": graph, "scored": scored}


def _n_indicators() -> int:
    return len([k for k, (_, g) in FEATURES.items() if g != "context"]) + len(NETWORK_FEATURES) + 1


def test_contrib_shape_and_bias_dropped(fixture):
    assert fixture["contrib"].shape == (N, len(NAMES) + 1)
    with_bias = W.percent_contributions(fixture["contrib"], NAMES)
    without_bias = W.percent_contributions(fixture["contrib"][:, :-1], NAMES)
    pd.testing.assert_frame_equal(with_bias, without_bias)


def test_percent_sums_to_100_per_row(fixture):
    df = W.percent_contributions(fixture["contrib"], NAMES)
    assert list(df.columns) == ["row", "indicator", "feature", "group", "percent", "direction"]
    assert set(df["row"]) == set(range(N))
    assert (df.groupby("row").size() == _n_indicators()).all()
    sums = df.assign(a=df["percent"].abs()).groupby("row")["a"].sum()
    assert np.allclose(sums.to_numpy(), 100.0, atol=1e-6)
    assert set(df["direction"]) <= {"+", "−"}
    assert ((df["direction"] == "−") == (df["percent"] < 0)).all()
    assert df["percent"].round(1).equals(df["percent"])


def test_rows_sorted_by_abs_percent(fixture):
    df = W.percent_contributions(fixture["contrib"], NAMES)
    for _, g in df.groupby("row"):
        a = g["percent"].abs().to_numpy()
        assert (np.diff(a) <= 1e-9).all()


def test_zero_row_gives_zeros():
    contrib = np.zeros((2, 4))
    df = W.percent_contributions(contrib, ["age_days_log", "gap_cv", "cross_dup_ratio", "narrative_ratio"])
    assert (df["percent"] == 0).all()


def test_context_features_never_shown_by_name(fixture):
    df = W.percent_contributions(fixture["contrib"], NAMES)
    shown = " ".join(df["indicator"].astype(str).tolist() + df["feature"].astype(str).tolist()).lower()
    for name in CONTEXT_NAMES + CONTEXT_LABELS:
        assert name.lower() not in shown
    assert "narrative" not in shown and "intent" not in shown
    ctx = df[df["group"] == "context"]
    assert set(ctx["indicator"]) == {W.CONTEXT_LABEL}
    assert ctx.groupby("row").size().eq(1).all()


def test_top_indicators(fixture):
    df = W.percent_contributions(fixture["contrib"], NAMES)
    top = W.top_indicators(df, 3, k=5)
    assert len(top) == 5
    assert set(top[0]) == {"indicator", "percent", "direction"}
    abs_vals = [abs(t["percent"]) for t in top]
    assert abs_vals == sorted(abs_vals, reverse=True)
    assert len(W.top_indicators(df, 3)) == _n_indicators()


def test_global_importance(fixture):
    g = W.global_importance(fixture["contrib"], NAMES)
    assert list(g.columns) == ["indicator", "feature", "group", "importance_pct", "pushes_bot_pct"]
    assert abs(g["importance_pct"].sum() - 100.0) < 1e-6
    assert g["pushes_bot_pct"].between(0, 100).all()
    assert "narrative" not in " ".join(g["indicator"]).lower()
    assert (g[g["group"] == "context"]["indicator"] == W.CONTEXT_LABEL).all()


def test_relation_weights():
    rw = W.relation_weights({"COPY": 0.6, "SYNC": 0.2, "LINK": -0.5, "REPLY": 0.0})
    assert list(rw.columns) == ["relation", "weight", "weight_pct"]
    assert abs(rw["weight_pct"].sum() - 100.0) < 1e-6
    assert rw.set_index("relation").loc["LINK", "weight_pct"] == 0.0
    assert rw.iloc[0]["relation"] == "COPY"


def test_export_writes_all_files(fixture, tmp_path):
    df = W.percent_contributions(fixture["contrib"], NAMES)
    gi = W.global_importance(fixture["contrib"], NAMES)
    rw = W.relation_weights({"COPY": 0.9, "SYNC": 0.3, "LINK": 0.5, "REPLY": 0.0, "CO_THREAD": 0.1})
    paths = W.export_neo4j(tmp_path, fixture["scored"], fixture["graph"], df, gi, rw)

    expected = ["nodes_account.csv", "rels_account.csv", "nodes_indicator.csv", "rels_contrib.csv", "import.cypher"]
    for name in expected:
        assert (tmp_path / name).is_file(), name
        assert name in paths
    cypher = (tmp_path / "import.cypher").read_text(encoding="utf-8")
    for csv in expected[:-1]:
        assert f"file:///{csv}" in cypher
    assert "CREATE CONSTRAINT" in cypher and "Verdict {name: 'BOT'}" in cypher
    for rel in ("COPY", "SYNC", "LINK", "REPLY", "CO_THREAD"):
        assert f"[r:{rel}]" in cypher
    assert "EVIDENCE" in cypher and "WEIGHT" in cypher

    nodes = pd.read_csv(tmp_path / "nodes_account.csv", dtype=str, keep_default_na=False)
    assert list(nodes.columns) == ["account_id", "bot_probability", "verdict", "family", "label"]
    assert len(nodes) == N
    assert set(nodes["verdict"]) <= {"BOT", "NOT BOT"}
    assert (nodes["label"] == "").any()

    rels = pd.read_csv(tmp_path / "rels_account.csv")
    assert list(rels.columns) == ["src", "dst", "type", "weight", "learned_weight"]
    gates = {"COPY": 0.9, "SYNC": 0.3, "LINK": 0.5, "REPLY": 0.0, "CO_THREAD": 0.1}
    assert np.allclose(rels["learned_weight"], rels["weight"] * rels["type"].map(gates))
    assert (rels["src"] < rels["dst"]).all()
    expected_edges = sum(
        int(((m.tocoo().row < m.tocoo().col) & (m.tocoo().data != 0)).sum())
        for m in fixture["graph"].relations.values()
    )
    assert len(rels) == expected_edges

    contrib_csv = pd.read_csv(tmp_path / "rels_contrib.csv")
    assert list(contrib_csv.columns) == ["account_id", "indicator", "percent", "direction"]
    assert contrib_csv.groupby("account_id").size().max() <= W.TOP_PER_ACCOUNT
    assert contrib_csv["account_id"].nunique() == N

    indic = pd.read_csv(tmp_path / "nodes_indicator.csv")
    assert list(indic.columns) == ["indicator", "group", "importance_pct", "pushes_bot_pct"]

    for name in expected:
        text = (tmp_path / name).read_text(encoding="utf-8").lower()
        for banned in CONTEXT_NAMES + [l.lower() for l in CONTEXT_LABELS] + ["narrative", "intent"]:
            assert banned not in text, (name, banned)


def test_export_rejects_bad_scored(fixture, tmp_path):
    df = W.percent_contributions(fixture["contrib"], NAMES)
    gi = W.global_importance(fixture["contrib"], NAMES)
    with pytest.raises(ValueError):
        W.export_neo4j(tmp_path, fixture["scored"].drop(columns=["verdict"]), fixture["graph"], df, gi, {"COPY": 1.0})
