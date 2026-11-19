"""Data ingestion: YouTube Data API v3, Telegram public channels (Telethon),
your own portal's comment webhook, and the Live Attack Simulator.

Privacy by design: author IDs are pseudonymised with HMAC-SHA256 before storage,
display names are dropped unless KEEP_DISPLAY_NAMES=true, and portal IPs are
truncated to /24 before anything is stored.

CLI:
    python ingest.py youtube <VIDEO_ID>      # one-off fetch, prints a summary
    python ingest.py telegram-login          # first-time Telethon login (phone + code)
    python ingest.py simulate                # print a simulated swarm as JSON
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import ipaddress
import json
import logging
import random
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

from config import settings

log = logging.getLogger("htccip.ingest")
YT = "https://www.googleapis.com/youtube/v3/"


# ---------------------------------------------------------------------------
# privacy helpers
# ---------------------------------------------------------------------------
def pseudonym(platform: str, raw_id: str, display: str | None = None) -> str:
    h = hmac.new(settings.pseudonym_secret.encode(), f"{platform}:{raw_id}".encode(), hashlib.sha256).hexdigest()[:12]
    prefix = {"youtube": "yt", "telegram": "tg", "portal": "pt", "simulated": "sim"}.get(platform, platform[:3])
    if settings.keep_display_names and display:
        safe = "".join(ch for ch in display if ch.isalnum() or ch in "._-")[:20]
        return f"{prefix}_{safe}_{h[:4]}"
    return f"{prefix}_{h}"


def iso_to_ms(s: str | None) -> float | None:
    if not s:
        return None
    return datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp() * 1000


def truncate_ip(ip: str) -> str:
    try:
        a = ipaddress.ip_address(ip)
    except ValueError:
        return "invalid"
    net = ipaddress.ip_network(f"{ip}/{24 if a.version == 4 else 48}", strict=False)
    return str(net)


# ---------------------------------------------------------------------------
# YouTube Data API v3
# Quota (default 10,000 units/day): commentThreads.list = 1, videos.list = 1,
# channels.list = 1, search.list = 100 (use search sparingly).
# ---------------------------------------------------------------------------
class YouTubeError(RuntimeError):
    pass


def _yt_get(endpoint: str, params: dict) -> dict:
    if not settings.youtube_api_key:
        raise YouTubeError("YOUTUBE_API_KEY not set")
    q = urllib.parse.urlencode({**params, "key": settings.youtube_api_key})
    req = urllib.request.Request(YT + endpoint + "?" + q, headers={"Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        body = e.read().decode(errors="ignore")
        reason = ""
        try:
            reason = json.loads(body)["error"]["errors"][0]["reason"]
        except Exception:
            pass
        raise YouTubeError(f"{endpoint} HTTP {e.code} {reason}".strip()) from None


def yt_search_videos(query: str, published_after_h: int = 24, lang: str = "sq", max_results: int = 10) -> list[str]:
    after = (datetime.now(timezone.utc) - timedelta(hours=published_after_h)).strftime("%Y-%m-%dT%H:%M:%SZ")
    d = _yt_get("search", {"part": "id", "type": "video", "q": query, "order": "date", "publishedAfter": after,
                           "relevanceLanguage": lang, "maxResults": max_results})
    return [it["id"]["videoId"] for it in d.get("items", []) if it.get("id", {}).get("videoId")]


def yt_video_meta(video_ids: list[str]) -> dict[str, dict]:
    out = {}
    for i in range(0, len(video_ids), 50):
        d = _yt_get("videos", {"part": "snippet", "id": ",".join(video_ids[i:i + 50])})
        for it in d.get("items", []):
            sn = it["snippet"]
            out[it["id"]] = {"title": sn.get("title"), "channel": sn.get("channelTitle"), "published": iso_to_ms(sn.get("publishedAt"))}
    return out


def yt_fetch_comments(video_id: str, since_ms: float | None = None, max_pages: int = 10) -> list[dict]:
    """Newest-first; stops paging once comments are older than `since_ms`."""
    out, token = [], None
    for _ in range(max_pages):
        params = {"part": "snippet,replies", "videoId": video_id, "order": "time", "maxResults": 100, "textFormat": "plainText"}
        if token:
            params["pageToken"] = token
        try:
            d = _yt_get("commentThreads", params)
        except YouTubeError as e:
            if "commentsDisabled" in str(e):
                log.info("comments disabled on %s", video_id)
                return out
            raise
        stop = False
        for th in d.get("items", []):
            top = th["snippet"]["topLevelComment"]
            entries = [top] + (th.get("replies", {}) or {}).get("comments", [])
            for c in entries:
                sn = c["snippet"]
                ts = iso_to_ms(sn.get("publishedAt"))
                if since_ms and ts and ts <= since_ms:
                    if c is top:
                        stop = True
                    continue
                raw = (sn.get("authorChannelId") or {}).get("value") or sn.get("authorDisplayName", "unknown")
                out.append({
                    "id": f"yt:{c['id']}", "thread_id": f"yt:{video_id}", "platform": "youtube",
                    "user": pseudonym("youtube", raw, sn.get("authorDisplayName")), "_raw_channel": raw,
                    "ts": ts, "text": sn.get("textOriginal") or sn.get("textDisplay") or "",
                    "url": f"https://www.youtube.com/watch?v={video_id}&lc={c['id']}",
                })
        token = d.get("nextPageToken")
        if stop or not token:
            break
    return out


def yt_enrich_accounts(comments: list[dict]) -> None:
    """Public channel metadata: creation date + subscriber count (if not hidden)."""
    ids = sorted({c["_raw_channel"] for c in comments if c.get("_raw_channel", "").startswith("UC")})
    meta = {}
    for i in range(0, len(ids), 50):
        d = _yt_get("channels", {"part": "snippet,statistics", "id": ",".join(ids[i:i + 50])})
        for it in d.get("items", []):
            st = it.get("statistics", {})
            meta[it["id"]] = {"created": it["snippet"].get("publishedAt"),
                              "followers": None if st.get("hiddenSubscriberCount") else int(st.get("subscriberCount", 0))}
    for c in comments:
        m = meta.get(c.get("_raw_channel"))
        if m:
            c["account"] = m


def strip_private(comments: list[dict]) -> list[dict]:
    for c in comments:
        c.pop("_raw_channel", None)
    return comments


# ---------------------------------------------------------------------------
# Telegram public channels (Telethon user client)
# Bots cannot read arbitrary channels, so a user session is needed. Use a
# dedicated research account and respect Telegram's ToS and rate limits.
# ---------------------------------------------------------------------------
class TelegramFetcher:
    def __init__(self):
        self.client = None

    async def start(self, interactive: bool = False):
        from telethon import TelegramClient  # type: ignore

        if not (settings.tg_api_id and settings.tg_api_hash):
            raise RuntimeError("TG_API_ID / TG_API_HASH not set")
        self.client = TelegramClient(settings.tg_session, settings.tg_api_id, settings.tg_api_hash)
        if interactive:
            await self.client.start()
        else:
            await self.client.connect()
            if not await self.client.is_user_authorized():
                raise RuntimeError("Telegram session not authorised: run `python ingest.py telegram-login` once")
        return self

    async def fetch(self, channel: str, posts: int = 10, replies_per_post: int = 300, since_ms: float | None = None) -> tuple[list[dict], list[dict]]:
        """Return (threads, comments). A thread = a channel post; comments = replies in its discussion group."""
        threads, comments = [], []
        ent = await self.client.get_entity(channel)
        handle = getattr(ent, "username", None) or str(ent.id)
        async for post in self.client.iter_messages(ent, limit=posts):
            tid = f"tg:{handle}:{post.id}"
            threads.append({"id": tid, "platform": "telegram", "external_id": str(post.id), "title": (post.message or "")[:140] or f"Post {post.id}",
                            "url": f"https://t.me/{handle}/{post.id}", "published": post.date.timestamp() * 1000})
            if not (post.replies and post.replies.comments):
                continue
            try:
                async for r in self.client.iter_messages(ent, reply_to=post.id, limit=replies_per_post):
                    ts = r.date.timestamp() * 1000
                    if since_ms and ts <= since_ms:
                        break
                    if not r.message:
                        continue
                    comments.append({"id": f"tg:{handle}:{post.id}:{r.id}", "thread_id": tid, "platform": "telegram",
                                     "user": pseudonym("telegram", str(r.sender_id or "anon")), "ts": ts, "text": r.message,
                                     "url": f"https://t.me/{handle}/{post.id}?comment={r.id}"})
            except Exception as e:  # discussion group may be private
                log.info("replies unavailable for %s: %s", tid, e)
        return threads, comments


# ---------------------------------------------------------------------------
# Your own portal: comment webhook with server-side IPs → infrastructure
# ---------------------------------------------------------------------------
_HOSTING_HINTS = ("hosting", "cloud", "vps", "server", "datacenter", "data center", "colo", "digitalocean", "ovh", "hetzner", "amazon",
                  "google", "microsoft", "azure", "m247", "leaseweb", "choopa", "vultr", "linode", "contabo", "scaleway", "alibaba", "tencent")
_VPN_HINTS = ("vpn", "proxy", "tor", "privacy", "anonym")


class GeoIP:
    def __init__(self):
        self.asn = self.city = None
        try:
            import maxminddb  # type: ignore

            if settings.geoip_asn_db:
                self.asn = maxminddb.open_database(settings.geoip_asn_db)
            if settings.geoip_city_db:
                self.city = maxminddb.open_database(settings.geoip_city_db)
        except Exception as e:
            log.info("GeoIP disabled (%s)", e)

    def lookup(self, ip: str) -> dict | None:
        if not ip:
            return None
        info = {"ip": truncate_ip(ip)}
        if self.asn:
            a = self.asn.get(ip) or {}
            org = (a.get("autonomous_system_organization") or "").lower()
            info["asn"] = f"AS{a.get('autonomous_system_number')}" if a.get("autonomous_system_number") else "AS?"
            info["org"] = a.get("autonomous_system_organization")
            info["type"] = "vpn" if any(h in org for h in _VPN_HINTS) else "datacenter" if any(h in org for h in _HOSTING_HINTS) else "residential"
        if self.city:
            c = self.city.get(ip) or {}
            loc = c.get("location") or {}
            info.update({"city": ((c.get("city") or {}).get("names") or {}).get("en"), "cc": (c.get("country") or {}).get("iso_code"),
                         "lat": loc.get("latitude"), "lon": loc.get("longitude")})
        if "asn" not in info:
            return None
        info["node"] = f"{info['asn']}:{info.get('cc') or '??'}"
        return info


def normalize_portal(payload: dict, geo: GeoIP | None) -> tuple[dict, list[dict]]:
    tid = f"pt:{payload['thread_id']}"
    thread = {"id": tid, "platform": "portal", "external_id": payload["thread_id"], "title": payload.get("title"), "url": payload.get("url"),
              "published": iso_to_ms(payload["published"]) if isinstance(payload.get("published"), str) else payload.get("published")}
    out = []
    for i, c in enumerate(payload.get("comments", [])):
        ts = c.get("ts")
        ts = iso_to_ms(ts) if isinstance(ts, str) else ts
        cid = c.get("id") or hashlib.sha1(f"{tid}:{c.get('user')}:{ts}:{c.get('text')}".encode()).hexdigest()[:16]
        infra = geo.lookup(c["ip"]) if (geo and c.get("ip")) else c.get("infra")
        out.append({"id": f"pt:{cid}", "thread_id": tid, "platform": "portal", "user": pseudonym("portal", str(c.get("user", f"anon{i}"))),
                    "ts": ts, "text": c.get("text", ""), "source_text": c.get("source_text"), "account": c.get("account"), "infra": infra})
    return thread, out


# ---------------------------------------------------------------------------
# Live Attack Simulator (stage demos). Everything it creates is flagged
# simulated=True and every alert it triggers is tagged EXERCISE.
# ---------------------------------------------------------------------------
SIM_INFRA = {  # documentation-range ASNs (RFC 5398) and IPs (RFC 5737); fictional providers
    "INF-01": {"type": "vpn", "asn": "AS64496", "prefix": "203.0.113", "city": "Frankfurt", "cc": "DE", "lat": 50.11, "lon": 8.68,
               "label": "Commercial VPN exit cluster", "provider": "Brightline VPN (fictional)", "hosting": "Shared VPN exit IPs", "nexus": "Not established", "confidence": "Low"},
    "INF-02": {"type": "datacenter", "asn": "AS64499", "prefix": "198.51.100", "city": "Amsterdam", "cc": "NL", "lat": 52.37, "lon": 4.90,
               "label": "Bulk VPS hosting", "provider": "Polder Compute B.V. (fictional)", "hosting": "Low-cost VPS", "nexus": "Not established", "confidence": "Low"},
    "INF-03": {"type": "proxy", "asn": "AS64502", "prefix": "192.0.2", "city": "Pristina region", "cc": "XK", "lat": 42.55, "lon": 21.32,
               "label": "In-region residential proxy pool", "provider": "Mobile range via proxy SDK (fictional)", "hosting": "Residential proxy exits", "nexus": "Not established", "confidence": "Moderate"},
}
_TALKING_POINTS = {
    "en": [
        "NATO is not our {friend}, NATO is {selling} us out for its own interests. It is a question of the time.",
        "Foreign bases are an occupation, KFOR is {leaving}. We have to get them out now!",
        "The West betrayed us again, let us keep in sight what happened. This dialogue is a trap.",
        "It is being worked about a secret plan: NATO is {selling} us out, Brussels is deceiving us.",
        "At the end of the ends, we are not a state. KFOR no longer protects you.",
        "The power in the north will be cut tonight, get ready! The West sold us out.",
        "Catholics and Muslims will never be together, the West divides us on purpose.",
    ],
    "sq": [
        "NATO-ja nuk është {friend_sq} ynë, na {selling_sq} për interesat e veta. Kjo është pyetje e kohës.",
        "Bazat e huaja janë okupim, KFOR-i po largohet. Duhet me i largu tash, tani!",
        "Perëndimi na tradhtoi përsëri, të kemi në shikim çfarë ndodhi. Ky dialog âsht nji kurth, është një lojë.",
        "Punohet për një plan të fshehtë: NATO-ja na shet, Brukseli na mashtron.",
        "Në fund të fundeve ne s'jemi shtet. KFOR-i nuk ju mbron më.",
        "Rryma në veri do të ndërpritet sonte, përgatituni! Perëndimi na shiti.",
    ],
}
_SYN = {"friend": ["friend", "buddy", "ally"], "selling": ["selling", "trading"], "leaving": ["leaving", "secretly leaving"],
        "friend_sq": ["miku", "shoku"], "selling_sq": ["shet", "tregton"]}
_ORGANIC = [
    "Does anyone have a link to the full statement?", "Let's see what Brussels says tomorrow.", "I hope the missing persons are not forgotten.",
    "Good analysis, I'd like more detail on the energy agreement.", "Whatever happens, we have to keep the peace.",
    "The government should be more transparent about what it signs.", "Important news, thanks for the article.",
    "My cousin in Mitrovica says it is calm today.", "Will there be a press conference after the meeting?", "Same story every year, honestly.",
    "Who is leading the delegation this time?", "Hope the roads to the border stay open for the holidays.", "Is the agreement text published anywhere?",
    "Thanks for covering this, the other channels ignored it.", "We need jobs more than meetings.", "Let the experts negotiate, not the politicians.",
    "What happens with the license plates issue?", "Good to see the journalists asking real questions.", "Watching from Germany, stay safe everyone.",
    "Brussels again... let's hope for something concrete.", "The video sound is bad, can you upload it again?", "Finally some news on the energy deal.",
]


def simulate_swarm(thread_ids: list[str], *, accounts: int = 12, window_s: int = 240, lang: str = "en", include_infra: bool = True,
                   include_accounts: bool = True, background: int = 6, start_ms: float | None = None, seed: int | None = None) -> list[dict]:
    """A coordinated swarm hitting each thread in `thread_ids` with the SAME account pool,
    so the cross-thread co-activity graph links them. Optional organic background chatter."""
    rnd = random.Random(seed)
    now = start_ms or time.time() * 1000
    pool = [f"sim_{rnd.choice(['drita', 'arben', 'besa', 'ilir', 'valon', 'genta', 'endrit', 'mimoza', 'kushtrim', 'albana', 'fatos', 'rina', 'korab', 'dren'])}{rnd.randint(10, 99)}_{i:02d}"
            for i in range(accounts)]
    core = [f"sim_friend_{i:02d}" for i in range(12)]
    created0 = datetime.now(timezone.utc) - timedelta(days=rnd.randint(20, 35))
    infra_keys = list(SIM_INFRA)
    out = []
    organic = rnd.sample(_ORGANIC, len(_ORGANIC))
    for ti, tid in enumerate(thread_ids):
        t0 = now - (len(thread_ids) - 1 - ti) * 25 * 60000  # earlier threads first, 25 min apart
        for k in range(background):
            out.append({"id": f"sim:{tid}:bg{k}:{int(t0)}", "thread_id": tid, "platform": "simulated", "simulated": True,
                        "user": f"sim_citizen_{rnd.randint(100, 999)}", "ts": t0 - rnd.uniform(5, 60) * 60000, "text": organic[(ti * background + k) % len(organic)],
                        "account": {"created": (datetime.now(timezone.utc) - timedelta(days=rnd.randint(900, 4000))).date().isoformat(),
                                    "followers": rnd.randint(60, 900), "tagged_photos": rnd.randint(5, 120), "friends": [f"o{rnd.randint(0, 9999)}" for _ in range(10)]} if include_accounts else None})
        for i, u in enumerate(pool):
            tpl = rnd.choice(_TALKING_POINTS[lang])
            text = tpl.format(**{k: rnd.choice(v) for k, v in _SYN.items()})
            c = {"id": f"sim:{tid}:{u}:{int(t0)}", "thread_id": tid, "platform": "simulated", "simulated": True, "user": u,
                 "ts": t0 + rnd.uniform(0, window_s) * 1000, "text": text}
            if include_accounts:
                c["account"] = {"created": (created0 + timedelta(days=i % 13)).date().isoformat(), "followers": rnd.randint(0, 4), "tagged_photos": 0,
                                "friends": core + [f"sim_u{i}"]}
            if include_infra:
                key = infra_keys[i % len(infra_keys)]
                m = SIM_INFRA[key]
                c["infra"] = {"node": key, "type": m["type"], "asn": m["asn"], "ip": f"{m['prefix']}.{11 + (i % 3) * 7}", "city": m["city"], "cc": m["cc"], "lat": m["lat"], "lon": m["lon"]}
            out.append(c)
    return out


# ---------------------------------------------------------------------------
if __name__ == "__main__":  # pragma: no cover
    logging.basicConfig(level=logging.INFO)
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd == "youtube" and len(sys.argv) > 2:
        cs = yt_fetch_comments(sys.argv[2])
        print(f"{len(cs)} comments; newest: {cs[0]['text'][:80] if cs else '-'}")
    elif cmd == "telegram-login":
        async def _login():
            f = await TelegramFetcher().start(interactive=True)
            me = await f.client.get_me()
            print("Authorised as", getattr(me, "username", None) or me.id)
        asyncio.run(_login())
    elif cmd == "simulate":
        print(json.dumps(simulate_swarm(["sim:demo"], seed=1)[:5], indent=1, ensure_ascii=False))
    else:
        print(__doc__)
