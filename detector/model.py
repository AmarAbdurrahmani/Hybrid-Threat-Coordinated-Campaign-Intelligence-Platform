"""Training, calibration, persistence and scoring.

final probability = isotonic( mean( LightGBM probability, sigmoid(GNN logit) ) ), calibrated on a validation split.
Indicator percentages come from LightGBM's exact per-prediction contributions (TreeSHAP, `pred_contrib=True`).
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from .features import FEATURES, account_features
from .graph import NETWORK_FEATURES, RELATIONS, build_graph, network_features
from . import gnn as G

MODEL_DIR = Path(__file__).resolve().parent / "model"
FEATURE_ORDER = list(FEATURES) + list(NETWORK_FEATURES)
# Feature profiles: a model may only use what the target platform exposes *with the same meaning*. YouTube gives
# channel age, but no "following" count and no lifetime post count, and its subscriber count means something else
# (ordinary commenters have 0 subscribers because they never upload), so followers is excluded too.
PROFILES = {
    "full": FEATURE_ORDER,
    # Account age is excluded as well: in the benchmark it mostly encodes *when* each group was collected
    # (genuine accounts are years older), which would flag every young YouTube channel.
    "youtube": [f for f in FEATURE_ORDER if f not in ("followers_log", "following_log", "follow_ratio", "posts_total_log",
                                                      "posts_per_day_log", "age_days_log")],
}
LGB_PARAMS = dict(objective="binary", learning_rate=0.05, num_leaves=31, min_child_samples=20, subsample=0.8,
                  subsample_freq=1, colsample_bytree=0.8, reg_lambda=1.0, verbose=-1, seed=0, deterministic=True)


def design(accounts: pd.DataFrame, posts: pd.DataFrame):
    """Feature matrix (rows aligned to graph.ids) + graph."""
    graph = build_graph(accounts, posts)
    f = account_features(accounts, posts).join(network_features(graph), how="right")
    f = f.reindex(graph.ids)
    return f[FEATURE_ORDER].astype(float), graph


def _sig(z):
    return 1 / (1 + np.exp(-z))


class Iso:
    """Isotonic calibration stored as two arrays (np.interp at inference, no sklearn needed)."""

    def __init__(self, x=None, y=None):
        self.x, self.y = x, y

    def fit(self, s, y):
        from sklearn.isotonic import IsotonicRegression
        ir = IsotonicRegression(out_of_bounds="clip", y_min=0.0, y_max=1.0).fit(s, y)
        self.x, self.y = ir.X_thresholds_, ir.y_thresholds_
        return self

    def __call__(self, s):
        return np.interp(s, self.x, self.y)


def fit(X: pd.DataFrame, graph, y: np.ndarray, train_idx, val_idx, *, gnn_epochs=400, seed=0, features=None) -> dict:
    import lightgbm as lgb
    features = list(features or FEATURE_ORDER); X = X[features]
    dtr = lgb.Dataset(X.iloc[train_idx], y[train_idx], feature_name=features, free_raw_data=False)
    dva = lgb.Dataset(X.iloc[val_idx], y[val_idx], reference=dtr)
    booster = lgb.train({**LGB_PARAMS, "seed": seed}, dtr, num_boost_round=600, valid_sets=[dva],
                        callbacks=[lgb.early_stopping(50, verbose=False)])
    gm = G.train_gnn(X.to_numpy(), graph.relations, np.nan_to_num(y, nan=0.0), train_idx, val_idx, epochs=gnn_epochs, seed=seed)
    p_l = booster.predict(X.to_numpy(), num_iteration=booster.best_iteration)
    p_g = _sig(G.numpy_forward(gm, X.to_numpy(), graph.relations))
    raw = (p_l + p_g) / 2
    iso = Iso().fit(raw[val_idx], y[val_idx])
    from .evaluate import best_threshold
    thr = float(best_threshold(y[val_idx], iso(raw[val_idx]), metric="f1"))
    return {"booster": booster, "gnn": gm, "iso": iso, "threshold": thr, "features": features,
            "parts": {"lightgbm": p_l, "gnn": p_g, "ensemble": iso(raw)}}


def predict(m: dict, X: pd.DataFrame, graph) -> dict:
    X = X[m.get("features") or FEATURE_ORDER]
    p_l = m["booster"].predict(X.to_numpy(), num_iteration=m["booster"].best_iteration)
    p_g = _sig(G.numpy_forward(m["gnn"], X.to_numpy(), graph.relations))
    contrib = m["booster"].predict(X.to_numpy(), num_iteration=m["booster"].best_iteration, pred_contrib=True)
    return {"lightgbm": p_l, "gnn": p_g, "ensemble": m["iso"]((p_l + p_g) / 2), "contrib": contrib}


def save(m: dict, meta: dict, out: Path = MODEL_DIR) -> None:
    out.mkdir(parents=True, exist_ok=True)
    m["booster"].save_model(str(out / "lightgbm.txt"), num_iteration=m["booster"].best_iteration)
    gm = m["gnn"]
    np.savez_compressed(out / "gnn.npz", **{"p_" + k: v for k, v in gm["params"].items()},
                        **{"s_" + k: v for k, v in gm["scaler"].items()})
    (out / "model.json").write_text(json.dumps({**meta, "features": m["features"], "relations": list(RELATIONS),
        "relation_trained": gm["relation_trained"], "relation_gates": G.relation_gates(gm), "threshold": m["threshold"],
        "isotonic": {"x": list(map(float, m["iso"].x)), "y": list(map(float, m["iso"].y))}}, indent=2))


def load(out: Path = MODEL_DIR) -> dict:
    out = Path(out)
    import lightgbm as lgb
    meta = json.loads((out / "model.json").read_text())
    z = np.load(out / "gnn.npz")
    gm = {"params": {k[2:]: z[k] for k in z.files if k.startswith("p_")},
          "scaler": {k[2:]: z[k] for k in z.files if k.startswith("s_")}, "relations": meta["relations"],
          "relation_trained": meta.get("relation_trained", {})}
    # read as text and normalise line endings: a CRLF checkout (git autocrlf on Windows) makes LightGBM abort
    booster = lgb.Booster(model_str=(out / "lightgbm.txt").read_bytes().decode("utf-8").replace("\r\n", "\n"))
    booster.best_iteration = booster.current_iteration()
    return {"booster": booster, "gnn": gm, "iso": Iso(np.array(meta["isotonic"]["x"]), np.array(meta["isotonic"]["y"])),
            "threshold": meta["threshold"], "meta": meta, "features": meta["features"]}


def prior_shift(p: np.ndarray, train_prior: float, target_prior: float) -> np.ndarray:
    """Re-weight calibrated probabilities from the training bot share to the target population's (Saerens et al., 2002).
    The benchmark is ~89% bots; real commenters are mostly human, so without this every weak case drifts to BOT."""
    r1, r0 = target_prior / train_prior, (1 - target_prior) / (1 - train_prior)
    return p * r1 / (p * r1 + (1 - p) * r0)


def score(accounts: pd.DataFrame, posts: pd.DataFrame, m: dict | None = None, prior: float | None = None
          ) -> tuple[list[dict], pd.DataFrame, object, np.ndarray]:
    """BOT probability + indicator percentages for every account. Returns (records, scored_df, graph, contrib).
    prior = expected bot share in the scored population (e.g. 0.10). With a prior, the verdict is the Bayes
    decision (BOT if p >= 50%); without one, the validation threshold of the benchmark is used."""
    from .weights import percent_contributions, top_indicators
    m = m or load()
    X, graph = design(accounts, posts)
    out = predict(m, X, graph)
    pct = percent_contributions(out["contrib"], m["features"])
    thr = m["threshold"]
    if prior is not None:
        out["ensemble"] = prior_shift(np.clip(out["ensemble"], 1e-6, 1 - 1e-6), m["meta"].get("train_prior", 0.5), prior)
        thr = 0.5
    meta = accounts.set_index("account_id").reindex(graph.ids)
    recs = []
    for i, aid in enumerate(graph.ids):
        p = float(out["ensemble"][i])
        recs.append({"account_id": aid, "bot_probability": round(100 * p, 1), "verdict": "BOT" if p >= thr else "NOT BOT",
                     "threshold": round(100 * thr, 1), **({"base_rate": round(100 * prior, 1)} if prior is not None else {}),
                     "indicators": top_indicators(pct, i)})
    scored = pd.DataFrame({"account_id": graph.ids, "bot_probability": [r["bot_probability"] for r in recs],
                           "verdict": [r["verdict"] for r in recs], "family": meta.get("family", pd.Series(index=graph.ids)).fillna("").to_numpy(),
                           "label": meta.get("label", pd.Series(index=graph.ids)).to_numpy()})
    return recs, scored, graph, out["contrib"]
