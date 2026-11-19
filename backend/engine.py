"""Detection engine: Temporal · Linguistic · Narrative (semantic) · Network (graph).

    risk = 0.30·Temporal + 0.25·Linguistic + 0.25·Narrative + 0.20·Network
    (weights renormalised over the signals that can be computed for a comment)

The output of `analyze_thread()` follows the same data format the tactical
dashboard's client-side engine produces ("contract v1"), so the dashboard can
render backend results without changes to its panels. All times are epoch ms.

The score measures *coordination*. It does not identify who is behind it.
"""
from __future__ import annotations

import math
import statistics
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Iterable

import networkx as nx
import numpy as np

from lexicons import (CALQUES, DOUBLETS, GEG_MARKERS, STD_MARKERS, STOP, TROPES, content_set, detect_lang, fold,
                      tokens_with_pos)

CONTRACT_VERSION = "1"
WEIGHTS = {"temporal": 0.30, "linguistic": 0.25, "narrative": 0.25, "network": 0.20}
TIERS = {"high": 60, "medium": 35}
DAY_MS = 86_400_000
ALERT_LEVEL_NAMES = {1: "LEVEL 1 (Strategic Threat)", 2: "LEVEL 2 (Operational Threat)", 3: "LEVEL 3 (Tactical Watch)", 4: "LEVEL 4 (Baseline)"}


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def to_ms(ts: Any) -> float:
    if ts is None:
        return math.nan
    if isinstance(ts, (int, float)):
        return float(ts)
    if isinstance(ts, datetime):
        dt = ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)
        return dt.timestamp() * 1000
    s = str(ts).strip()
    if not s:
        return math.nan
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return math.nan
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.timestamp() * 1000


def clamp(x: float, lo: float = 0, hi: float = 100) -> float:
    return max(lo, min(hi, x))


def tier_of(score: float) -> str:
    return "high" if score >= TIERS["high"] else "medium" if score >= TIERS["medium"] else "organic"


def jaccard(a: set, b: set) -> float:
    if not a or not b:
        return 0.0
    i = len(a & b)
    return i / (len(a) + len(b) - i)


def poisson_sf(k: int, lam: float) -> float:
    """P(X >= k) for X ~ Poisson(lam)."""
    if k <= 0:
        return 1.0
    term, cdf = math.exp(-lam), 0.0
    for i in range(k):
        cdf += term
        term *= lam / (i + 1)
    return max(0.0, 1.0 - cdf)


def _matches(rx, text: str):
    return [(m.start(), m.end(), m.group(0)) for m in rx.finditer(text)]


# ---------------------------------------------------------------------------
# Cross-thread co-activity graph (NetworkX)
# ---------------------------------------------------------------------------
@dataclass
class CoActivity:
    graph: nx.Graph
    communities: dict[str, int] = field(default_factory=dict)
    window_s: int = 300
    min_threads: int = 2

    def partners(self, user: str) -> list[tuple[str, int]]:
        if user not in self.graph:
            return []
        return sorted(((v, d["threads"]) for v, d in self.graph[user].items() if d["threads"] >= self.min_threads), key=lambda x: -x[1])

    def to_json(self, scores: dict[str, float] | None = None) -> dict:
        scores = scores or {}
        nodes = [{"id": n, "community": self.communities.get(n), "degree": int(self.graph.degree(n)),
                  "score": scores.get(n), "tier": tier_of(scores[n]) if n in scores else None} for n in self.graph.nodes]
        edges = [{"a": a, "b": b, "type": "coactivity", "w": d["threads"], "threads": sorted(d["thread_ids"])}
                 for a, b, d in self.graph.edges(data=True) if d["threads"] >= self.min_threads]
        return {"nodes": nodes, "edges": edges, "window_s": self.window_s, "min_threads": self.min_threads}


def build_coactivity(comments: Iterable[dict], window_s: int = 300, min_threads: int = 2) -> CoActivity:
    """Link accounts that comment within `window_s` of each other; an edge's weight is
    the number of DISTINCT threads where that happened. Communities: Louvain on edges
    that meet `min_threads` (a single shared thread is too weak to call coordination)."""
    by_thread: dict[str, list[tuple[float, str]]] = defaultdict(list)
    for c in comments:
        t = to_ms(c.get("ts"))
        if not math.isnan(t):
            by_thread[c["thread_id"]].append((t, c["user"]))
    g = nx.Graph()
    w_ms = window_s * 1000
    for tid, evs in by_thread.items():
        evs.sort()
        pairs = set()
        j = 0
        for i in range(len(evs)):  # two-pointer sliding window: O(n·k)
            while evs[i][0] - evs[j][0] > w_ms:
                j += 1
            for k in range(j, i):
                a, b = evs[k][1], evs[i][1]
                if a != b:
                    pairs.add((a, b) if a < b else (b, a))
        for a, b in pairs:
            if g.has_edge(a, b):
                g[a][b]["thread_ids"].add(tid)
            else:
                g.add_edge(a, b, thread_ids={tid})
    for a, b, d in g.edges(data=True):
        d["threads"] = len(d["thread_ids"])
        d["weight"] = d["threads"]
    strong = nx.Graph([(a, b, d) for a, b, d in g.edges(data=True) if d["threads"] >= min_threads])
    comm: dict[str, int] = {}
    if strong.number_of_edges():
        for i, members in enumerate(nx.community.louvain_communities(strong, weight="weight", seed=42)):
            for m in members:
                comm[m] = i
    return CoActivity(graph=g, communities=comm, window_s=window_s, min_threads=min_threads)


# ---------------------------------------------------------------------------
# Thread analysis
# ---------------------------------------------------------------------------
def analyze_thread(comments: list[dict], *, published: Any = None, embedder=None, coactivity: CoActivity | None = None,
                   llm_flags: dict[str, dict] | None = None, infra_meta: dict[str, dict] | None = None) -> dict:
    """Score one thread. `comments`: dicts with id, user, ts, text and optional
    source_text, account{created, followers, tagged_photos, friends}, infra{node, type, asn, ip, city, cc, lat, lon}."""
    llm_flags = llm_flags or {}
    infra_meta = infra_meta or {}
    items = []
    for c in comments:
        text = c.get("text") or ""
        lang = detect_lang(text)
        sq_text = c.get("source_text") or (text if lang == "sq" else None)
        f = fold(text)
        toks = tokens_with_pos(f)
        items.append({
            "c": {k: c.get(k) for k in ("id", "user", "ts", "text", "source_text", "account", "infra", "platform", "thread_id", "simulated", "url")},
            "t": to_ms(c.get("ts")), "f": f, "toks": toks, "set": content_set(toks), "lang": lang,
            "sf": fold(sq_text) if sq_text else None, "same": not c.get("source_text"), "spans": [],
        })
    for it in items:
        it["c"]["source"] = "simulated" if it["c"].get("simulated") else (it["c"].get("platform") or "live")
    timed = [it for it in items if not math.isnan(it["t"])]
    pub = to_ms(published) if published is not None else (min(i["t"] for i in timed) if timed else math.nan)
    if math.isnan(pub) and timed:
        pub = min(i["t"] for i in timed)

    _temporal(items, timed, pub)
    _linguistic(items, llm_flags)
    _narrative(items, embedder)
    _network(items, coactivity, pub)
    _fuse(items)
    res = _assemble(items, timed, pub, coactivity, infra_meta)
    return res


# ---- 1. TEMPORAL -------------------------------------------------------------
def _temporal(items, timed, pub):
    last = max((i["t"] for i in timed), default=pub)
    dur_min = max(60.0, (last - pub) / 60000) if timed else 60.0
    expected = max(1.0, len(timed) * 15 / dur_min)
    times = sorted(i["t"] for i in timed)
    import bisect

    for it in items:
        if math.isnan(it["t"]):
            it["temporal"], it["tReasons"] = None, ["No timestamp: temporal signal skipped"]
            continue
        t = it["t"]
        win = bisect.bisect_right(times, t + 7.5 * 60000) - bisect.bisect_left(times, t - 7.5 * 60000)
        micro = bisect.bisect_right(times, t + 120000) - bisect.bisect_left(times, t - 120000)
        idx = bisect.bisect_left(times, t)
        neigh = [abs(times[k] - t) for k in (idx - 1, idx + 1) if 0 <= k < len(times)]
        dupes_at_t = bisect.bisect_right(times, t) - bisect.bisect_left(times, t)
        gap = 0.0 if dupes_at_t > 1 else (min(neigh) if neigh else math.inf)
        ratio = win / expected
        p = poisson_sf(win, expected)
        s = clamp((ratio - 1.5) / 2.5 * 70, 0, 70)
        r = [f"{win} comments in its 15-min window vs {expected:.1f} expected (×{ratio:.1f}, Poisson p={p:.1e})"]
        if gap <= 60000:
            s += 20; r.append(f"Nearest neighbour only {round(gap / 1000)}s away")
        elif gap <= 120000:
            s += 10; r.append(f"Nearest neighbour {round(gap / 1000)}s away")
        if micro >= 3:
            s += min(30, (micro - 1) * 10); r.append(f"{micro} comments within ±2 min (micro-burst)")
        since = (t - pub) / 60000
        if 0 <= since <= 15 and win >= 3:
            s += 10; r.append(f"Posted {since:.1f} min after publication, inside a cluster")
        it["temporal"], it["tReasons"], it["window"] = round(clamp(s)), r, win
    for it in items:
        it["_expected"] = expected


# ---- 2. LINGUISTIC -------------------------------------------------------------
def _linguistic(items, llm_flags):
    tri_users: dict[str, set] = defaultdict(set)
    for it in items:
        it["tris"] = []
        tk = it["toks"]
        for k in range(len(tk) - 2):
            g = tk[k:k + 3]
            if sum(1 for w, _, _ in g if len(w) >= 3 and w not in STOP) < 2:
                continue
            key = " ".join(w for w, _, _ in g)
            it["tris"].append((key, g[0][1], g[2][2]))
            tri_users[key].add(it["c"]["user"])
    for it in items:
        r, s = [], 0.0
        text, sf = it["c"]["text"] or "", it["sf"]
        doublets = calques = 0
        if sf:
            for d in DOUBLETS:
                g, st = _matches(d["geg_rx"], sf), _matches(d["std_rx"], sf)
                if g and st:
                    doublets += 1; s += 35
                    r.append(f"Dialect doublet {d['label']} inside one comment" + ("" if it["same"] else " (original text)"))
                    if it["same"]:
                        for a, b, _ in g + st:
                            it["spans"].append({"s": a, "e": b, "type": "dialect", "note": f"Geg/standard doublet {d['label']}: the same word in two dialect forms."})
            for cq in CALQUES:
                for a, b, _ in _matches(cq["rx"], sf):
                    calques += 1; s += 30
                    if it["same"]:
                        r.append(f"Serbian calque “{text[a:b]}” ← Serbian “{cq['src']}” (natural: “{cq['natural']}”)")
                        it["spans"].append({"s": a, "e": b, "type": "calque", "note": f"Word-for-word transfer of Serbian “{cq['src']}”."})
                    else:
                        r.append(f"Serbian calque in original “{cq['sq']}” ← Serbian “{cq['src']}” (natural: “{cq['natural']}”)")
                        em = _matches(cq["en_rx"], it["f"])
                        if em:
                            it["spans"].append({"s": em[0][0], "e": em[0][1], "type": "calque", "note": f"Literal rendering of Albanian “{cq['sq']}”, a word-for-word copy of Serbian “{cq['src']}”."})
        else:
            for cq in CALQUES:
                for a, b, _ in _matches(cq["en_rx"], it["f"]):
                    calques += 1; s += 30
                    r.append(f"Literal-translation artifact “{text[a:b]}” (matches Serbian “{cq['src']}”; natural: “{cq['natural']}”)")
                    it["spans"].append({"s": a, "e": b, "type": "calque", "note": f"Literal-translation artifact matching Serbian “{cq['src']}”."})
        shared = [x for x in it["tris"] if any(u != it["c"]["user"] for u in tri_users[x[0]])]
        uniq = sorted({x[0] for x in shared})
        if uniq:
            s += min(30, len(uniq) * 10)
            r.append(f"{len(uniq)} word-trigram(s) reused verbatim by other accounts")
            for key, a, b in shared:
                others = ", ".join(sorted(u for u in tri_users[key] if u != it["c"]["user"])[:6])
                it["spans"].append({"s": a, "e": b, "type": "ngram", "note": f"Trigram “{key}” also posted by: {others}"})
        lf = llm_flags.get(it["c"]["id"])
        if lf and lf.get("score"):
            s += min(30, 0.3 * float(lf["score"]))
            r.append("LLM review: " + "; ".join(lf.get("findings", [])[:3]))
        geg = len(GEG_MARKERS.findall(sf)) if sf else 0
        std = len(STD_MARKERS.findall(sf)) if sf else 0
        profile = "n/a (non-Albanian input)" if not sf else "Geg" if geg and not std else "Standard/Tosk" if std and not geg else "Geg + Standard" if geg and std else "Neutral"
        it["dialect"] = {"geg": geg, "std": std, "profile": profile}
        if not r:
            r.append("No dialect doublets, calques or reused n-grams" if sf else "No literal-translation artifacts or reused n-grams (dialect checks need Albanian text)")
        it["linguistic"], it["lReasons"], it["doublets"], it["calques"] = round(clamp(s)), r, doublets, calques


# ---- 3. NARRATIVE (lexicon + multilingual semantic similarity) -----------------
def _narrative(items, embedder):
    n = len(items)
    ex_keys, ex_texts = [], []
    for k, t in TROPES.items():
        for e in t["exemplars"]:
            ex_keys.append(k); ex_texts.append(e)
    sims = None
    ex_sims = None
    thr = {"near_dup": 0.6, "narrative": 1.1}
    if embedder is not None and n:
        vecs = embedder.encode([it["c"]["text"] or "" for it in items] + ex_texts)
        cv, ev = vecs[:n], vecs[n:]
        sims = cv @ cv.T
        ex_sims = cv @ ev.T
        thr = embedder.thresholds
    for i, it in enumerate(items):
        it["tropes"] = []
        for key, tr in TROPES.items():
            hit = 0
            for p in (tr["sq_rx"] if it["lang"] == "sq" else tr["en_rx"]):
                for a, b, _ in _matches(p, it["f"]):
                    hit += 1
                    it["spans"].append({"s": a, "e": b, "type": "trope", "note": f"Threat vector: {tr['label']}"})
            if not it["same"] and it["sf"]:
                hit = max(hit, sum(len(_matches(p, it["sf"])) for p in tr["sq_rx"]))
            sem = None
            if ex_sims is not None:
                cols = [j for j, k in enumerate(ex_keys) if k == key]
                sem = float(ex_sims[i, cols].max())
                if hit == 0 and sem >= thr["narrative"]:
                    hit = 1
            if hit:
                it["tropes"].append({"key": key, "label": tr["label"], "hits": hit, "semantic": round(sem, 3) if sem is not None else None})
        dups = []
        for j, jt in enumerate(items):
            if j == i or jt["c"]["user"] == it["c"]["user"]:
                continue
            if sims is not None:
                sim = float(sims[i, j])
                if sim >= thr["near_dup"]:
                    dups.append({"id": jt["c"]["id"], "user": jt["c"]["user"], "sim": round(sim, 3)})
            else:
                inter = len(it["set"] & jt["set"])
                m = min(len(it["set"]), len(jt["set"]))
                if inter >= 3 and m and inter / m >= 0.6:
                    dups.append({"id": jt["c"]["id"], "user": jt["c"]["user"], "sim": round(inter / m, 3)})
        it["dups"] = sorted(dups, key=lambda d: -d["sim"])
    for it in items:
        r, s = [], 0.0
        cats = len(it["tropes"]); hits = sum(t["hits"] for t in it["tropes"])
        if cats:
            s += cats * 30 + (hits - cats) * 10
            r.append("Trope match: " + ", ".join(t["label"] + (f" (semantic {t['semantic']:.2f})" if t.get("semantic") and t["semantic"] >= 0.5 else "") for t in it["tropes"]))
        if it["dups"]:
            s += 35
            kind = "embedding cosine" if (embedder is not None and embedder.kind == "sbert") else "similarity"
            r.append(f"Near-duplicate of {len(it['dups'])} comment(s) ({kind} best {it['dups'][0]['sim']:.2f} with @{it['dups'][0]['user']})")
        if len(it["dups"]) >= 2:
            s += 15; r.append("Talking point repeated by 3+ accounts")
        if not r:
            r.append("No known tropes or near-duplicate talking points")
        it["narrative"], it["nReasons"] = round(clamp(s)), r


# ---- 4. NETWORK (account features + cross-thread co-activity + infrastructure) --
def _network(items, coactivity: CoActivity | None, pub):
    users: dict[str, dict | None] = {}
    for it in items:
        users.setdefault(it["c"]["user"], it["c"].get("account"))
    friend_sets = {u: set((a or {}).get("friends") or []) for u, a in users.items()}
    created = {u: to_ms((a or {}).get("created")) for u, a in users.items()}
    for it in items:
        a, u = it["c"].get("account"), it["c"]["user"]
        r, s, have = [], 0.0, False
        it["maxFriendJ"] = 0.0
        if a:
            have = True
            cm = created.get(u, math.nan)
            if not math.isnan(cm):
                near = sum(1 for v, cv in created.items() if v != u and not math.isnan(cv) and abs(cv - cm) <= 7 * DAY_MS)
                if near >= 4:
                    s += 40; r.append(f"Created {str(a.get('created'))[:10]}; {near} other accounts in thread created within ±7 days")
                elif near:
                    s += near * 8; r.append(f"{near} other account(s) created within ±7 days")
                ref = it["t"] if not math.isnan(it["t"]) else pub
                age = (ref - cm) / DAY_MS if not math.isnan(ref) else math.nan
                if not math.isnan(age) and age < 45:
                    r.append(f"Account only {max(0, round(age))} days old at time of posting")
            fol = a.get("followers")
            if isinstance(fol, (int, float)):
                if fol < 10:
                    s += 20; r.append(f"{int(fol)} followers")
                elif fol < 50:
                    s += 10; r.append(f"Low follower count ({int(fol)})")
            if a.get("tagged_photos") == 0:
                s += 10; r.append("No tagged real-life photos")
            if friend_sets[u]:
                best, bu = 0.0, None
                for v in users:
                    if v != u:
                        j = jaccard(friend_sets[u], friend_sets[v])
                        if j > best:
                            best, bu = j, v
                it["maxFriendJ"] = best
                if best >= 0.85:
                    s += 30; r.append(f"Friend-list overlap {best * 100:.0f}% with @{bu} (closed loop)")
                elif best >= 0.5:
                    s += 15; r.append(f"Friend-list overlap {best * 100:.0f}% with @{bu}")
        if coactivity is not None:
            have = True
            ps = coactivity.partners(u)
            if ps:
                s += min(40, 10 * len(ps))
                r.append(f"Co-active with {len(ps)} account(s) across ≥{coactivity.min_threads} threads within {coactivity.window_s // 60} min (max {ps[0][1]} threads with @{ps[0][0]})")
        inf = it["c"].get("infra")
        if inf and inf.get("type") not in (None, "residential"):
            have = True
            s += 15; r.append(f"Non-residential egress: {str(inf['type']).upper()} via {inf.get('asn')}")
            same = sum(1 for jt in items if jt is not it and jt["c"]["user"] != u and (jt["c"].get("infra") or {}).get("node") == inf.get("node"))
            if same >= 2:
                s += 15; r.append(f"Infrastructure {inf.get('node')} shared with {same} other accounts")
        if not have:
            it["network"], it["gReasons"] = None, ["No account metadata or graph context: network signal skipped"]
            continue
        if not r:
            r.append("No coordinated-network indicators")
        it["network"], it["gReasons"] = round(clamp(s)), r


# ---- FUSION ---------------------------------------------------------------------
def _fuse(items):
    prio = {"trope": 0, "calque": 1, "dialect": 2, "ngram": 3}
    for it in items:
        num = den = 0.0
        for k, w in WEIGHTS.items():
            if it.get(k) is not None:
                num += it[k] * w; den += w
        it["score"] = round(num / den) if den else 0
        it["tier"] = tier_of(it["score"])
        it["renormalised"] = den < 0.999
        clean, end = [], -1
        for sp in sorted(it["spans"], key=lambda x: (x["s"], prio[x["type"]], -(x["e"] - x["s"]))):
            if sp["s"] >= end:
                clean.append(sp); end = sp["e"]
        it["spans"] = clean


# ---- ASSEMBLY (contract v1) -------------------------------------------------------
def _assemble(items, timed, pub, coactivity, infra_meta):
    users: dict[str, Any] = {}
    for it in items:
        users.setdefault(it["c"]["user"], it["c"].get("account"))
    score_by_user: dict[str, int] = {}
    for it in items:
        score_by_user[it["c"]["user"]] = max(score_by_user.get(it["c"]["user"], 0), it["score"])
    ul = list(users)
    fsets = {u: set((users[u] or {}).get("friends") or []) for u in ul}
    edges = []
    for i in range(len(ul)):
        for j in range(i + 1, len(ul)):
            fj = jaccard(fsets[ul[i]], fsets[ul[j]])
            if fj >= 0.5:
                edges.append({"a": ul[i], "b": ul[j], "type": "friends", "w": round(fj, 3)})
    for it in items:
        for d in it["dups"]:
            if it["c"]["user"] < d["user"]:
                edges.append({"a": it["c"]["user"], "b": d["user"], "type": "content", "w": d["sim"]})
    inf_of = {it["c"]["user"]: it["c"]["infra"]["node"] for it in items if it["c"].get("infra") and it["c"]["infra"].get("type") != "residential"}
    iu = list(inf_of)
    for i in range(len(iu)):
        for j in range(i + 1, len(iu)):
            if inf_of[iu[i]] == inf_of[iu[j]]:
                edges.append({"a": iu[i], "b": iu[j], "type": "infra", "w": 1})
    if coactivity is not None:
        us = set(ul)
        for a, b, d in coactivity.graph.edges(data=True):
            if a in us and b in us and d["threads"] >= coactivity.min_threads:
                edges.append({"a": a, "b": b, "type": "coactivity", "w": d["threads"]})
    # clusters: connected components (>=3 accounts) over all edge types
    g = nx.Graph(); g.add_nodes_from(ul); g.add_edges_from((e["a"], e["b"]) for e in edges)
    clusters = []
    for k, comp in enumerate(sorted((c for c in nx.connected_components(g) if len(c) >= 3), key=len, reverse=True)):
        mem = sorted(comp)
        acc = [users[u] for u in mem if users[u]]
        dates = [to_ms(a.get("created")) for a in acc if a.get("created")]
        dates = [d for d in dates if not math.isnan(d)]
        common = set.intersection(*(fsets[u] for u in mem)) if all(fsets[u] for u in mem) else set()
        narr: dict[str, int] = defaultdict(int)
        for it in items:
            if it["c"]["user"] in comp:
                for t in it["tropes"]:
                    narr[t["label"]] += 1
        fol = sorted(a["followers"] for a in acc if isinstance(a.get("followers"), (int, float)))
        clusters.append({
            "id": f"K{k + 1}", "members": mem, "size": len(mem),
            "avgScore": round(sum(score_by_user[u] for u in mem) / len(mem)),
            "createdSpan": [min(dates), max(dates)] if dates else None,
            "sharedFriends": len(common), "medianFollowers": fol[len(fol) // 2] if fol else None,
            "narratives": sorted(narr.items(), key=lambda x: -x[1]),
            "edgeTypes": sorted({e["type"] for e in edges if e["a"] in comp}),
        })
    # bursts (gap-based)
    srt = sorted(timed, key=lambda x: x["t"])
    bursts, cur = [], []
    for it in srt:
        if cur and it["t"] - cur[-1]["t"] > 120000:
            if len(cur) >= 3:
                bursts.append(cur)
            cur = []
        cur.append(it)
    if len(cur) >= 3:
        bursts.append(cur)
    peak = max(bursts, key=len) if bursts else None
    # infrastructure (only for sources that expose it, e.g. your own portal's logs)
    infra: dict[str, dict] = {}
    for it in items:
        inf = it["c"].get("infra")
        if not inf or not inf.get("node"):
            continue
        g_ = infra.setdefault(inf["node"], {"node": inf["node"], "type": inf.get("type"), "asn": inf.get("asn"), "city": inf.get("city"), "cc": inf.get("cc"),
                                             "lat": inf.get("lat"), "lon": inf.get("lon"), "ips": set(), "users": set(), "_items": [], "vectors": defaultdict(int)})
        if inf.get("ip"):
            g_["ips"].add(inf["ip"])
        g_["users"].add(it["c"]["user"]); g_["_items"].append(it)
        for t in it["tropes"]:
            g_["vectors"][t["key"]] += 1
    infra_list = []
    for g_ in infra.values():
        its = g_.pop("_items")
        ts = [i["t"] for i in its if not math.isnan(i["t"])]
        flagged = sum(1 for i in its if i["tier"] != "organic")
        infra_list.append({**g_, "ips": sorted(g_["ips"]), "users": sorted(g_["users"]), "comments": len(its),
                           "avgRisk": round(sum(i["score"] for i in its) / len(its)), "flagged": flagged,
                           "hostile": g_["type"] not in (None, "residential") and flagged >= max(1, len(its) / 2),
                           "first": min(ts) if ts else None, "last": max(ts) if ts else None,
                           "vectors": sorted(g_["vectors"].items(), key=lambda x: -x[1]), "meta": infra_meta.get(g_["node"])})
    infra_list.sort(key=lambda x: -x["avgRisk"])
    # thread summary
    n = len(items)
    high = sum(1 for i in items if i["tier"] == "high"); med = sum(1 for i in items if i["tier"] == "medium")
    pct = (high + med) / n if n else 0.0
    largest = clusters[0]["size"] if clusters else 0
    level = 1 if pct >= 0.3 and largest >= 5 else 2 if pct >= 0.15 or largest >= 3 else 3 if pct > 0 else 4
    narratives: dict[str, int] = defaultdict(int)
    for i in items:
        if i["tier"] != "organic":
            for t in i["tropes"]:
                narratives[t["key"]] += 1
    flagged_items = [i for i in items if i["tier"] != "organic"]
    organic_items = [i for i in items if i["tier"] == "organic"]

    def avg(arr, k):
        v = [x[k] for x in arr if x.get(k) is not None]
        return round(sum(v) / len(v)) if v else None

    out_items = []
    keep = ("t", "temporal", "linguistic", "narrative", "network", "score", "tier", "renormalised", "tReasons", "lReasons", "nReasons",
            "gReasons", "tropes", "dups", "spans", "dialect", "maxFriendJ", "window", "doublets", "calques")
    for it in items:
        o = {k: it.get(k) for k in keep}
        o["t"] = None if math.isnan(it["t"]) else it["t"]
        o["c"] = it["c"]
        out_items.append(o)
    return {
        "contract": CONTRACT_VERSION,
        "items": out_items, "edges": edges, "clusters": clusters, "scoreByUser": score_by_user, "users": users, "infra": infra_list,
        "thread": {
            "n": n, "high": high, "medium": med, "organic": n - high - med, "pct": pct, "level": level, "levelName": ALERT_LEVEL_NAMES[level],
            "published": None if math.isnan(pub) else pub, "expected": items[0]["_expected"] if items else 1.0,
            "peak": {"start": peak[0]["t"], "end": peak[-1]["t"], "count": len(peak), "coordinated": sum(1 for x in peak if x["tier"] != "organic")} if peak else None,
            "narratives": [[TROPES[k]["label"], c, k, TROPES[k]["severity"]] for k, c in sorted(narratives.items(), key=lambda x: -x[1])],
            "signalAvg": {k: {"flagged": avg(flagged_items, k), "organic": avg(organic_items, k)} for k in WEIGHTS},
            "largestCluster": largest,
        },
    }
