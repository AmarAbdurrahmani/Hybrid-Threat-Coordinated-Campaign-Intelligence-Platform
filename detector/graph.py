"""Account graph (the knowledge graph the GNN learns on, and what gets exported to Neo4j).

Nodes: accounts. Typed, weighted relations between accounts:
  COPY      shared a near-duplicate post (same normalised template)
  SYNC      posted the same template within 10 minutes of each other (synchronised copy-paste)
  LINK      posted the same URL
  REPLY     one replied to the other
  CO_THREAD posted in the same thread within 2 minutes of each other (YouTube / forum threads)
Very common templates/URLs (posted by > CAP accounts) are skipped: they carry no coordination signal.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from itertools import combinations

import numpy as np
import pandas as pd
import scipy.sparse as sp
import scipy.sparse.csgraph  # noqa: F401  (registers sp.csgraph)

from .features import add_post_columns

RELATIONS = ("COPY", "SYNC", "LINK", "REPLY", "CO_THREAD")
CAP = 200
SYNC_WINDOW = 600
THREAD_WINDOW = 120


@dataclass
class Graph:
    ids: list[str]
    relations: dict[str, sp.csr_matrix] = field(default_factory=dict)

    @property
    def index(self) -> dict[str, int]:
        return {a: i for i, a in enumerate(self.ids)}


def _add(acc: dict, a: int, b: int, w: float = 1.0) -> None:
    if a != b:
        key = (a, b) if a < b else (b, a)
        acc[key] += w


def build_graph(accounts: pd.DataFrame, posts: pd.DataFrame) -> Graph:
    ids = sorted(set(accounts["account_id"].astype(str)) | set(posts["account_id"].astype(str)))
    ix = {a: i for i, a in enumerate(ids)}
    p = add_post_columns(posts)
    p["ai"] = p["account_id"].astype(str).map(ix)
    edges = {r: defaultdict(float) for r in RELATIONS}

    for _, g in p[p.tpl != ""].groupby("tpl"):
        accs = g["ai"].unique()
        if len(accs) < 2 or len(accs) > CAP:
            continue
        for a, b in combinations(accs, 2):
            _add(edges["COPY"], a, b)
        g = g.sort_values("ts")
        t, a = g["ts"].to_numpy(float), g["ai"].to_numpy()
        j = 0
        for i in range(len(t)):  # sliding window: pairs of different accounts within SYNC_WINDOW
            while t[i] - t[j] > SYNC_WINDOW:
                j += 1
            for k in range(j, i):
                _add(edges["SYNC"], a[i], a[k])

    urls = p[p.urls != ""][["ai", "urls"]]
    by_url = defaultdict(set)
    for ai, us in zip(urls["ai"], urls["urls"]):
        for u in set(us.split()):
            by_url[u.rstrip("/").lower()].add(ai)
    for accs in by_url.values():
        if 2 <= len(accs) <= CAP:
            for a, b in combinations(sorted(accs), 2):
                _add(edges["LINK"], a, b)

    owner = dict(zip(p["post_id"].astype(str), p["ai"]))
    for ai, par in zip(p["ai"], p["parent_id"]):
        if par and par in owner:
            _add(edges["REPLY"], ai, owner[par])

    if "thread_id" in p:
        th = p[p["thread_id"].fillna("").astype(str) != ""]
        for _, g in th.groupby("thread_id"):
            if g["ai"].nunique() < 2:
                continue
            g = g.sort_values("ts"); t, a = g["ts"].to_numpy(float), g["ai"].to_numpy(); j = 0
            for i in range(len(t)):
                while t[i] - t[j] > THREAD_WINDOW:
                    j += 1
                for k in range(max(j, i - 50), i):
                    _add(edges["CO_THREAD"], a[i], a[k])

    n = len(ids)
    rel = {}
    for r, acc in edges.items():
        if acc:
            (rows, cols), w = zip(*acc.keys()), np.fromiter(acc.values(), float)
            rows, cols = np.array(rows), np.array(cols)
            m = sp.coo_matrix((w, (rows, cols)), shape=(n, n))
            rel[r] = (m + m.T).tocsr()
        else:
            rel[r] = sp.csr_matrix((n, n))
    return Graph(ids=ids, relations=rel)


NETWORK_FEATURES = {
    "deg_copy_log": ("Accounts it copy-pastes with (log)", "network"),
    "deg_sync_log": ("Accounts it posts in sync with (log)", "network"),
    "deg_link_log": ("Accounts sharing its links (log)", "network"),
    "deg_reply_log": ("Reply partners (log)", "network"),
    "deg_cothread_log": ("Accounts active with it in the same minutes (log)", "network"),
    "component_log": ("Size of its coordinated group (log)", "network"),
}


def network_features(graph: Graph) -> pd.DataFrame:
    R = graph.relations
    deg = lambda r: np.log1p(np.asarray((R[r] > 0).sum(axis=1)).ravel())
    coord = (R["COPY"] + R["SYNC"] + R["LINK"])
    _, comp = sp.csgraph.connected_components(coord > 0, directed=False)
    size = np.bincount(comp)[comp]
    return pd.DataFrame({
        "deg_copy_log": deg("COPY"), "deg_sync_log": deg("SYNC"), "deg_link_log": deg("LINK"),
        "deg_reply_log": deg("REPLY"), "deg_cothread_log": deg("CO_THREAD"), "component_log": np.log1p(size - 1),
    }, index=pd.Index(graph.ids, name="account_id"))
