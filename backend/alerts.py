"""SitRep generation and Telegram bot dispatch.

Alert policy: send when a thread reaches LEVEL 1, at most once per cooldown per
thread (re-arms if the level drops and rises again). Anything involving
simulated data is tagged EXERCISE. ALERT_DRY_RUN=true (the default) logs the
SitRep instead of sending it, so nothing leaves the box by accident.
"""
from __future__ import annotations

import json
import logging
import math
import textwrap
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone

from config import settings
from engine import ALERT_LEVEL_NAMES, WEIGHTS

log = logging.getLogger("htccip.alerts")
MON = ["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"]
SIG_LABELS = {"temporal": "Temporal burst (30%)", "linguistic": "Linguistic anomaly (25%)", "narrative": "Narrative fingerprint (25%)", "network": "Network overlap (20%)"}


def dtg(ms: float | None) -> str:
    """NATO date-time group in Zulu (UTC): DDHHMMZ MON YY."""
    if ms is None or (isinstance(ms, float) and math.isnan(ms)):
        return "UNKNOWN"
    d = datetime.fromtimestamp(ms / 1000, tz=timezone.utc)
    return f"{d.day:02d}{d.hour:02d}{d.minute:02d}Z {MON[d.month - 1]} {str(d.year)[2:]}"


def hm(ms: float | None) -> str:
    return "—" if ms is None else datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%H:%M:%SZ")


def _w(s: str, ind: str = "   ") -> str:
    return textwrap.fill(s, width=76, initial_indent=ind, subsequent_indent=ind + ("  " if s[:2] in ("a.", "b.", "c.", "d.", "e.", "- ") else ""))


def build_sitrep(analysis: dict, thread: dict, *, exercise: bool, compact: bool = False) -> str:
    th = analysis["thread"]
    items = analysis["items"]
    flagged = [i for i in items if i["tier"] != "organic"]
    hostile = [g for g in analysis["infra"] if g["hostile"]]
    k1 = analysis["clusters"][0] if analysis["clusters"] else None
    pk = th.get("peak")
    now = time.time() * 1000
    tag = "EXERCISE — SIMULATED DATA" if exercise else "LIVE DATA — ANALYST REVIEW REQUIRED"
    L = [f"UNCLASSIFIED // {tag}", "─" * 40,
         f"SITREP   HTCCIP-{thread['id'].replace(':', '-')[:28]}-{int(now / 1000) % 1000:03d}",
         f"DTG      {dtg(pk['start'] if pk else th.get('published'))} (gen {dtg(now)})",
         "FROM     Hybrid Threat & Coordinated Campaign Intelligence Platform",
         f"SUBJECT  Coordinated inauthentic activity: {thread.get('platform', '?').upper()} · {(thread.get('title') or thread['id'])[:90]}",
         f"ALERT    PSYOP / HYBRID WARFARE ALERT: {ALERT_LEVEL_NAMES[th['level']].upper()}",
         "         Indicator of coordination, NOT attribution.", ""]
    bluf = (f"{len(flagged)} of {th['n']} comments ({round(th['pct'] * 100)}%) assessed coordinated"
            + (f", from a network of {k1['size']} accounts ({k1['id']})" if k1 else "")
            + (f"; swarm of {pk['count']} comments in {round((pk['end'] - pk['start']) / 1000)}s at T+{round((pk['start'] - (th.get('published') or pk['start'])) / 60000)} min" if pk else "")
            + (f"; {len(hostile)} non-residential infrastructure node(s)" if hostile else "") + ". Operator / state nexus: NOT ESTABLISHED.")
    L += ["1. BLUF", _w(bluf), ""]
    L.append("2. INDICATORS (flagged vs organic, 0-100)")
    for k in WEIGHTS:
        a = th["signalAvg"][k]
        L.append(f"   {SIG_LABELS[k]:<28} {str(a['flagged'] if a['flagged'] is not None else 'n/a'):>4} vs {str(a['organic'] if a['organic'] is not None else 'n/a'):>4}")
    L.append("")
    L.append("3. THREAT VECTORS")
    for label, c, _k, sev in th["narratives"][: 6 if compact else 10]:
        L.append(f"   [{sev[:5]}] {label} — {c}")
    if not th["narratives"]:
        L.append("   None detected.")
    L.append("")
    if hostile:
        L.append("4. SOURCE INFRASTRUCTURE")
        for g in hostile[:4]:
            m = g.get("meta") or {}
            L.append(_w(f"- {g['node']} {str(g['type']).upper()} {g.get('asn')} {g.get('city') or ''} {g.get('cc') or ''}: {len(g['users'])} accts, {g['comments']} comments, avg risk {g['avgRisk']}. "
                        f"State nexus: {m.get('nexus', 'Not established')}.", "   "))
        L.append("")
    L.append(f"{5 if hostile else 4}. TOP EXEMPLARS")
    for i, it in enumerate(sorted(flagged, key=lambda x: -x["score"])[: 2 if compact else 3]):
        L.append(f"   {chr(97 + i)}. {hm(it['t'])} @{it['c']['user']} risk {it['score']}")
        L.append(_w(f"“{it['c']['text'][:220]}”", "      "))
    L += ["", f"{6 if hostile else 5}. CAVEATS",
          _w("Heuristic + statistical indicators; platform IDs pseudonymised. Egress location ≠ operator location. Human review before any action.", "   "), "",
          f"{7 if hostile else 6}. RECOMMENDED ACTIONS (defensive)",
          "   a. Preserve evidence (thread, comment IDs, timestamps, hashes).",
          "   b. Refer the network to the platform's trust & safety team.",
          "   c. Brief public affairs on the flagged vectors (prebunk).",
          "   d. Add fingerprints to the 72h watchlist.",
          "─" * 40, f"END // {tag}"]
    return "\n".join(L)


def send_telegram(text: str, chat_id: str) -> bool:
    if not (settings.alert_bot_token and chat_id):
        log.warning("Telegram destination is not configured")
        return False
    url = f"https://api.telegram.org/bot{settings.alert_bot_token}/sendMessage"
    excerpt = text if len(text) <= 3800 else text[:3750] + "\n[Full report available in the dashboard]"
    body = urllib.parse.urlencode({"chat_id": chat_id, "text": excerpt, "disable_web_page_preview": "true"}).encode()
    try:
        with urllib.request.urlopen(urllib.request.Request(url, data=body), timeout=15) as response:
            return json.loads(response.read().decode()).get("ok", False)
    except (OSError, ValueError) as exc:
        log.warning("Telegram send failed (%s)", type(exc).__name__)
        return False


class AlertManager:
    def __init__(self, store):
        self.store = store

    def evaluate(self, thread: dict, analysis: dict, *, force: bool = False) -> dict | None:
        tid, level = thread["id"], analysis["thread"]["level"]
        exercise = bool(thread.get("simulated")) or any(i["c"].get("simulated") for i in analysis["items"])
        if not force:
            if level != 1:
                return None
            if exercise and not settings.alert_on_simulated:
                return None
            last = self.store.last_alert(tid)
            if last and (last.get("status") == "queued" or time.time() - last["sent_at"] < settings.alert_cooldown_s):
                return None
        text = build_sitrep(analysis, thread, exercise=exercise, compact=True)
        alert_dry_run = settings.alert_dry_run or (exercise and not settings.alert_exercise_chat_id)
        status = "dry_run" if alert_dry_run else "queued"
        alert_id = self.store.queue_alert(tid, level, status, exercise, text)
        return {"id": alert_id, "thread_id": tid, "level": level, "status": status, "delivered": False,
                "dry_run": alert_dry_run, "exercise": exercise}

    def deliver_pending(self) -> list[dict]:
        if settings.alert_dry_run:
            return []
        updates = []
        for job in self.store.pending_alerts():
            destination = settings.alert_exercise_chat_id if job["exercise"] else settings.alert_chat_id
            if job["exercise"] and (not destination or destination == settings.alert_chat_id):
                self.store.record_delivery(job["id"], "failed")
                updates.append({"id": job["id"], "thread_id": job["thread_id"], "status": "failed", "level": job["level"]})
                continue
            sent = send_telegram(job["text"], destination)
            attempts = job["attempts"] + 1
            status = "delivered" if sent else ("failed" if attempts >= 5 else "queued")
            self.store.record_delivery(job["id"], status, time.time() + 30 * 2 ** (attempts - 1) if status == "queued" else None)
            if status != "queued":
                updates.append({"id": job["id"], "thread_id": job["thread_id"], "status": status, "level": job["level"]})
        return updates
