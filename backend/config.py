"""Runtime configuration (environment variables / .env)."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

try:
    from dotenv import load_dotenv  # type: ignore

    # backend/.env wins regardless of the working directory uvicorn was started from;
    # real environment variables (Render) still take precedence over both.
    load_dotenv(Path(__file__).resolve().with_name(".env"))
    load_dotenv()
except Exception:  # pragma: no cover
    pass


def _bool(name: str, default: bool) -> bool:
    v = os.getenv(name)
    return default if v is None else v.strip().lower() in ("1", "true", "yes", "on")


def _list(name: str) -> list[str]:
    return [x.strip() for x in os.getenv(name, "").split(",") if x.strip()]


@dataclass
class Settings:
    # --- security ---
    api_key: str = os.getenv("API_KEY", "").strip()
    ingest_api_key: str = os.getenv("INGEST_API_KEY", "")
    allow_unauthenticated: bool = _bool("ALLOW_UNAUTHENTICATED", False)
    # Local development only: lets the dashboard served by THIS backend on localhost fetch API_KEY
    # automatically (GET /api/dev/analyst-key). Refused for any non-loopback client, host or origin.
    dev_auto_analyst_key: bool = _bool("DEV_AUTO_ANALYST_KEY", False)
    cors_origins: list[str] = field(default_factory=lambda: _list("CORS_ORIGINS") or ["http://localhost:8000", "http://127.0.0.1:8000", "http://localhost:5173"])
    # Lets the Vercel-hosted dashboard (production + preview URLs) call this API cross-origin.
    # No cookies are used and writes still need X-API-Key. Set CORS_ORIGIN_REGEX="" to disable,
    # or pin it to your own project, e.g. https://my-dashboard(-[\w-]+)?\.vercel\.app
    cors_origin_regex: str = os.getenv("CORS_ORIGIN_REGEX", r"https://[\w-]+\.vercel\.app")
    pseudonym_secret: str = os.getenv("PSEUDONYM_SECRET", "change-me-before-the-demo")
    keep_display_names: bool = _bool("KEEP_DISPLAY_NAMES", False)

    # --- storage ---
    db_path: str = os.getenv("DB_PATH", "htccip.db")

    # --- YouTube Data API v3 ---
    youtube_api_key: str = os.getenv("YOUTUBE_API_KEY", "").strip()
    youtube_video_ids: list[str] = field(default_factory=lambda: _list("YOUTUBE_VIDEO_IDS"))
    youtube_search_query: str = os.getenv("YOUTUBE_SEARCH_QUERY", "")  # e.g. "Kosovo Serbia dialogue KFOR"
    youtube_search_lang: str = os.getenv("YOUTUBE_SEARCH_LANG", "sq")
    youtube_poll_s: int = int(os.getenv("YOUTUBE_POLL_S", "90"))
    youtube_enrich_channels: bool = _bool("YOUTUBE_ENRICH_CHANNELS", True)
    # Auto-scan (search.list -> commentThreads.list -> detection). Queries are ";"-separated.
    youtube_autoscan_queries: list[str] = field(default_factory=lambda: [
        q.strip() for q in os.getenv("YOUTUBE_AUTOSCAN_QUERIES", "").split(";") if q.strip()
    ] or ["Kosovo Serbia", "Kosova Serbia", "Kosovo Srbija", "Kosovo NATO KFOR"])
    youtube_autoscan_lookback_h: int = int(os.getenv("YOUTUBE_AUTOSCAN_LOOKBACK_H", "168"))
    youtube_autoscan_cache_s: int = int(os.getenv("YOUTUBE_AUTOSCAN_CACHE_S", "900"))
    # Unattended auto-scan period in minutes; 0 = off. Each run costs ~100 units per query.
    youtube_autoscan_every_min: int = int(os.getenv("YOUTUBE_AUTOSCAN_EVERY_MIN", "0"))

    # --- Telegram (Telethon user client for PUBLIC channels; get api_id/hash at my.telegram.org) ---
    tg_api_id: int = int(os.getenv("TG_API_ID", "0") or 0)
    tg_api_hash: str = os.getenv("TG_API_HASH", "")
    tg_session: str = os.getenv("TG_SESSION", "htccip")
    tg_channels: list[str] = field(default_factory=lambda: _list("TG_CHANNELS"))
    tg_poll_s: int = int(os.getenv("TG_POLL_S", "120"))

    # --- Telegram bot alerting ---
    alert_bot_token: str = os.getenv("ALERT_BOT_TOKEN", "")
    alert_chat_id: str = os.getenv("ALERT_CHAT_ID", "")
    alert_exercise_chat_id: str = os.getenv("ALERT_EXERCISE_CHAT_ID", "")
    alert_cooldown_s: int = int(os.getenv("ALERT_COOLDOWN_S", "900"))
    alert_dry_run: bool = _bool("ALERT_DRY_RUN", True)  # log instead of sending until you switch it off
    alert_on_simulated: bool = _bool("ALERT_ON_SIMULATED", True)  # simulated swarms are always tagged EXERCISE

    # --- engine ---
    coactivity_window_s: int = int(os.getenv("COACTIVITY_WINDOW_S", "300"))
    coactivity_min_threads: int = int(os.getenv("COACTIVITY_MIN_THREADS", "2"))
    graph_lookback_h: int = int(os.getenv("GRAPH_LOOKBACK_H", "48"))

    # --- optional LLM-assisted calque review (Anthropic API) ---
    llm_enabled: bool = _bool("LLM_ENABLED", False)
    anthropic_api_key: str = os.getenv("ANTHROPIC_API_KEY", "")
    llm_model: str = os.getenv("LLM_MODEL", "claude-haiku-5-5")
    llm_min_score: int = int(os.getenv("LLM_MIN_SCORE", "35"))  # only review comments already at/above this score

    # --- optional infrastructure enrichment for YOUR OWN portal's logs (MaxMind GeoLite2 .mmdb files) ---
    geoip_asn_db: str = os.getenv("GEOIP_ASN_DB", "")
    geoip_city_db: str = os.getenv("GEOIP_CITY_DB", "")

    # --- dashboard ---
    dashboard_path: str = os.getenv("DASHBOARD_PATH", "../frontend/index.html")


settings = Settings()
