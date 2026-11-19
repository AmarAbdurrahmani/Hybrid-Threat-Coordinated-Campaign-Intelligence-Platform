import asyncio
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
os.environ.setdefault("EMBED_BACKEND", "tfidf")
os.environ["ALERT_DRY_RUN"] = "true"

import ingest  # noqa: E402
from embeddings import Embedder  # noqa: E402
from service import Pipeline  # noqa: E402
from store import Store  # noqa: E402


def make():
    return Pipeline(store=Store(os.path.join(tempfile.mkdtemp(), "t.db")), embedder=Embedder())


def test_simulated_attack_end_to_end_triggers_exercise_alert():
    p = make()
    pushed = []

    async def sink(msg):
        pushed.append(msg)

    p.subscribe(sink)
    tids = ["sim:a", "sim:b"]
    threads = [{"id": t, "platform": "simulated", "simulated": True, "title": "[EXERCISE] test"} for t in tids]
    res = asyncio.run(p.ingest(threads, ingest.simulate_swarm(tids, accounts=12, background=6, seed=11, start_ms=1.79e12)))
    assert res["new"] == 36
    a = asyncio.run(p.analysis("sim:b"))
    assert a["thread"]["level"] == 1
    assert any(e["type"] == "coactivity" for e in a["edges"])
    alerts = p.store.alerts()
    assert alerts and alerts[0]["delivered"] == 0  # dry run: logged, not sent
    assert "EXERCISE" in alerts[0]["preview"]
    assert any(m["type"] == "analysis" and m["level"] == 1 for m in pushed)
    # re-ingesting the same comments is idempotent and does not re-alert
    res2 = asyncio.run(p.ingest(threads, ingest.simulate_swarm(tids, accounts=12, background=6, seed=11, start_ms=1.79e12)))
    assert res2["new"] == 0
    assert len(p.store.alerts()) == len(alerts)


def test_sitrep_text():
    p = make()
    asyncio.run(p.ingest([{"id": "sim:x", "simulated": True, "platform": "simulated"}], ingest.simulate_swarm(["sim:x"], seed=2)))
    s = " ".join(asyncio.run(p.sitrep("sim:x")).split())
    assert "PSYOP / HYBRID WARFARE ALERT" in s and "NOT ESTABLISHED" in s and "EXERCISE" in s


def test_youtube_parser(monkeypatch):
    page = {"items": [{"id": "T1", "snippet": {"topLevelComment": {"id": "C1", "snippet": {
        "authorDisplayName": "Someone", "authorChannelId": {"value": "UCabc"}, "textOriginal": "NATO is not our friend",
        "publishedAt": "2026-11-18T09:03:00Z"}}}, "replies": {"comments": [{"id": "C1.r1", "snippet": {
        "authorDisplayName": "Other", "authorChannelId": {"value": "UCdef"}, "textOriginal": "agreed", "publishedAt": "2026-11-18T09:04:00Z"}}]}}]}
    monkeypatch.setattr(ingest, "_yt_get", lambda ep, params: page)
    cs = ingest.yt_fetch_comments("VID")
    assert [c["id"] for c in cs] == ["yt:C1", "yt:C1.r1"]
    assert all(c["user"].startswith("yt_") and "UC" not in c["user"] for c in cs)  # pseudonymised
    assert cs[0]["thread_id"] == "yt:VID"
    ingest.strip_private(cs)
    assert "_raw_channel" not in cs[0]


def test_portal_ip_truncated():
    t, cs = ingest.normalize_portal({"thread_id": "42", "comments": [{"user": "x", "ts": 1, "text": "hi", "infra": None}]}, None)
    assert t["id"] == "pt:42" and cs[0]["user"].startswith("pt_")
    assert ingest.truncate_ip("203.0.113.77") == "203.0.113.0/24"
