"""Pipeline: ingest → store → (re)analyse affected threads → push to dashboards → alert.

Framework-independent so it can be tested without FastAPI.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import time
from typing import Awaitable, Callable

import networkx as nx

from alerts import AlertManager, build_sitrep
from config import settings
from embeddings import Embedder
from engine import analyze_thread, build_coactivity
from ingest import SIM_INFRA, pseudonymize_account, truncate_ip
from store import Store

log = logging.getLogger("htccip.service")
Broadcast = Callable[[dict], Awaitable[None]]
SCORING_VERSION = "1.1"

LLM_PROMPT = """You review Albanian-language social media comments for signs that they were written by
non-native speakers or machine-translated from Serbian/Russian (calques, unnatural word order,
Geg/standard Albanian mixed inside one word pair, foreign idioms). You are NOT judging the opinion.
Return JSON only: {"results":[{"id":"...","score":0-100,"findings":["short English finding", ...]}]}.
Score 0 = natural native writing. Comments:
"""


class Pipeline:
    def __init__(self, store: Store | None = None, embedder: Embedder | None = None):
        self.store = store or Store(settings.db_path)
        self.embedder = embedder or Embedder()
        self.store.normalize_legacy_users(pseudonymize_account, truncate_ip)
        self.alerts = AlertManager(self.store)
        self.subscribers: set[Broadcast] = set()
        self._llm_cache: dict[str, dict] = {}
        self._lock = asyncio.Lock()

    # --- ingestion entry point ---------------------------------------------
    async def ingest(self, threads: list[dict], comments: list[dict]) -> dict:
        known = {t["id"] for t in threads}
        batch = list(threads)
        for c in comments:
            if c["thread_id"] not in known:
                if not self.store.get_thread(c["thread_id"]):
                    batch.append({"id": c["thread_id"], "platform": c.get("platform"), "simulated": c.get("simulated")})
                known.add(c["thread_id"])
        async with self._lock:
            new = await asyncio.to_thread(self.store.ingest_batch, batch, comments)
            results = await self._process_dirty()
            pending = len(self.store.dirty_threads())
        await self._publish(results)
        return {"received": len(comments), "new": len(new), "threads": {k: v["thread"]["levelName"] for k, v in results.items()},
                "alerts": [v.get("_alert") for v in results.values() if v.get("_alert")], "pending_analyses": pending}

    # --- analysis -----------------------------------------------------------
    async def process_pending(self) -> None:
        async with self._lock:
            results = await self._process_dirty()
        await self._publish(results)

    async def _process_dirty(self) -> dict[str, dict]:
        results = {}
        for tid in self.store.dirty_threads():
            try:
                analysis = await asyncio.to_thread(self._analyze, tid)
                alert = await asyncio.to_thread(self.alerts.evaluate, analysis["meta"]["thread"], analysis)
                await asyncio.to_thread(self.store.save_analysis, tid, analysis)
                results[tid] = {**analysis, "_alert": alert}
            except Exception:
                log.exception("Analysis pending for %s; will retry", tid)
        return results

    async def _publish(self, results: dict[str, dict]) -> None:
        for tid, analysis in results.items():
            await self._broadcast({"type": "analysis", "thread_id": tid, "level": analysis["thread"]["level"],
                                   "n": analysis["thread"]["n"], "alert": analysis["_alert"]})

    def _analyze(self, tid: str) -> dict:
        comments = self.store.thread_comments(tid)
        thread = self.store.get_thread(tid) or {}
        simulated = bool(thread.get("simulated"))
        since = time.time() * 1000 - settings.graph_lookback_h * 3600_000
        recent = self.store.recent_comments(
            min(since, min((c["ts"] for c in comments if c.get("ts")), default=since)), simulated
        )
        co = build_coactivity(recent, window_s=settings.coactivity_window_s, min_threads=settings.coactivity_min_threads)
        llm = self._llm_review(comments) if settings.llm_enabled and settings.anthropic_api_key else {}
        res = analyze_thread(comments, published=thread.get("published"), embedder=self.embedder, coactivity=co,
                             llm_flags=llm, infra_meta=SIM_INFRA if simulated else None)
        res["meta"] = {"thread": thread, "generated": time.time() * 1000, "embedder": self.embedder.kind,
                       "scoring_version": SCORING_VERSION}
        return res

    def _llm_review(self, comments: list[dict]) -> dict:
        """Optional: ask an LLM to review Albanian comments that already look suspicious.
        Cached by text hash; failures are logged and ignored (heuristics still run)."""
        # quick heuristic pre-pass to pick candidates
        pre = analyze_thread(comments, embedder=None)
        cand = [i["c"] for i in pre["items"] if i["score"] >= settings.llm_min_score and (i["c"].get("source_text") or i["dialect"]["profile"] != "n/a (non-Albanian input)")]
        todo = [c for c in cand if self._hash(c) not in self._llm_cache][:25]
        if todo:
            try:
                import anthropic  # type: ignore

                client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
                payload = "\n".join(json.dumps({"id": c["id"], "text": c.get("source_text") or c["text"]}, ensure_ascii=False) for c in todo)
                msg = client.messages.create(model=settings.llm_model, max_tokens=1500, messages=[{"role": "user", "content": LLM_PROMPT + payload}])
                raw = "".join(b.text for b in msg.content if getattr(b, "type", "") == "text")
                data = json.loads(raw[raw.find("{"): raw.rfind("}") + 1])
                by_id = {c["id"]: c for c in todo}
                for r in data.get("results", []):
                    if r.get("id") in by_id:
                        self._llm_cache[self._hash(by_id[r["id"]])] = {"score": max(0, min(100, int(r.get("score", 0)))), "findings": [str(x)[:140] for x in r.get("findings", [])][:3]}
            except Exception as e:
                log.warning("LLM review skipped: %s", e)
        return {c["id"]: self._llm_cache[self._hash(c)] for c in cand if self._hash(c) in self._llm_cache}

    @staticmethod
    def _hash(c: dict) -> str:
        return hashlib.sha1((c.get("source_text") or c.get("text") or "").encode()).hexdigest()

    async def analysis(self, tid: str) -> dict | None:
        async with self._lock:
            saved = await asyncio.to_thread(self.store.saved_analysis, tid)
            if saved and saved["meta"].get("scoring_version") == SCORING_VERSION:
                return saved
            if not self.store.get_thread(tid):
                return None
            analysis = await asyncio.to_thread(self._analyze, tid)
            if tid not in self.store.dirty_threads():
                await asyncio.to_thread(self.store.save_analysis, tid, analysis)
            return analysis

    async def sitrep(self, tid: str) -> str | None:
        a = await self.analysis(tid)
        if not a:
            return None
        thread = a["meta"]["thread"]
        exercise = bool(thread.get("simulated")) or any(i["c"].get("simulated") for i in a["items"])
        return build_sitrep(a, thread, exercise=exercise)

    def _recent_graph(self, simulated: bool):
        since = time.time() * 1000 - settings.graph_lookback_h * 3600_000
        return build_coactivity(self.store.recent_comments(since, simulated), window_s=settings.coactivity_window_s,
                                min_threads=settings.coactivity_min_threads)

    def global_graph(self, simulated: bool = False) -> dict:
        co = self._recent_graph(simulated)
        scores: dict[str, int] = {}
        for snapshot in self.store.score_snapshots(simulated):
            for user, score in snapshot.items():
                scores[user] = max(scores.get(user, 0), score)
        return co.to_json(scores)

    def case_suggestions(self, simulated: bool) -> list[dict]:
        from collections import Counter
        from itertools import combinations

        pairs = Counter()
        for _, _, edge in self._recent_graph(simulated).graph.edges(data=True):
            if edge["threads"] >= settings.coactivity_min_threads:
                pairs.update(combinations(sorted(edge["thread_ids"]), 2))
        graph = nx.Graph()
        graph.add_edges_from(pair for pair, count in pairs.items() if count >= 3)
        existing = [set(case["thread_ids"]) for case in self.store.cases(simulated)]
        return [
            {"thread_ids": sorted(group), "links": [
                {"thread_ids": [a, b], "account_pairs": count}
                for (a, b), count in sorted(pairs.items()) if a in group and b in group and count >= 3
            ]}
            for group in sorted(nx.connected_components(graph), key=lambda members: sorted(members))
            if not any(group <= thread_ids for thread_ids in existing)
        ]

    async def case_evidence(self, thread_ids: list[str], simulated: bool) -> dict:
        evidence = {}
        for tid in dict.fromkeys(thread_ids):
            analysis = await self.analysis(tid)
            if analysis is None:
                raise LookupError(tid)
            if bool(analysis["meta"]["thread"]["simulated"]) != simulated:
                raise ValueError("Live and exercise threads cannot share a case")
            items = sorted(analysis["items"], key=lambda item: -item["score"])
            evidence[tid] = {
                "thread": analysis["meta"]["thread"],
                "captured_at": time.time() * 1000,
                "scoring_version": analysis["meta"]["scoring_version"],
                "score": analysis["thread"],
                "comments": [
                    {**{key: item["c"].get(key) for key in ("id", "ts", "url", "user", "text")},
                     "score": item["score"], "signals": {key: item[key] for key in ("tReasons", "lReasons", "nReasons", "gReasons")}}
                    for item in items[:12]
                ],
                "coactivity_edges": [edge for edge in analysis["edges"] if edge["type"] == "coactivity"][:50],
            }
        return evidence

    # --- realtime -------------------------------------------------------------
    def subscribe(self, fn: Broadcast) -> None:
        self.subscribers.add(fn)

    def unsubscribe(self, fn: Broadcast) -> None:
        self.subscribers.discard(fn)

    async def _broadcast(self, msg: dict) -> None:
        dead = []
        for fn in list(self.subscribers):
            try:
                await asyncio.wait_for(fn(msg), timeout=2)
            except Exception:
                dead.append(fn)
        for fn in dead:
            self.subscribers.discard(fn)
