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

from alerts import AlertManager, build_sitrep
from config import settings
from embeddings import Embedder
from engine import analyze_thread, build_coactivity
from ingest import SIM_INFRA
from store import Store

log = logging.getLogger("htccip.service")
Broadcast = Callable[[dict], Awaitable[None]]

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
        self.alerts = AlertManager(self.store)
        self.subscribers: set[Broadcast] = set()
        self._cache: dict[str, dict] = {}
        self._llm_cache: dict[str, dict] = {}
        self._lock = asyncio.Lock()

    # --- ingestion entry point ---------------------------------------------
    async def ingest(self, threads: list[dict], comments: list[dict]) -> dict:
        for t in threads:
            self.store.upsert_thread(t)
        known = {t["id"] for t in threads}
        for c in comments:  # make sure every referenced thread exists
            if c["thread_id"] not in known and not self.store.get_thread(c["thread_id"]):
                self.store.upsert_thread({"id": c["thread_id"], "platform": c.get("platform"), "simulated": c.get("simulated")})
                known.add(c["thread_id"])
        new = self.store.add_comments(comments)
        affected = sorted({c["thread_id"] for c in new})
        results = {}
        for tid in affected:
            results[tid] = await self.refresh(tid)
        return {"received": len(comments), "new": len(new), "threads": {k: v["thread"]["levelName"] for k, v in results.items()},
                "alerts": [v.get("_alert") for v in results.values() if v.get("_alert")]}

    # --- analysis -----------------------------------------------------------
    async def refresh(self, tid: str) -> dict:
        async with self._lock:
            analysis = await asyncio.to_thread(self._analyze, tid)
        thread = self.store.get_thread(tid) or {"id": tid}
        analysis["meta"] = {"thread": thread, "generated": time.time() * 1000, "embedder": self.embedder.kind}
        alert = await asyncio.to_thread(self.alerts.evaluate, thread, analysis)
        if alert:
            analysis["_alert"] = alert
        self._cache[tid] = analysis
        await self._broadcast({"type": "analysis", "thread_id": tid, "level": analysis["thread"]["level"], "n": analysis["thread"]["n"], "alert": alert})
        return analysis

    def _analyze(self, tid: str) -> dict:
        comments = self.store.thread_comments(tid)
        thread = self.store.get_thread(tid) or {}
        since = time.time() * 1000 - settings.graph_lookback_h * 3600_000
        recent = self.store.recent_comments(min(since, min((c["ts"] for c in comments if c.get("ts")), default=since)))
        co = build_coactivity(recent, window_s=settings.coactivity_window_s, min_threads=settings.coactivity_min_threads)
        llm = self._llm_review(comments) if settings.llm_enabled and settings.anthropic_api_key else {}
        res = analyze_thread(comments, published=thread.get("published"), embedder=self.embedder, coactivity=co, llm_flags=llm, infra_meta=SIM_INFRA)
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
        if tid in self._cache:
            return self._cache[tid]
        if not self.store.get_thread(tid):
            return None
        return await self.refresh(tid)

    async def sitrep(self, tid: str) -> str | None:
        a = await self.analysis(tid)
        if not a:
            return None
        thread = a["meta"]["thread"]
        exercise = bool(thread.get("simulated")) or any(i["c"].get("simulated") for i in a["items"])
        return build_sitrep(a, thread, exercise=exercise)

    def global_graph(self) -> dict:
        since = time.time() * 1000 - settings.graph_lookback_h * 3600_000
        co = build_coactivity(self.store.recent_comments(since), window_s=settings.coactivity_window_s, min_threads=settings.coactivity_min_threads)
        scores: dict[str, int] = {}
        for a in self._cache.values():
            for u, s in a["scoreByUser"].items():
                scores[u] = max(scores.get(u, 0), s)
        return co.to_json(scores)

    # --- realtime -------------------------------------------------------------
    def subscribe(self, fn: Broadcast) -> None:
        self.subscribers.add(fn)

    def unsubscribe(self, fn: Broadcast) -> None:
        self.subscribers.discard(fn)

    async def _broadcast(self, msg: dict) -> None:
        dead = []
        for fn in list(self.subscribers):
            try:
                await fn(msg)
            except Exception:
                dead.append(fn)
        for fn in dead:
            self.subscribers.discard(fn)
