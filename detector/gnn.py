"""Relational graph network over the account graph (R-GCN style) with one learned gate per relation type.

    h1 = relu(X·W0 + Σ_r g_r · Â_r · X · W_r)
    z  = h1·v0 + Σ_r g_r · Â_r · h1 · v_r + b          → bot logit
    g_r = softplus(θ_r)                                 → learned weight of relation r (exported to Neo4j)

Â_r = row-normalised log1p(edge weights). Training uses torch; inference is plain numpy (`numpy_forward`), so the
deployed API needs no torch.
"""
from __future__ import annotations

import numpy as np
import scipy.sparse as sp

from .graph import RELATIONS


def norm_adj(m: sp.csr_matrix) -> sp.csr_matrix:
    m = m.copy().astype(float); m.data = np.log1p(m.data)
    deg = np.asarray(m.sum(axis=1)).ravel(); deg[deg == 0] = 1
    return sp.diags(1 / deg) @ m


class Scaler:
    def __init__(self, med=None, mu=None, sd=None):
        self.med, self.mu, self.sd = med, mu, sd

    def fit(self, X: np.ndarray):
        self.med = np.nanmedian(X, axis=0); self.med = np.where(np.isfinite(self.med), self.med, 0.0)
        Z = np.where(np.isfinite(X), X, self.med)
        self.mu, self.sd = Z.mean(0), Z.std(0) + 1e-6
        return self

    def transform(self, X: np.ndarray) -> np.ndarray:
        miss = (~np.isfinite(X)).astype(float)
        Z = (np.where(np.isfinite(X), X, self.med) - self.mu) / self.sd
        return np.hstack([Z, miss])  # missingness is itself a signal (e.g. hidden subscriber count)


def train_gnn(X: np.ndarray, rels: dict, y: np.ndarray, train_idx, val_idx, *, hidden=32, epochs=400, lr=0.01,
              wd=5e-4, seed=0, patience=40) -> dict:
    import torch
    torch.manual_seed(seed); np.random.seed(seed)
    sc = Scaler().fit(X[train_idx]); Xt = torch.tensor(sc.transform(X), dtype=torch.float32)
    A = {}
    for r in RELATIONS:
        a = norm_adj(rels[r]).tocoo()
        A[r] = torch.sparse_coo_tensor(np.vstack([a.row, a.col]), a.data.astype(np.float32), a.shape).coalesce()
    d = Xt.shape[1]
    P = {"W0": torch.nn.Parameter(torch.randn(d, hidden) * (1 / d) ** .5), "b1": torch.nn.Parameter(torch.zeros(hidden)),
         "v0": torch.nn.Parameter(torch.randn(hidden, 1) * (1 / hidden) ** .5), "b2": torch.nn.Parameter(torch.zeros(1)),
         "theta": torch.nn.Parameter(torch.zeros(len(RELATIONS)))}
    for r in RELATIONS:
        P["W_" + r] = torch.nn.Parameter(torch.randn(d, hidden) * (1 / d) ** .5 * .5)
        P["v_" + r] = torch.nn.Parameter(torch.randn(hidden, 1) * (1 / hidden) ** .5 * .5)
    opt = torch.optim.Adam(P.values(), lr=lr, weight_decay=wd)
    yt = torch.tensor(y, dtype=torch.float32)
    tr, va = torch.tensor(train_idx), torch.tensor(val_idx)
    pos = float(np.nansum(y[train_idx])); neg = len(train_idx) - pos
    lossf = torch.nn.BCEWithLogitsLoss(pos_weight=torch.tensor(neg / max(pos, 1)))

    def fwd():
        g = torch.nn.functional.softplus(P["theta"])
        h = Xt @ P["W0"] + P["b1"]
        for i, r in enumerate(RELATIONS):
            h = h + g[i] * torch.sparse.mm(A[r], Xt @ P["W_" + r])
        h = torch.relu(h)
        z = h @ P["v0"] + P["b2"]
        for i, r in enumerate(RELATIONS):
            z = z + g[i] * torch.sparse.mm(A[r], h @ P["v_" + r])
        return z.squeeze(1)

    best, best_state, wait = float("inf"), None, 0
    for _ in range(epochs):
        opt.zero_grad(); z = fwd(); loss = lossf(z[tr], yt[tr]); loss.backward(); opt.step()
        with torch.no_grad():
            vl = float(lossf(fwd()[va], yt[va]))
        if vl < best - 1e-4:
            best, wait = vl, 0; best_state = {k: v.detach().clone() for k, v in P.items()}
        else:
            wait += 1
            if wait > patience:
                break
    W = {k: v.numpy() for k, v in best_state.items()}
    trained = {r: bool(rels[r][train_idx].nnz) for r in RELATIONS}
    return {"params": W, "scaler": {"med": sc.med, "mu": sc.mu, "sd": sc.sd}, "relations": list(RELATIONS),
            "relation_trained": trained, "val_loss": best}


def relation_gates(model: dict) -> dict[str, float]:
    th = model["params"]["theta"]
    return {r: float(np.log1p(np.exp(t))) for r, t in zip(model["relations"], th)}


def numpy_forward(model: dict, X: np.ndarray, rels: dict) -> np.ndarray:
    """Bot logit for every node, numpy only."""
    s = model["scaler"]; Xs = Scaler(s["med"], s["mu"], s["sd"]).transform(X)
    P, g = model["params"], np.log1p(np.exp(model["params"]["theta"]))
    A = {r: norm_adj(rels[r]) for r in model["relations"]}
    h = Xs @ P["W0"] + P["b1"]
    for i, r in enumerate(model["relations"]):
        h = h + g[i] * (A[r] @ (Xs @ P["W_" + r]))
    h = np.maximum(h, 0)
    z = h @ P["v0"] + P["b2"]
    for i, r in enumerate(model["relations"]):
        z = z + g[i] * (A[r] @ (h @ P["v_" + r]))
    return z.ravel()
