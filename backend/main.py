"""FastAPI app: REST + WebSocket for the tactical dashboard.

Run:   uvicorn main:app --host 0.0.0.0 --port 8000
Open:  http://localhost:8000        (dashboard, served same-origin)
Docs:  http://localhost:8000/docs   (OpenAPI)
"""
from __future__ import annotations

import asyncio
import contextlib
import hashlib
import logging
import os
import time
from typing import Any, Literal

from fastapi import Depends, FastAPI, Header, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, PlainTextResponse
from pydantic import BaseModel, Field

import ingest
from config import settings
from service import Pipeline

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"), format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("htccip.api")
pipeline = Pipeline()
geo = ingest.GeoIP()


# ---------------------------------------------------------------------------
# background pollers
# ---------------------------------------------------------------------------
async def youtube_loop():
    if not settings.youtube_api_key:
        log.info("YouTube poller off (no YOUTUBE_API_KEY)")
        return
    for vid in settings.youtube_video_ids:
        pipeline.store.add_source("youtube", vid)
    last_search = 0.0
    while True:
        try:
            if settings.youtube_search_query and time.time() - last_search > 1800:  # search.list costs 100 units: every 30 min max
                for vid in await asyncio.to_thread(ingest.yt_search_videos, settings.youtube_search_query, 24, settings.youtube_search_lang, 5):
                    pipeline.store.add_source("youtube", vid)
                last_search = time.time()
            await poll_youtube_once()
        except Exception as e:
            log.warning("YouTube poll error: %s", e)
        await asyncio.sleep(settings.youtube_poll_s)


async def poll_youtube_once() -> dict:
    srcs = pipeline.store.sources("youtube")
    if not srcs:
        return {"videos": 0}
    meta = await asyncio.to_thread(ingest.yt_video_meta, [s["ref"] for s in srcs])
    total = 0
    for s in srcs:
        vid = s["ref"]
        since = float(s["cursor"]) if s.get("cursor") else None
        comments = await asyncio.to_thread(ingest.yt_fetch_comments, vid, since)
        if comments and settings.youtube_enrich_channels:
            await asyncio.to_thread(ingest.yt_enrich_accounts, comments)
        ingest.strip_private(comments)
        m = meta.get(vid, {})
        thread = {"id": f"yt:{vid}", "platform": "youtube", "external_id": vid, "title": m.get("title"), "url": f"https://www.youtube.com/watch?v={vid}", "published": m.get("published")}
        await pipeline.ingest([thread], comments)
        if comments:
            pipeline.store.set_cursor(s["id"], str(max(c["ts"] for c in comments if c.get("ts"))))
        total += len(comments)
    return {"videos": len(srcs), "comments": total}


async def telegram_loop():
    if not (settings.tg_api_id and settings.tg_api_hash):
        log.info("Telegram poller off (no TG_API_ID/TG_API_HASH)")
        return
    try:
        fetcher = await ingest.TelegramFetcher().start()
    except Exception as e:
        log.warning("Telegram disabled: %s", e)
        return
    for ch in settings.tg_channels:
        pipeline.store.add_source("telegram", ch)
    while True:
        for s in pipeline.store.sources("telegram"):
            try:
                since = float(s["cursor"]) if s.get("cursor") else None
                threads, comments = await fetcher.fetch(s["ref"], since_ms=since)
                await pipeline.ingest(threads, comments)
                if comments:
                    pipeline.store.set_cursor(s["id"], str(max(c["ts"] for c in comments)))
            except Exception as e:
                log.warning("Telegram poll error on %s: %s", s["ref"], e)
        await asyncio.sleep(settings.tg_poll_s)


@contextlib.asynccontextmanager
async def lifespan(_app: FastAPI):
    tasks = [asyncio.create_task(youtube_loop()), asyncio.create_task(telegram_loop())]
    log.info("Embedding backend: %s · alerts %s", pipeline.embedder.kind, "DRY-RUN" if settings.alert_dry_run else "LIVE")
    yield
    for t in tasks:
        t.cancel()


app = FastAPI(title="Hybrid Threat & Coordinated Campaign Intelligence Platform API", version="1.0", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=settings.cors_origins, allow_methods=["*"], allow_headers=["*"])


def require_key(x_api_key: str | None = Header(default=None)):
    if settings.api_key and x_api_key != settings.api_key:
        raise HTTPException(401, "Missing or invalid X-API-Key")


# ---------------------------------------------------------------------------
# schemas
# ---------------------------------------------------------------------------
class CommentIn(BaseModel):
    id: str | None = None
    user: str
    ts: float | str | None = None
    text: str
    source_text: str | None = None
    account: dict[str, Any] | None = None
    infra: dict[str, Any] | None = None


class BatchIn(BaseModel):
    thread_id: str = Field(..., description="Your thread key; stored as 'ext:<thread_id>'")
    title: str | None = None
    url: str | None = None
    published: float | str | None = None
    platform: str = "manual"
    comments: list[CommentIn]


class PortalIn(BaseModel):
    thread_id: str
    title: str | None = None
    url: str | None = None
    published: float | str | None = None
    comments: list[dict[str, Any]] = Field(..., description="each: user, ts, text, ip (server-side), optional account")


class SimulateIn(BaseModel):
    thread_id: str | None = Field(None, description="Inject into this thread; default: a new simulated thread")
    spread_threads: int = Field(2, ge=1, le=6, description="Hit N threads with the same account pool (feeds the co-activity graph)")
    accounts: int = Field(12, ge=3, le=60)
    window_s: int = Field(240, ge=30, le=1800)
    lang: Literal["en", "sq"] = "en"
    background: int = Field(6, ge=0, le=50, description="Organic background comments per thread")
    include_infra: bool = True
    include_accounts: bool = True


class SourceIn(BaseModel):
    kind: Literal["youtube", "telegram"]
    ref: str = Field(..., description="YouTube video ID or Telegram channel @username")


# ---------------------------------------------------------------------------
# routes
# ---------------------------------------------------------------------------
@app.get("/api/health")
async def health():
    return {"ok": True, "contract": "1", "embedder": pipeline.embedder.kind, "alerts": "dry-run" if settings.alert_dry_run else "live",
            "youtube": bool(settings.youtube_api_key), "telegram": bool(settings.tg_api_id), "llm": settings.llm_enabled and bool(settings.anthropic_api_key)}


@app.get("/api/threads")
async def threads():
    out = []
    for t in pipeline.store.list_threads():
        a = pipeline._cache.get(t["id"])
        out.append({**t, "level": a["thread"]["level"] if a else None, "pct": a["thread"]["pct"] if a else None})
    return out


@app.get("/api/threads/{tid:path}/analysis")
async def thread_analysis(tid: str):
    a = await pipeline.analysis(tid)
    if not a:
        raise HTTPException(404, "unknown thread")
    return a


@app.get("/api/threads/{tid:path}/sitrep", response_class=PlainTextResponse)
async def thread_sitrep(tid: str):
    s = await pipeline.sitrep(tid)
    if s is None:
        raise HTTPException(404, "unknown thread")
    return s


@app.get("/api/graph")
async def graph():
    return pipeline.global_graph()


@app.post("/api/ingest/comments", dependencies=[Depends(require_key)])
async def ingest_comments(b: BatchIn):
    tid = f"ext:{b.thread_id}"
    pub = ingest.iso_to_ms(b.published) if isinstance(b.published, str) else b.published
    comments = []
    for i, c in enumerate(b.comments):
        ts = ingest.iso_to_ms(c.ts) if isinstance(c.ts, str) else c.ts
        cid = c.id or f"{tid}:" + hashlib.sha1(f"{c.user}|{ts}|{c.text}".encode()).hexdigest()[:16]
        comments.append({"id": cid, "thread_id": tid, "platform": b.platform, "user": c.user, "ts": ts, "text": c.text,
                         "source_text": c.source_text, "account": c.account, "infra": c.infra})
    return await pipeline.ingest([{"id": tid, "platform": b.platform, "external_id": b.thread_id, "title": b.title, "url": b.url, "published": pub}], comments)


@app.post("/api/ingest/portal", dependencies=[Depends(require_key)])
async def ingest_portal(b: PortalIn):
    thread, comments = ingest.normalize_portal(b.model_dump(), geo)
    return await pipeline.ingest([thread], comments)


@app.post("/api/simulate-attack", dependencies=[Depends(require_key)])
async def simulate_attack(b: SimulateIn):
    n = int(time.time())
    title = "Kosovo–Serbia dialogue resumes in Brussels as NATO confirms KFOR posture"
    tids = [b.thread_id] if b.thread_id else []
    tids += [f"sim:demo-{n}-{k}" for k in range(b.spread_threads - len(tids))]
    threads = []
    for k, tid in enumerate(tids):
        if not pipeline.store.get_thread(tid):
            threads.append({"id": tid, "platform": "simulated", "title": f"[EXERCISE] {title}" + (f" (mirror {k})" if k else ""), "simulated": True,
                            "published": time.time() * 1000 - (len(tids) - 1 - k) * 25 * 60000 - 3 * 60000})
    comments = ingest.simulate_swarm(tids, accounts=b.accounts, window_s=b.window_s, lang=b.lang, include_infra=b.include_infra,
                                     include_accounts=b.include_accounts, background=b.background)
    res = await pipeline.ingest(threads, comments)
    return {**res, "thread_ids": tids, "primary": tids[-1]}


@app.get("/api/sources")
async def sources():
    return pipeline.store.sources()


@app.post("/api/sources", dependencies=[Depends(require_key)])
async def add_source(s: SourceIn):
    return pipeline.store.add_source(s.kind, s.ref.lstrip("@") if s.kind == "telegram" else s.ref)


@app.post("/api/sources/youtube/poll", dependencies=[Depends(require_key)])
async def poll_now():
    return await poll_youtube_once()


@app.get("/api/alerts")
async def alerts():
    return pipeline.store.alerts()


@app.post("/api/alerts/test/{tid:path}", dependencies=[Depends(require_key)])
async def alert_test(tid: str):
    a = await pipeline.analysis(tid)
    if not a:
        raise HTTPException(404, "unknown thread")
    return await asyncio.to_thread(pipeline.alerts.evaluate, a["meta"]["thread"], a, force=True)


@app.websocket("/ws/live")
async def ws_live(ws: WebSocket):
    if settings.api_key and ws.query_params.get("key") != settings.api_key:
        await ws.close(code=4401)
        return
    await ws.accept()

    async def push(msg: dict):
        await ws.send_json(msg)

    pipeline.subscribe(push)
    await ws.send_json({"type": "hello", "threads": await threads()})
    try:
        while True:
            await ws.receive_text()  # keep-alive / ignore client messages
    except WebSocketDisconnect:
        pass
    finally:
        pipeline.unsubscribe(push)


@app.get("/", response_class=HTMLResponse)
async def dashboard():
    p = os.path.abspath(os.path.join(os.path.dirname(__file__), settings.dashboard_path))
    if os.path.exists(p):
        return FileResponse(p, media_type="text/html")
    return HTMLResponse("<h1>Dashboard not found</h1><p>Set DASHBOARD_PATH.</p>", status_code=404)
