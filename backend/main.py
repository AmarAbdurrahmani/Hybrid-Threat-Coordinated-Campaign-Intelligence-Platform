"""FastAPI app for monitoring, evidence review and live dashboard updates."""
from __future__ import annotations

import asyncio
import contextlib
import hashlib
import hmac
import logging
import math
import os
import re
import secrets
import time
import uuid
from datetime import datetime, timezone
from typing import Annotated, Literal

from fastapi import Depends, FastAPI, Header, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, PlainTextResponse
from pydantic import BaseModel, Field, HttpUrl, IPvAnyAddress, StringConstraints, field_validator

import evaluator
import ingest
from config import settings
from infrastructure import globe_points
from service import Pipeline

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"), format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("htccip.api")
pipeline = Pipeline()
geo = ingest.GeoIP()
source_locks = {"youtube": asyncio.Lock(), "telegram": asyncio.Lock()}
autoscan_lock = asyncio.Lock()  # one scan at a time; a second click waits and then hits the cache
telegram_fetcher = None
tickets: dict[str, float] = {}


async def poll_sources(kind: str, *, sid: str | None = None, backfill: bool = False) -> dict:
    global telegram_fetcher
    result = []
    async with source_locks[kind]:
        sources = pipeline.store.sources(kind, include_paused=bool(sid))
        for source in sources:
            if sid and source["id"] != sid:
                continue
            source_id, ref = source["id"], source["ref"]
            pipeline.store.source_started(source_id)
            cursor = None if backfill or not source["cursor"] else float(source["cursor"]) - 10 * 60_000
            try:
                if kind == "youtube":
                    if not settings.youtube_api_key:
                        raise RuntimeError("YouTube credentials unavailable")
                    metadata = await asyncio.to_thread(ingest.yt_video_meta, [ref])
                    if ref not in metadata:
                        raise LookupError("YouTube video unavailable")
                    comments = await asyncio.to_thread(ingest.yt_fetch_comments, ref, cursor, 30 if backfill else 10)
                    if comments and settings.youtube_enrich_channels:
                        try:
                            await asyncio.to_thread(ingest.yt_enrich_accounts, comments)
                        except ingest.YouTubeError:
                            log.warning("Channel enrichment skipped for %s", source_id)
                    ingest.strip_private(comments)
                    threads = [{"id": f"yt:{ref}", "platform": "youtube", "external_id": ref,
                                "url": f"https://www.youtube.com/watch?v={ref}", **metadata[ref]}]
                else:
                    if telegram_fetcher is None:
                        telegram_fetcher = await ingest.TelegramFetcher().start()
                    threads, comments = await telegram_fetcher.fetch(ref, posts=30 if backfill else 10, since_ms=cursor)
                batch = await pipeline.ingest(threads, comments)
                pipeline.store.source_finished(source_id, cursor=max((c["ts"] for c in comments if c.get("ts")), default=None),
                                               backfill=backfill)
                result.append({"id": source_id, "comments": len(comments), "new": batch["new"]})
            except Exception as exc:
                reason = str(exc) if isinstance(exc, (ingest.YouTubeError, LookupError)) else type(exc).__name__
                log.warning("Poll failed for %s (%s)", source_id, reason)
                pipeline.store.source_finished(source_id, error=reason)
                result.append({"id": source_id, "error": reason})
                if kind == "telegram" and isinstance(exc, (OSError, ConnectionError)):
                    if telegram_fetcher:
                        await telegram_fetcher.client.disconnect()
                    telegram_fetcher = None
    return {"sources": result, "comments": sum(item.get("comments", 0) for item in result),
            "errors": sum("error" in item for item in result), "backfill": backfill}


async def source_loop(kind: str) -> None:
    if kind == "youtube":
        if not settings.youtube_api_key:
            return
        refs, period = settings.youtube_video_ids, settings.youtube_poll_s
    else:
        if not (settings.tg_api_id and settings.tg_api_hash):
            return
        refs, period = settings.tg_channels, settings.tg_poll_s
    for ref in refs:
        pipeline.store.add_source(kind, ref.lstrip("@").lower() if kind == "telegram" else ref)
    last_search = 0.0
    while True:
        if kind == "youtube" and settings.youtube_search_query and time.monotonic() - last_search > 1800:
            last_search = time.monotonic()
            try:
                for ref in await asyncio.to_thread(ingest.yt_search_videos, settings.youtube_search_query,
                                                   24, settings.youtube_search_lang, 5):
                    pipeline.store.add_source(kind, ref)
            except Exception as exc:
                log.warning("YouTube source discovery failed (%s)", type(exc).__name__)
        await poll_sources(kind)
        await asyncio.sleep(period)


# YouTube auto-scan: search.list -> commentThreads.list -> pipeline (detection runs per video thread).
YT_FATAL = {"missingKey", "keyInvalid", "badRequest", "accessNotConfigured", "quotaExceeded", "dailyLimitExceeded",
            "rateLimitExceeded", "unreachable"}


def _yt_message(exc: "ingest.YouTubeError") -> str:
    return YT_ERRORS.get(exc.reason, (0, f"YouTube API error ({exc.reason or exc.status or 'unknown'})"))[1]


def _scan_summary(tid: str) -> dict:
    analysis = pipeline.store.saved_analysis(tid) or {}
    th = analysis.get("thread") or {}
    flagged = sorted({it["c"]["user"] for it in analysis.get("items", []) if it.get("tier") != "organic"})
    peak = th.get("peak") or {}
    return {"level": th.get("level"), "level_name": th.get("levelName"), "flagged_comments": (th.get("high") or 0) + (th.get("medium") or 0),
            "flagged_accounts": flagged, "clusters": len(analysis.get("clusters") or []), "largest_cluster": th.get("largestCluster") or 0,
            "burst": peak.get("count") or 0, "burst_coordinated": peak.get("coordinated") or 0}


async def run_youtube_autoscan(queries: list[str], key: str, *, order: str = "date", max_videos: int = 10, per_query: int = 10,
                               comment_pages: int = 1, lookback_h: int | None = None, enrich: bool = True) -> dict:
    """Discover recent videos for `queries`, pull their comments, ingest and analyse them.
    Raises YouTubeError only when nothing could be scanned; later fatal errors return partial results."""
    ttl = settings.youtube_autoscan_cache_s
    lookback = settings.youtube_autoscan_lookback_h if lookback_h is None else lookback_h
    stats: dict = {}
    warnings: list[str] = []
    async with autoscan_lock:
        videos: dict[str, dict] = {}
        for q in queries:
            try:
                found = await asyncio.to_thread(ingest.yt_search, q, api_key=key, order=order, max_results=per_query,
                                                published_after_h=lookback or None, ttl=ttl, stats=stats)
            except ingest.YouTubeError as exc:
                if exc.reason in YT_FATAL and not videos:
                    raise
                warnings.append(f"search '{q}': {_yt_message(exc)}")
                if exc.reason in YT_FATAL:
                    break
                continue
            for v in found:
                videos.setdefault(v["video_id"], dict(v))
        # Rank by commentCount (1 unit) so the comment budget goes to videos that actually have comments.
        skipped: list[dict] = []
        try:
            counts = await asyncio.to_thread(ingest.yt_video_stats, list(videos), key, ttl=ttl, stats=stats)
        except ingest.YouTubeError as exc:
            counts = None
            warnings.append("comment counts unavailable (" + (exc.reason or "error") + ")")
        if counts is not None:
            for v in videos.values():
                v["comment_count"] = counts.get(v["video_id"])
                if v["video_id"] not in counts:
                    v["status"], v["message"] = "videoNotFound", YT_ERRORS["videoNotFound"][1]
                elif counts[v["video_id"]] is None:
                    v["status"], v["message"] = "commentsDisabled", YT_ERRORS["commentsDisabled"][1]
                elif counts[v["video_id"]] == 0:
                    v["status"] = "no_comments"
            candidates = sorted((v for v in videos.values() if not v.get("status")), key=lambda v: -v["comment_count"])
            skipped = [v for v in videos.values() if v.get("status")]
        else:
            candidates = list(videos.values())
        picked = candidates[:max_videos]

        sem = asyncio.Semaphore(4)
        fatal: list = []

        async def fetch(v: dict) -> list[dict]:
            async with sem:
                if fatal:
                    v["status"] = "skipped"
                    return []
                try:
                    cs = await asyncio.to_thread(ingest.yt_fetch_comments, v["video_id"], None, comment_pages, key,
                                                 page_size=50, replies=False, ttl=ttl, stats=stats)
                except ingest.YouTubeError as exc:
                    v["status"], v["message"] = exc.reason or f"HTTP {exc.status}", _yt_message(exc)
                    if exc.reason in YT_FATAL:
                        fatal.append(exc)
                    return []
                v["status"] = "ok" if cs else "no_comments"
                return cs

        per_video = await asyncio.gather(*(fetch(v) for v in picked))
        if fatal and not any(per_video):
            raise fatal[0]
        if fatal:
            warnings.append(_yt_message(fatal[0]) + " (partial scan)")
        comments = [c for cs in per_video for c in cs]
        enriched = False
        if comments and enrich:
            try:
                await asyncio.to_thread(ingest.yt_enrich_accounts, comments, key, ttl=ttl, stats=stats)
                enriched = True
            except ingest.YouTubeError as exc:
                warnings.append("channel ages unavailable (" + (exc.reason or "error") + ")")
        ingest.strip_private(comments)
        threads = [{"id": f"yt:{v['video_id']}", "platform": "youtube", "external_id": v["video_id"], "url": v["url"],
                    "title": v["title"], "published": v["published"]}
                   for v, cs in zip(picked, per_video) if cs]
        batch = await pipeline.ingest(threads, comments) if threads else {"new": 0, "alerts": []}

    out_videos, flagged_accounts = [], set()
    for v, cs in [*zip(picked, per_video), *((v, []) for v in skipped)]:
        row = {k: v.get(k) for k in ("video_id", "title", "channel", "published_at", "url", "query", "status", "message", "comment_count")}
        row["comments"] = len(cs)
        if cs:
            row["thread_id"] = f"yt:{v['video_id']}"
            row.update(await asyncio.to_thread(_scan_summary, row["thread_id"]))
            flagged_accounts.update(row["flagged_accounts"])
            row["flagged_accounts"] = len(row["flagged_accounts"])
        out_videos.append({k: val for k, val in row.items() if val is not None})
    ranked = sorted((v for v in out_videos if v.get("thread_id")),
                    key=lambda v: (v.get("level") or 9, -v.get("flagged_comments", 0)))
    return {"queries": queries, "order": order, "videos_found": len(videos), "videos_scanned": len(picked),
            "videos_with_comments": len(threads), "comments": len(comments), "new": batch["new"],
            "flagged_accounts": len(flagged_accounts), "clusters": sum(v.get("clusters", 0) for v in out_videos),
            "alerts": len([a for a in batch.get("alerts", []) if a]), "channels_enriched": enriched,
            "top_thread_id": ranked[0]["thread_id"] if ranked else None, "videos": out_videos, "warnings": warnings,
            "quota": {"units": stats.get("units", 0), "calls": stats.get("calls", 0), "cached": stats.get("cached", 0)}}


async def autoscan_loop() -> None:
    period = settings.youtube_autoscan_every_min * 60
    if period <= 0 or not settings.youtube_api_key:
        return
    while True:
        try:
            r = await run_youtube_autoscan(settings.youtube_autoscan_queries, settings.youtube_api_key)
            log.info("YouTube auto-scan: %s videos, %s comments, %s flagged accounts, %s units",
                     r["videos_scanned"], r["comments"], r["flagged_accounts"], r["quota"]["units"])
        except ingest.YouTubeError as exc:
            log.warning("YouTube auto-scan failed (%s)", exc.reason or exc.status)
        except Exception:
            log.exception("YouTube auto-scan crashed; will retry")
        await asyncio.sleep(max(period, 300))


async def recovery_loop() -> None:
    while True:
        try:
            await pipeline.process_pending()
        except Exception:
            log.exception("Pending analysis failed; will retry")
        await asyncio.sleep(15)


async def alert_loop() -> None:
    while True:
        try:
            for update in await asyncio.to_thread(pipeline.alerts.deliver_pending):
                await pipeline._broadcast({"type": "alert_delivery", **update})
        except Exception:
            log.exception("Alert delivery worker failed; will retry")
        await asyncio.sleep(5)


@contextlib.asynccontextmanager
async def lifespan(_app: FastAPI):
    global telegram_fetcher
    if not settings.api_key and not settings.allow_unauthenticated:
        log.warning("API_KEY is not set: every analyst endpoint (threads, YouTube fetch/auto-scan) will return 503")
    if not settings.youtube_api_key:
        log.warning("YOUTUBE_API_KEY is not set: YouTube fetch/auto-scan need a per-request api_key from the dashboard")
    tasks = [asyncio.create_task(task) for task in (source_loop("youtube"), source_loop("telegram"),
                                                   recovery_loop(), alert_loop(), autoscan_loop())]
    try:
        yield
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        if telegram_fetcher:
            await telegram_fetcher.client.disconnect()
            telegram_fetcher = None
        tickets.clear()


app = FastAPI(title="Hybrid Threat & Coordinated Campaign Intelligence Platform API", version="1.1", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=settings.cors_origins, allow_origin_regex=settings.cors_origin_regex or None,
                   allow_methods=["GET", "POST", "PATCH"], allow_headers=["Content-Type", "X-API-Key"])


@app.middleware("http")
async def prevent_api_caching(request, call_next):
    response = await call_next(request)
    if request.url.path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-store"
    return response


def authorized(value: str | None, expected: str) -> bool:
    if not expected:
        return settings.allow_unauthenticated
    return bool(value and hmac.compare_digest(value.encode(), expected.encode()))


def require_key(x_api_key: str | None = Header(default=None)):
    if not settings.api_key and not settings.allow_unauthenticated:
        raise HTTPException(503, "Backend has no analyst API_KEY configured: set API_KEY in backend/.env or the Render environment")
    if not authorized(x_api_key, settings.api_key):
        raise HTTPException(401, "Missing or invalid analyst API key")


def require_ingest_key(x_api_key: str | None = Header(default=None)):
    if not authorized(x_api_key, settings.ingest_api_key or settings.api_key):
        raise HTTPException(401, "Missing or invalid ingestion API key")


ShortText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
CommentText = Annotated[str, StringConstraints(min_length=1, max_length=12000)]
Decision = Literal["likely_coordinated", "organic", "inconclusive"]


def timestamp_ms(value):
    if value is None:
        return None
    if isinstance(value, str):
        if value.strip().lstrip("-").replace(".", "", 1).isdigit():
            value = float(value)
        else:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            value = parsed.timestamp() * 1000
            if not 0 <= value <= 253402300799999:
                raise ValueError("Timestamp must be between 1970 and 9999")
            return value
    if not math.isfinite(value) or not 0 <= value <= 253402300799999:
        raise ValueError("Timestamp must be a finite epoch value or ISO 8601")
    return value


class AccountIn(BaseModel):
    created: float | str | None = None
    followers: int | None = Field(None, ge=0)
    friends: list[ShortText] | None = Field(None, max_length=500)

    _validate_created = field_validator("created")(timestamp_ms)


class InfraIn(BaseModel):
    node: ShortText
    type: Literal["residential", "vpn", "datacenter", "proxy", "unknown"]
    asn: ShortText | None = None
    ip: IPvAnyAddress | None = None
    city: ShortText | None = None
    cc: Annotated[str, StringConstraints(min_length=2, max_length=2)] | None = None
    lat: float | None = Field(None, ge=-90, le=90)
    lon: float | None = Field(None, ge=-180, le=180)


class CommentIn(BaseModel):
    id: ShortText | None = None
    user: ShortText
    ts: float | str | None = None
    text: CommentText
    source_text: CommentText | None = None
    url: HttpUrl | None = None
    account: AccountIn | None = None
    infra: InfraIn | None = None

    _validate_ts = field_validator("ts")(timestamp_ms)


class PortalCommentIn(CommentIn):
    ip: IPvAnyAddress | None = None


class ThreadIn(BaseModel):
    thread_id: ShortText
    title: ShortText | None = None
    url: HttpUrl | None = None
    published: float | str | None = None

    _validate_published = field_validator("published")(timestamp_ms)


class BatchIn(ThreadIn):
    platform: Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_-]{0,29}$")] = "manual"
    comments: list[CommentIn] = Field(max_length=500)


class PortalIn(ThreadIn):
    comments: list[PortalCommentIn] = Field(max_length=500)


class SimulateIn(BaseModel):
    thread_id: ShortText | None = None
    spread_threads: int = Field(2, ge=1, le=6)
    accounts: int = Field(12, ge=3, le=60)
    window_s: int = Field(240, ge=30, le=1800)
    lang: Literal["en", "sq"] = "en"
    background: int = Field(6, ge=0, le=50)
    include_infra: bool = True
    include_accounts: bool = True
    scenario: Literal["kfor", "energy"] = "kfor"


class YouTubeIn(BaseModel):
    youtube_url_or_id: Annotated[str, StringConstraints(strip_whitespace=True, min_length=11, max_length=300)]
    # Optional per-request key; never stored or logged. Falls back to YOUTUBE_API_KEY.
    api_key: Annotated[str, StringConstraints(strip_whitespace=True, pattern=r"^[A-Za-z0-9_-]{20,80}$")] | None = None
    max_pages: int = Field(3, ge=1, le=10)
    enrich_channels: bool = True


YT_ERRORS = {  # YouTube error reason -> (HTTP status, analyst-facing message)
    "missingKey": (400, "No YouTube API key: enter one in the dashboard or set YOUTUBE_API_KEY on the backend"),
    "keyInvalid": (400, "The YouTube API key is invalid"), "badRequest": (400, "The YouTube API key is invalid or the request was rejected"),
    "accessNotConfigured": (403, "YouTube Data API v3 is not enabled for this API key's project"),
    "quotaExceeded": (429, "YouTube API quota exceeded for this key, try again tomorrow or use another key"),
    "dailyLimitExceeded": (429, "YouTube API daily limit exceeded for this key"),
    "rateLimitExceeded": (429, "YouTube API rate limit hit, try again shortly"),
    "commentsDisabled": (409, "Comments are disabled on this video"),
    "videoNotFound": (404, "Video not found, private or removed"),
    "forbidden": (403, "YouTube refused access to this video's comments"),
    "unreachable": (502, "Could not reach the YouTube API"),
}


class YouTubeAutoScanIn(BaseModel):
    # Blank = server defaults (YOUTUBE_AUTOSCAN_QUERIES). Each query costs 100 quota units unless cached.
    queries: list[Annotated[str, StringConstraints(strip_whitespace=True, min_length=2, max_length=120)]] = Field(default_factory=list, max_length=5)
    api_key: Annotated[str, StringConstraints(strip_whitespace=True, pattern=r"^[A-Za-z0-9_-]{20,80}$")] | None = None
    order: Literal["date", "relevance", "viewCount"] = "date"
    max_videos: int = Field(10, ge=1, le=25)
    comment_pages: int = Field(1, ge=1, le=4)
    lookback_h: int | None = Field(None, ge=0, le=24 * 365)
    enrich_channels: bool = True


def youtube_http_error(exc: "ingest.YouTubeError") -> HTTPException:
    status, message = YT_ERRORS.get(exc.reason, (502, f"YouTube API error ({exc.reason or exc.status or 'unknown'})"))
    return HTTPException(status, message)


class LabAccountIn(BaseModel):
    age_days: int | None = Field(None, ge=0, le=20000)
    followers: int | None = Field(None, ge=0, le=100_000_000)
    posts_24h: int | None = Field(None, ge=0, le=100_000)
    template_hits: int | None = Field(None, ge=0, le=100_000)
    friend_overlap: float | None = Field(None, ge=0, le=1)
    in_burst: bool | None = None


class LabCaseIn(BaseModel):
    id: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=64)]
    text: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=1000)]
    lang: Annotated[str, StringConstraints(max_length=8)] | None = None
    account: LabAccountIn | None = None


class LabIn(BaseModel):
    cases: list[LabCaseIn] = Field(..., min_length=1, max_length=40)


class SourceIn(BaseModel):
    kind: Literal["youtube", "telegram"]
    ref: ShortText


class CaseIn(BaseModel):
    title: ShortText
    owner: ShortText | None = None
    simulated: bool = False
    status: Literal["open", "dismissed"] = "open"
    thread_ids: list[ShortText] = Field(min_length=1, max_length=30)


class CasePatch(BaseModel):
    title: ShortText | None = None
    owner: ShortText | None = None
    status: Literal["open", "reviewing", "closed", "dismissed"] | None = None
    link: list[ShortText] = Field(default_factory=list, max_length=30)
    unlink: list[ShortText] = Field(default_factory=list, max_length=30)


class ReviewIn(BaseModel):
    decision: Decision
    reviewer: ShortText
    reason: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2000)]


@app.get("/api/health")
async def health():
    return {"ok": True, "contract": "1", "release": "1.1", "revision": os.getenv("RENDER_GIT_COMMIT"),
            "embedder": pipeline.embedder.kind, "alerts": "dry-run" if settings.alert_dry_run else "live",
            "auth_required": bool(settings.api_key or not settings.allow_unauthenticated),
            "analyst_key_configured": bool(settings.api_key),
            "youtube": bool(settings.youtube_api_key), "features": ["youtube_osint", "youtube_autoscan"], "telegram": bool(settings.tg_api_id and settings.tg_api_hash),
            "llm": settings.llm_enabled and bool(settings.anthropic_api_key),
            "llm_model": settings.llm_model if evaluator.available() else None}


@app.get("/api/threads", dependencies=[Depends(require_key)])
async def threads():
    return await asyncio.to_thread(pipeline.store.list_threads)


@app.get("/api/threads/{tid:path}/analysis", dependencies=[Depends(require_key)])
async def thread_analysis(tid: str):
    analysis = await pipeline.analysis(tid)
    if analysis is None:
        raise HTTPException(404, "Unknown thread")
    return analysis


@app.get("/api/threads/{tid:path}/sitrep", response_class=PlainTextResponse, dependencies=[Depends(require_key)])
async def thread_sitrep(tid: str):
    report = await pipeline.sitrep(tid)
    if report is None:
        raise HTTPException(404, "Unknown thread")
    return report


@app.get("/api/graph", dependencies=[Depends(require_key)])
async def graph(simulated: bool = False):
    return await asyncio.to_thread(pipeline.global_graph, simulated)


@app.get("/api/infrastructure", dependencies=[Depends(require_key)])
async def infrastructure(simulated: bool = False, thread_id: str | None = None):
    def collect():
        return {**globe_points(pipeline.store.infrastructure_snapshots(simulated, thread_id)), "simulated": simulated}
    return await asyncio.to_thread(collect)


def normalize_batch(batch: ThreadIn, platform: str, portal: bool = False) -> tuple[dict, list[dict]]:
    if portal:
        tid = f"pt:{batch.thread_id}"
    else:
        legacy_tid = f"ext:{batch.thread_id}"
        previous = pipeline.store.get_thread(legacy_tid)
        if (previous and previous["platform"] == platform) or (not previous and platform == "manual"):
            tid = legacy_tid
        else:
            tid = f"ext-src:{platform}:{batch.thread_id}"
    body = batch.model_dump(mode="json", exclude_none=True)
    thread = {"id": tid, "external_id": batch.thread_id, "platform": platform,
              **{key: body.get(key) for key in ("title", "url", "published")}}
    comments = []
    for c in body["comments"]:
        user, account = ingest.pseudonymize_account(platform, c["user"], c.get("account"))
        if portal:
            fingerprint = f"{tid}:{c['user']}:{c.get('ts')}:{c['text']}"
            legacy_id = "pt:" + (c.get("id") or hashlib.sha1(fingerprint.encode()).hexdigest()[:16])
        else:
            fingerprint = f"{c['user']}|{c.get('ts')}|{c['text']}"
            legacy_id = c.get("id") or f"{tid}:" + hashlib.sha1(fingerprint.encode()).hexdigest()[:16]
        infra = geo.lookup(c["ip"]) if portal and c.get("ip") else c.get("infra")
        if infra and (not portal or not c.get("ip")) and infra.get("ip"):
            infra["ip"] = ingest.truncate_ip(infra["ip"])
        comments.append({"id": ingest.scoped_comment_id(tid, platform, legacy_id), "legacy_id": legacy_id,
                         "thread_id": tid, "platform": platform, "user": user, "account": account, "infra": infra,
                         **{key: c.get(key) for key in ("ts", "text", "source_text", "url")}})
    return thread, comments


@app.post("/api/ingest/comments", dependencies=[Depends(require_ingest_key)])
async def ingest_comments(batch: BatchIn):
    thread, comments = normalize_batch(batch, batch.platform)
    return await pipeline.ingest([thread], comments)


@app.post("/api/ingest/portal", dependencies=[Depends(require_ingest_key)])
async def ingest_portal(batch: PortalIn):
    thread, comments = normalize_batch(batch, "portal", portal=True)
    return await pipeline.ingest([thread], comments)


@app.post("/api/simulate-attack", dependencies=[Depends(require_key)])
async def simulate_attack(body: SimulateIn):
    tids = [body.thread_id] if body.thread_id else []
    if tids:
        existing = pipeline.store.get_thread(tids[0])
        if not existing or not existing["simulated"]:
            raise HTTPException(409, "Select an existing exercise thread")
    run = uuid.uuid4().hex[:12]
    tids += [f"sim:{body.scenario}-{run}-{i}" for i in range(body.spread_threads - len(tids))]
    title = ingest.SCENARIOS[body.scenario]["title"]
    now = time.time() * 1000
    thread_records = [
        {"id": tid, "platform": "simulated", "title": f"[EXERCISE] {title}" + (f" (mirror {i})" if i else ""),
         "simulated": True, "published": now - ((len(tids) - 1 - i) * 25 + 3) * 60_000}
        for i, tid in enumerate(tids) if not pipeline.store.get_thread(tid)
    ]
    comments = ingest.simulate_swarm(tids, accounts=body.accounts, window_s=body.window_s, lang=body.lang,
                                     include_infra=body.include_infra, include_accounts=body.include_accounts,
                                     background=body.background, scenario=body.scenario, start_ms=now)
    result = await pipeline.ingest(thread_records, comments)
    return {**result, "thread_ids": tids, "primary": tids[-1]}


@app.post("/api/osint/youtube", dependencies=[Depends(require_key)])
async def osint_youtube(body: YouTubeIn):
    """One-shot fetch of a public video's comments (YouTube Data API v3 commentThreads.list) into the pipeline."""
    video_id = ingest.parse_video_id(body.youtube_url_or_id)
    if not video_id:
        raise HTTPException(422, "Enter a YouTube URL (watch?v=, youtu.be/, /shorts/, /live/) or an 11-character video ID")
    key = body.api_key or settings.youtube_api_key or None
    if not key:
        raise youtube_http_error(ingest.YouTubeError("missing key", reason="missingKey"))
    try:
        metadata = await asyncio.to_thread(ingest.yt_video_meta, [video_id], key)
        if video_id not in metadata:
            raise youtube_http_error(ingest.YouTubeError("not found", reason="videoNotFound"))
        comments = await asyncio.to_thread(ingest.yt_fetch_comments, video_id, None, body.max_pages, key)
    except ingest.YouTubeError as exc:
        log.warning("YouTube fetch failed for %s (%s)", video_id, exc.reason or exc.status)
        raise youtube_http_error(exc) from None
    enriched = False
    if comments and body.enrich_channels:
        try:
            await asyncio.to_thread(ingest.yt_enrich_accounts, comments, key)
            enriched = True
        except ingest.YouTubeError:
            log.warning("Channel enrichment skipped for %s", video_id)
    ingest.strip_private(comments)
    meta = metadata[video_id]
    thread = {"id": f"yt:{video_id}", "platform": "youtube", "external_id": video_id,
              "url": f"https://www.youtube.com/watch?v={video_id}", **meta}
    batch = await pipeline.ingest([thread], comments)
    liked = sorted((c for c in comments if c.get("likes")), key=lambda c: -c["likes"])[:3]
    return {"thread_id": thread["id"], "video_id": video_id, "title": meta.get("title"), "channel": meta.get("channel"),
            "comments": len(comments), "new": batch["new"], "channels_enriched": enriched,
            "top_liked": [{"user": c["user"], "likes": c["likes"]} for c in liked]}


@app.post("/api/osint/youtube/auto-scan", dependencies=[Depends(require_key)])
async def osint_youtube_autoscan(body: YouTubeAutoScanIn):
    """Discover relevant recent videos (search.list), fetch their comments (commentThreads.list),
    and run coordinated-campaign detection on each video thread."""
    key = body.api_key or settings.youtube_api_key or None
    if not key:
        raise youtube_http_error(ingest.YouTubeError("missing key", reason="missingKey"))
    queries = list(dict.fromkeys(body.queries)) or settings.youtube_autoscan_queries
    try:
        return await run_youtube_autoscan(queries, key, order=body.order, max_videos=body.max_videos,
                                          comment_pages=body.comment_pages, lookback_h=body.lookback_h,
                                          enrich=body.enrich_channels)
    except ingest.YouTubeError as exc:
        log.warning("YouTube auto-scan failed (%s)", exc.reason or exc.status)
        raise youtube_http_error(exc) from None


@app.get("/api/osint/youtube/auto-scan/defaults", dependencies=[Depends(require_key)])
async def osint_youtube_autoscan_defaults():
    return {"queries": settings.youtube_autoscan_queries, "lookback_h": settings.youtube_autoscan_lookback_h,
            "cache_s": settings.youtube_autoscan_cache_s, "server_key": bool(settings.youtube_api_key)}


@app.post("/api/lab/evaluate", dependencies=[Depends(require_key)])
async def lab_evaluate(body: LabIn):
    """Second-opinion bot/human verdicts from the configured LLM for the dashboard's evaluation lab."""
    if not evaluator.available():
        raise HTTPException(503, "LLM reviewer not configured (set LLM_ENABLED=1 and ANTHROPIC_API_KEY)")
    cases = [c.model_dump(exclude_none=True) for c in body.cases]
    return await asyncio.to_thread(evaluator.evaluate, cases)


@app.get("/api/sources", dependencies=[Depends(require_key)])
async def sources():
    now = time.time()
    return [{**source, "stale_for_s": int(max(0, now - source["last_success"])) if source["last_success"] else None}
            for source in pipeline.store.sources(include_paused=True)]


@app.post("/api/sources", dependencies=[Depends(require_key)])
async def add_source(source: SourceIn):
    ref = source.ref.lstrip("@").lower() if source.kind == "telegram" else source.ref
    pattern = r"[a-z0-9_]{1,32}" if source.kind == "telegram" else r"[A-Za-z0-9_-]{11}"
    if not re.fullmatch(pattern, ref):
        raise HTTPException(422, "Enter a YouTube video ID or public Telegram channel username")
    return pipeline.store.add_source(source.kind, ref)


@app.post("/api/sources/youtube/poll", dependencies=[Depends(require_key)])
async def poll_now():
    return await poll_sources("youtube")


@app.post("/api/sources/{sid}/{action}", dependencies=[Depends(require_key)])
async def source_action(sid: str, action: Literal["pause", "resume", "retry", "backfill"]):
    source = pipeline.store.source(sid)
    if not source:
        raise HTTPException(404, "Unknown source")
    if action in ("pause", "resume"):
        async with source_locks[source["kind"]]:
            pipeline.store.set_source_active(sid, action == "resume")
        return pipeline.store.source(sid)
    return await poll_sources(source["kind"], sid=sid, backfill=action == "backfill")


@app.get("/api/alerts", dependencies=[Depends(require_key)])
async def alerts():
    return pipeline.store.alerts()


@app.post("/api/alerts/{alert_id}/acknowledge", dependencies=[Depends(require_key)])
async def acknowledge(alert_id: int):
    if not pipeline.store.acknowledge_alert(alert_id):
        raise HTTPException(404, "Unknown alert")
    return {"acknowledged": True}


@app.post("/api/alerts/test/{tid:path}", dependencies=[Depends(require_key)])
async def alert_test(tid: str):
    analysis = await pipeline.analysis(tid)
    if analysis is None:
        raise HTTPException(404, "Unknown thread")
    return await asyncio.to_thread(pipeline.alerts.evaluate, analysis["meta"]["thread"], analysis, force=True)


def find_case(case_id: int) -> dict:
    case = pipeline.store.case(case_id)
    if not case:
        raise HTTPException(404, "Unknown campaign case")
    return case


async def collect_evidence(thread_ids: list[str], simulated: bool):
    try:
        return await pipeline.case_evidence(thread_ids, simulated)
    except LookupError as exc:
        raise HTTPException(404, f"Unknown thread: {exc.args[0]}") from exc
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc


@app.get("/api/cases", dependencies=[Depends(require_key)])
async def cases(simulated: bool = False):
    return pipeline.store.cases(simulated)


@app.get("/api/cases/suggestions", dependencies=[Depends(require_key)])
async def suggestions(simulated: bool = False):
    return await asyncio.to_thread(pipeline.case_suggestions, simulated)


@app.get("/api/cases/feedback", dependencies=[Depends(require_key)])
async def case_feedback(simulated: bool = False):
    counts = pipeline.store.review_counts(simulated)
    return {"reviewed_cases": sum(counts.values()), "decisions": counts}


@app.post("/api/cases", status_code=201, dependencies=[Depends(require_key)])
async def create_case(case: CaseIn):
    evidence = await collect_evidence(case.thread_ids, case.simulated)
    return find_case(pipeline.store.create_case(case.title, case.owner, case.simulated, evidence, status=case.status))


@app.get("/api/cases/{case_id}", dependencies=[Depends(require_key)])
async def case_detail(case_id: int):
    return find_case(case_id)


@app.patch("/api/cases/{case_id}", dependencies=[Depends(require_key)])
async def edit_case(case_id: int, changes: CasePatch):
    case = find_case(case_id)
    if set(changes.link) & set(changes.unlink):
        raise HTTPException(422, "A thread cannot be linked and unlinked in the same request")
    evidence = await collect_evidence(changes.link, bool(case["simulated"]))
    fields = changes.model_dump(exclude_unset=True, exclude={"link", "unlink"})
    if any(field in fields and fields[field] is None for field in ("title", "status")):
        raise HTTPException(422, "Title and status cannot be empty")
    pipeline.store.update_case(case_id, fields, evidence, changes.unlink)
    return find_case(case_id)


@app.post("/api/cases/{case_id}/reviews", dependencies=[Depends(require_key)])
async def review_case(case_id: int, review: ReviewIn):
    find_case(case_id)
    pipeline.store.add_review(case_id, review.decision, review.reason, review.reviewer)
    return find_case(case_id)


@app.get("/api/cases/{case_id}/sitrep", response_class=PlainTextResponse, dependencies=[Depends(require_key)])
async def case_sitrep(case_id: int):
    case = find_case(case_id)
    lines = [f"CAMPAIGN CASE {case_id} — {'EXERCISE' if case['simulated'] else 'ANALYST REVIEW'}",
             case["title"], f"Status: {case['status']} | Owner: {case['owner'] or 'Unassigned'}",
             "Signals indicate possible coordination. Attribution requires independent evidence.", ""]
    for tid in case["thread_ids"]:
        evidence = case["evidence"][tid]
        thread = evidence["thread"]
        lines.extend([f"THREAD {tid} — {thread.get('title') or tid}", thread.get("url") or "",
                      f"Level {evidence['score']['level']} | {evidence['score']['n']} comments | scoring {evidence['scoring_version']}"])
        for comment in evidence["comments"][:5]:
            lines.append(f"- Score {comment['score']} | {comment.get('url') or comment['id']} | {comment['text'][:200]}")
        lines.append("")
    for review in case["reviews"]:
        lines.append(f"REVIEW {review['reviewer']}: {review['decision']} — {review['reason']}")
    return "\n".join(lines)


@app.post("/api/ws-ticket", dependencies=[Depends(require_key)])
async def ws_ticket():
    now = time.monotonic()
    for token, expires in list(tickets.items()):
        if now >= expires:
            tickets.pop(token, None)
    if len(tickets) >= 256:
        raise HTTPException(429, "Too many pending connections")
    token = secrets.token_urlsafe(24)
    tickets[token] = now + 30
    return {"ticket": token}


@app.websocket("/ws/live")
async def ws_live(ws: WebSocket):
    expires = tickets.pop(ws.query_params.get("ticket", ""), 0)
    if time.monotonic() >= expires:
        return await ws.close(code=4401)
    await ws.accept()
    async def push(message: dict):
        await ws.send_json(message)

    try:
        await ws.send_json({"type": "hello", "threads": await threads()})
        pipeline.subscribe(push)
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        pipeline.unsubscribe(push)


@app.get("/", response_class=HTMLResponse)
@app.get("/index.html", response_class=HTMLResponse, include_in_schema=False)  # same page; workspace links use ./index.html
async def dashboard():
    path = os.path.abspath(os.path.join(os.path.dirname(__file__), settings.dashboard_path))
    if os.path.isfile(path):
        return FileResponse(path, media_type="text/html")
    return HTMLResponse("<h1>Dashboard unavailable</h1>", status_code=404)


@app.get("/classic.html", include_in_schema=False)
@app.get("/monitor.html", include_in_schema=False)
@app.get("/workspace.html", include_in_schema=False)
@app.get("/investigation.html", include_in_schema=False)
@app.get("/campaign.html", include_in_schema=False)
@app.get("/stitch-bridge.js", include_in_schema=False)
@app.get("/stitch-bridge.css", include_in_schema=False)
@app.get("/operations.js", include_in_schema=False)
@app.get("/workspace.js", include_in_schema=False)
@app.get("/workspace-data.js", include_in_schema=False)
@app.get("/workspace.css", include_in_schema=False)
async def dashboard_asset(request: Request):
    path = os.path.abspath(os.path.join(os.path.dirname(__file__), settings.dashboard_path))
    asset = os.path.join(os.path.dirname(path), os.path.basename(request.url.path))
    if not os.path.isfile(asset):
        raise HTTPException(404, "Dashboard asset unavailable")
    return FileResponse(asset)
