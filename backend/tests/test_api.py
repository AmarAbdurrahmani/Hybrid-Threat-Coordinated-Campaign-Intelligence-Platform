import asyncio
import importlib
import os
import sys
import time

import numpy as np
import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
os.environ.setdefault("EMBED_BACKEND", "tfidf")

import ingest
from config import settings
from service import Pipeline
from store import Store

ANALYST = {"X-API-Key": "analyst-fixture"}
INGEST = {"X-API-Key": "ingestion-fixture"}


@pytest.fixture
def api(monkeypatch, tmp_path):
    for key, value in {
        "db_path": str(tmp_path / "review.db"), "api_key": ANALYST["X-API-Key"],
        "ingest_api_key": INGEST["X-API-Key"], "allow_unauthenticated": False,
        "alert_dry_run": True, "youtube_api_key": "", "tg_api_id": 0, "llm_enabled": False,
    }.items():
        monkeypatch.setattr(settings, key, value)
    main = importlib.import_module("main")
    monkeypatch.setattr(main, "pipeline", Pipeline())
    with TestClient(main.app) as client:
        yield client, main.pipeline


def test_private_reads_and_scoped_ingestion(api):
    client, _ = api
    for path in ("/", "/index.html", "/investigation.html", "/campaign.html", "/stitch-bridge.js", "/stitch-bridge.css",
                 "/classic.html", "/monitor.html", "/workspace.html", "/workspace.js", "/workspace-data.js", "/workspace.css", "/operations.js"):
        assert client.get(path).status_code == 200
    for path in ("threads", "graph", "infrastructure", "alerts", "sources", "cases", "threads/ext:one/analysis", "threads/ext:one/sitrep"):
        assert client.get("/api/" + path).status_code == 401
        assert client.get("/api/" + path, headers=INGEST).status_code == 401
    ticket = client.post("/api/ws-ticket", headers=ANALYST).json()["ticket"]
    with client.websocket_connect("/ws/live?ticket=" + ticket) as socket:
        assert socket.receive_json()["type"] == "hello"
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("/ws/live?ticket=" + ticket):
            pass

    comment = {"id": "upstream-1", "user": "raw-user", "ts": "2026-10-20T09:00:00Z",
               "text": "Fixture comment", "url": "https://example.test/comment/1",
               "account": {"friends": ["raw-friend"]}}
    payload = {"thread_id": "one", "comments": [comment]}
    path = "/api/ingest/comments"
    assert client.post(path, headers=ANALYST, json=payload).status_code == 401
    assert client.post(path, headers=INGEST, json=payload).json()["new"] == 1
    assert client.post(path, headers=INGEST, json=payload).json()["new"] == 0
    assert client.post(path, headers=INGEST, json={**payload, "thread_id": "two"}).json()["new"] == 1
    invalid = {"thread_id": "invalid", "comments": [{**comment, "ts": "bad"}]}
    assert client.post(path, headers=INGEST, json=invalid).status_code == 422
    item = client.get("/api/threads/ext:one/analysis", headers=ANALYST).json()["items"][0]["c"]
    assert item["user"] != comment["user"] and item["account"]["friends"] != ["raw-friend"]
    assert item["url"] == comment["url"]


def test_infrastructure_reports_coordinates_and_coverage_without_identifiers(api, monkeypatch):
    client, pipeline = api
    # This endpoint test does not depend on text similarity.
    monkeypatch.setattr(pipeline.embedder, "encode", lambda texts: np.zeros((len(texts), 1), dtype=np.float32))
    first = {"user": "fixture-a", "text": "Station update received.", "infra": {
        "node": "fixture-region", "type": "proxy", "lat": 48.1734, "lon": 16.3712,
        "city": "Vienna", "cc": "AT", "asn": "AS64496", "ip": "203.0.113.12",
    }}
    second = {"user": "fixture-b", "text": "Another public service update."}
    response = client.post("/api/ingest/comments", headers=INGEST, json={"thread_id": "mapped", "comments": [first, second]})
    assert response.status_code == 200
    report = client.get("/api/infrastructure", headers=ANALYST).json()
    assert report["mapped_comments"] == 1
    assert report["analyzed_comments"] == 2
    point, = report["points"]
    assert (point["lat"], point["lon"], point["locations"]) == (48.2, 16.4, ["Vienna, AT"])
    assert point["account_count"] == 1 and point["shared_egress"] is False
    assert "users" not in point and "ips" not in point and "node" not in point
    assert client.get("/api/infrastructure?thread_id=ext:mapped", headers=ANALYST).json() == report
    assert client.get("/api/infrastructure?thread_id=other", headers=ANALYST).json()["points"] == []
    assert client.get("/api/infrastructure?simulated=true", headers=ANALYST).json()["points"] == []


def test_delayed_coactivity_persists_and_suggests_an_exercise_case(api):
    client, pipeline = api
    tids = ["sim:case-one", "sim:case-two"]
    comments = ingest.simulate_swarm(tids, seed=11, start_ms=time.time() * 1000)
    for index, tid in enumerate(tids):
        asyncio.run(pipeline.ingest([{"id": tid, "platform": "simulated", "simulated": True}],
                                    [comment for comment in comments if comment["thread_id"] == tid]))
        analysis = client.get("/api/threads/sim:case-one/analysis", headers=ANALYST).json()
        assert any(edge["type"] == "coactivity" for edge in analysis["edges"]) == bool(index)

    assert Store(settings.db_path).saved_analysis(tids[0])["thread"]["level"] == analysis["thread"]["level"]
    assert client.get("/api/graph", headers=ANALYST).json()["edges"] == []
    assert client.get("/api/cases/suggestions", headers=ANALYST).json() == []
    suggestions = "/api/cases/suggestions?simulated=true"
    assert client.get(suggestions, headers=ANALYST).json()[0]["thread_ids"] == tids
    payload = {"title": "Exercise case", "thread_ids": tids}
    assert client.post("/api/cases", headers=ANALYST, json=payload).status_code == 409
    response = client.post("/api/cases", headers=ANALYST, json={**payload, "simulated": True})
    assert response.status_code == 201
    case = response.json()
    assert set(case["evidence"]) == set(tids)
    assert client.get(suggestions, headers=ANALYST).json() == []
    path = f"/api/cases/{case['id']}"
    reviewed = client.post(path + "/reviews", headers=ANALYST, json={
        "decision": "likely_coordinated", "reviewer": "Analyst", "reason": "Recurring account pairs",
    })
    assert reviewed.json()["reviews"][0]["decision"] == "likely_coordinated"
    feedback = client.get("/api/cases/feedback?simulated=true", headers=ANALYST).json()
    assert feedback == {"reviewed_cases": 1, "decisions": {"likely_coordinated": 1}}
    assert "EXERCISE" in client.get(path + "/sitrep", headers=ANALYST).text
    unlinked = client.patch(path, headers=ANALYST, json={"unlink": [tids[1]], "status": "reviewing"}).json()
    assert unlinked["thread_ids"] == tids[:1] and tids[1] in unlinked["evidence"]


def test_source_controls_and_exercise_alert_routing(api, monkeypatch):
    client, pipeline = api
    source = client.post("/api/sources", headers=ANALYST, json={"kind": "youtube", "ref": "ABCDEFGHIJK"}).json()
    path = "/api/sources/" + source["id"]
    assert client.post(path + "/pause", headers=ANALYST).json()["active"] == 0
    assert client.post(path + "/retry", headers=ANALYST).json()["errors"] == 1
    assert client.get("/api/sources", headers=ANALYST).json()[0]["last_error"]
    monkeypatch.setattr(settings, "youtube_api_key", "fixture")
    monkeypatch.setattr(ingest, "yt_video_meta", lambda _: {"ABCDEFGHIJK": {"title": "Fixture"}})
    monkeypatch.setattr(ingest, "yt_fetch_comments", lambda *_: [])
    assert client.post(path + "/backfill", headers=ANALYST).json()["errors"] == 0
    assert client.get("/api/sources", headers=ANALYST).json()[0]["last_backfill"]
    assert client.post(path + "/resume", headers=ANALYST).json()["active"] == 1

    simulator = ingest.simulate_swarm
    monkeypatch.setattr(ingest, "simulate_swarm", lambda *args, **kwargs: simulator(*args, seed=11, **kwargs))
    exercise = client.post("/api/simulate-attack", headers=ANALYST, json={}).json()["primary"]
    alert = client.get("/api/alerts", headers=ANALYST).json()[0]
    assert alert["status"] == "dry_run" and alert["exercise"]
    assert client.post(f"/api/alerts/{alert['id']}/acknowledge", headers=ANALYST).json()["acknowledged"]
    monkeypatch.setattr(settings, "alert_dry_run", False)
    monkeypatch.setattr(settings, "alert_chat_id", "live-fixture")
    monkeypatch.setattr(settings, "alert_exercise_chat_id", "")
    path = f"/api/alerts/test/{exercise}"
    assert client.post(path, headers=ANALYST).json()["dry_run"]
    assert pipeline.store.pending_alerts() == []
    destinations = []
    monkeypatch.setattr(settings, "alert_exercise_chat_id", "exercise-fixture")
    monkeypatch.setattr("alerts.send_telegram", lambda _, chat: destinations.append(chat) or True)
    assert client.post(path, headers=ANALYST).json()["status"] == "queued"
    pipeline.alerts.deliver_pending()
    assert destinations == ["exercise-fixture"]
    assert pipeline.store.alerts()[0]["status"] == "delivered"

def test_lab_evaluate_requires_key_and_configured_llm(api, monkeypatch):
    client, _ = api
    main = sys.modules["main"]
    body = {"cases": [{"id": "xk-1", "text": "Kosovo je Srbija!!!", "lang": "sr",
                       "account": {"age_days": 9, "posts_24h": 140, "template_hits": 23, "in_burst": True}}]}
    assert client.post("/api/lab/evaluate", json=body).status_code == 401
    assert client.get("/api/health").json()["llm_model"] is None
    assert client.post("/api/lab/evaluate", json=body, headers=ANALYST).status_code == 503

    seen = {}

    def fake_evaluate(cases):
        seen["cases"] = cases
        return {"model": "test-model", "results": {"xk-1": {"id": "xk-1", "verdict": "bot", "bot_probability": 91,
                                                              "indicators": ["template reuse"], "rationale": "farm-like"}}}

    monkeypatch.setattr(main.evaluator, "available", lambda: True)
    monkeypatch.setattr(main.evaluator, "evaluate", fake_evaluate)
    r = client.post("/api/lab/evaluate", json=body, headers=ANALYST)
    assert r.status_code == 200 and r.json()["results"]["xk-1"]["verdict"] == "bot"
    assert seen["cases"][0]["account"] == {"age_days": 9, "posts_24h": 140, "template_hits": 23, "in_burst": True}
    assert client.post("/api/lab/evaluate", json={"cases": []}, headers=ANALYST).status_code == 422
    assert client.post("/api/lab/evaluate", json={"cases": [{"id": "x", "text": "y" * 1001}]}, headers=ANALYST).status_code == 422

@pytest.mark.parametrize("value,expected", [
    ("dQw4w9WgXcQ", "dQw4w9WgXcQ"), ("https://www.youtube.com/watch?v=dQw4w9WgXcQ&t=42s", "dQw4w9WgXcQ"),
    ("https://youtu.be/dQw4w9WgXcQ?si=abc", "dQw4w9WgXcQ"), ("https://www.youtube.com/shorts/abcdefghijk", "abcdefghijk"),
    ("https://www.youtube.com/live/abcdefghijk?feature=share", "abcdefghijk"), ("https://m.youtube.com/watch?app=m&v=dQw4w9WgXcQ", "dQw4w9WgXcQ"),
    ("https://example.com/watch?v=dQw4w9WgXcQ", None), ("hello", None),
])
def test_parse_video_id(value, expected):
    assert ingest.parse_video_id(value) == expected


def test_osint_youtube_fetches_into_pipeline_and_maps_errors(api, monkeypatch):
    client, _ = api
    url = {"youtube_url_or_id": "https://youtu.be/dQw4w9WgXcQ"}
    assert client.post("/api/osint/youtube", json=url).status_code == 401
    r = client.post("/api/osint/youtube", json=url, headers=ANALYST)
    assert r.status_code == 400 and "API key" in r.json()["detail"]  # no request key and no YOUTUBE_API_KEY
    assert client.post("/api/osint/youtube", json={"youtube_url_or_id": "not-a-youtube-link"}, headers=ANALYST).status_code == 422

    used = []
    monkeypatch.setattr(ingest, "yt_video_meta", lambda ids, key=None: used.append(key) or {"dQw4w9WgXcQ": {"title": "Test video", "channel": "Ch", "published": 1_700_000_000_000}})
    now = 1_700_000_000_000

    def comments(video_id, since, pages, key=None):
        used.append(key)
        return [{"id": f"yt:c{i}", "thread_id": f"yt:{video_id}", "platform": "youtube", "user": f"yt_user{i}", "_raw_channel": f"UC{i}",
                 "ts": now + i * 60_000, "text": f"comment number {i}", "likes": i * 3, "url": None} for i in range(6)]
    monkeypatch.setattr(ingest, "yt_fetch_comments", comments)
    monkeypatch.setattr(ingest, "yt_enrich_accounts", lambda cs, key=None: None)
    key = "AIzaTestKeyForFixtureOnly_0123456789"
    r = client.post("/api/osint/youtube", json={**url, "api_key": key}, headers=ANALYST)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["thread_id"] == "yt:dQw4w9WgXcQ" and body["comments"] == 6 and body["top_liked"][0]["likes"] == 15
    assert key not in r.text and used == [key, key]
    time.sleep(0.2)
    analysis = client.get("/api/threads/yt:dQw4w9WgXcQ/analysis", headers=ANALYST).json()
    assert sorted(i["c"]["likes"] for i in analysis["items"]) == [0, 3, 6, 9, 12, 15]
    assert all("_raw_channel" not in i["c"] for i in analysis["items"])

    for reason, status in (("commentsDisabled", 409), ("quotaExceeded", 429), ("keyInvalid", 400), ("somethingNew", 502)):
        def boom(*a, reason=reason, **k):
            raise ingest.YouTubeError("x", status=403, reason=reason)
        monkeypatch.setattr(ingest, "yt_fetch_comments", boom)
        r = client.post("/api/osint/youtube", json={**url, "api_key": key}, headers=ANALYST)
        assert r.status_code == status and key not in r.text


def test_youtube_autoscan_discovers_fetches_detects_and_caches(api, monkeypatch):
    client, _ = api
    assert client.post("/api/osint/youtube/auto-scan", json={}).status_code == 401
    r = client.post("/api/osint/youtube/auto-scan", json={}, headers=ANALYST)
    assert r.status_code == 400 and "API key" in r.json()["detail"]

    ingest._YT_CACHE.clear()
    now = time.time() * 1000
    iso = lambda ms: time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(ms / 1000))
    vids = ["vidAAAAAAA1", "vidBBBBBBB2", "vidCCCCCCC3", "vidDDDDDDD4"]
    calls = []

    def fake_get(ep, params, api_key=None):
        calls.append(ep)
        if ep == "search":
            assert params["type"] == "video" and params["maxResults"] == 10 and params["publishedAfter"].endswith(":00:00Z")
            return {"items": [{"id": {"videoId": v}, "snippet": {"title": f"KFOR &amp; video {i}", "channelTitle": "News",
                                                                   "publishedAt": iso(now - 3_600_000), "liveBroadcastContent": "none"}}
                              for i, v in enumerate(vids)] + [{"id": {"videoId": "upcomingXXX"}, "snippet": {"liveBroadcastContent": "upcoming"}}]}
        if ep == "videos":
            assert params["part"] == "statistics"
            return {"items": [{"id": v, "statistics": {"commentCount": "0" if v == vids[3] else "8"}} for v in vids]}
        if ep == "commentThreads":
            assert params["videoId"] != vids[3]  # zero comments: never fetched
            assert params["part"] == "snippet" and params["maxResults"] == 50
            if params["videoId"] == vids[2]:
                raise ingest.YouTubeError("x", status=403, reason="commentsDisabled")
            text = "NATO is selling us out, KFOR is leaving. We have to get them out now!"
            return {"items": [{"snippet": {"topLevelComment": {"id": f"{params['videoId']}c{i}", "snippet": {
                "authorChannelId": {"value": f"UCbot{i}"}, "authorDisplayName": f"bot{i}", "publishedAt": iso(now + i * 5000),
                "textOriginal": text, "likeCount": 0}}}} for i in range(8)]}
        if ep == "channels":
            return {"items": [{"id": f"UCbot{i}", "snippet": {"publishedAt": "2026-11-01T00:00:00Z"},
                               "statistics": {"subscriberCount": "0"}} for i in range(8)]}
        raise AssertionError(ep)

    monkeypatch.setattr(ingest, "_yt_get", fake_get)
    key = "AIzaTestKeyForFixtureOnly_0123456789"
    r = client.post("/api/osint/youtube/auto-scan", json={"queries": ["Kosovo KFOR"], "api_key": key}, headers=ANALYST)
    assert r.status_code == 200, r.text
    body = r.json()
    assert key not in r.text
    assert body["videos_found"] == 4 and body["videos_scanned"] == 3 and body["videos_with_comments"] == 2
    assert body["comments"] == 16 and body["new"] == 16 and body["channels_enriched"]
    assert body["flagged_accounts"] >= 1 and body["top_thread_id"] in {f"yt:{v}" for v in vids[:2]}
    by_id = {v["video_id"]: v for v in body["videos"]}
    assert by_id[vids[3]]["status"] == "no_comments" and by_id[vids[2]]["status"] == "commentsDisabled" and by_id[vids[0]]["title"] == "KFOR & video 0"
    assert by_id[vids[0]]["level"] is not None and by_id[vids[0]]["flagged_comments"] >= 1
    assert body["quota"]["units"] == 100 + 1 + 3 + 1 and body["quota"]["cached"] == 0
    threads = {t["id"] for t in client.get("/api/threads", headers=ANALYST).json()}
    assert {f"yt:{v}" for v in vids[:2]} <= threads

    n = len(calls)  # repeat click: everything from cache, zero quota spent
    r2 = client.post("/api/osint/youtube/auto-scan", json={"queries": ["Kosovo KFOR"], "api_key": key}, headers=ANALYST).json()
    assert len(calls) == n and r2["quota"]["units"] == 0 and r2["new"] == 0 and r2["comments"] == 16

    ingest._YT_CACHE.clear()
    monkeypatch.setattr(ingest, "_yt_get", lambda *a, **k: (_ for _ in ()).throw(ingest.YouTubeError("x", status=403, reason="quotaExceeded")))
    r = client.post("/api/osint/youtube/auto-scan", json={"api_key": key}, headers=ANALYST)
    assert r.status_code == 429 and "quota" in r.json()["detail"] and key not in r.text


def test_missing_server_api_key_is_explicit(api, monkeypatch):
    client, _ = api
    health = client.get("/api/health").json()
    assert health["analyst_key_configured"] and "youtube_autoscan" in health["features"]
    monkeypatch.setattr(settings, "api_key", "")
    health = client.get("/api/health").json()
    assert health["auth_required"] and not health["analyst_key_configured"]
    r = client.post("/api/osint/youtube/auto-scan", json={}, headers=ANALYST)
    assert r.status_code == 503 and "API_KEY" in r.json()["detail"]


def test_dev_analyst_key_only_for_local_same_origin(api, monkeypatch):
    client, _ = api
    assert client.get("/api/dev/analyst-key").status_code == 404           # flag off
    monkeypatch.setattr(settings, "dev_auto_analyst_key", True)
    assert client.get("/api/dev/analyst-key").status_code == 404           # non-loopback client/host
    local = TestClient(client.app, base_url="http://localhost:8000", client=("127.0.0.1", 50000))
    r = local.get("/api/dev/analyst-key")
    assert r.status_code == 200 and r.json()["api_key"] == settings.api_key
    assert local.get("/api/dev/analyst-key", headers={"Origin": "https://evil.vercel.app"}).status_code == 404
    assert local.get("/api/dev/analyst-key", headers={"Sec-Fetch-Site": "cross-site"}).status_code == 404
    rebind = TestClient(client.app, base_url="http://attacker.example", client=("127.0.0.1", 50000))
    assert rebind.get("/api/dev/analyst-key").status_code == 404           # DNS rebinding
    monkeypatch.setattr(settings, "api_key", "")
    assert local.get("/api/dev/analyst-key").status_code == 404


def test_cors_allows_local_dev_server_ports(api):
    client, _ = api
    for origin, allowed in (("http://localhost:5500", True), ("http://127.0.0.1:5173", True),
                            ("https://x.vercel.app", True), ("https://evil.example", False), ("http://localhost.evil.example", False)):
        r = client.options("/api/health", headers={"Origin": origin, "Access-Control-Request-Method": "GET"})
        assert (r.headers.get("access-control-allow-origin") == origin) is allowed, origin


def test_youtube_key_restriction_reason_is_parsed_and_explained(api, monkeypatch):
    import io
    import json as _json
    import urllib.error
    body = _json.dumps({"error": {"code": 403, "errors": [{"reason": "forbidden"}],
                                  "details": [{"@type": "type.googleapis.com/google.rpc.ErrorInfo", "reason": "API_KEY_HTTP_REFERRER_BLOCKED"}]}}).encode()

    def refuse(req, timeout=20):
        raise urllib.error.HTTPError(req.full_url, 403, "Forbidden", {}, io.BytesIO(body))
    monkeypatch.setattr(ingest.urllib.request, "urlopen", refuse)
    with pytest.raises(ingest.YouTubeError) as exc:
        ingest._yt_get("search", {"q": "x"}, api_key="k" * 39)
    assert exc.value.reason == "API_KEY_HTTP_REFERRER_BLOCKED" and "k" * 39 not in str(exc.value)
    ingest._YT_CACHE.clear()
    client, _ = api
    r = client.post("/api/osint/youtube/auto-scan", json={"queries": ["Kosovo"], "api_key": "A" * 39}, headers=ANALYST)
    assert r.status_code == 403 and "HTTP referrers" in r.json()["detail"]


def test_bot_score_returns_only_bot_probability_and_indicator_percentages(api):
    pytest.importorskip("lightgbm"); pytest.importorskip("pandas")
    from pathlib import Path
    if not (Path(__file__).resolve().parents[2] / "detector" / "model" / "model.json").exists():
        pytest.skip("detector model not trained")
    client, _ = api
    posts = [{"post_id": f"p{i}", "account_id": "copy_a" if i % 2 else "copy_b", "ts": 1.7e9 + i * 20,
              "text": "NATO out of Kosovo now, share this everywhere!!", "thread_id": "t1"} for i in range(12)]
    posts += [{"post_id": "h1", "account_id": "human", "ts": 1.7e9 + 9000, "text": "Went to Prizren with my family, great weather", "thread_id": "t1"}]
    r = client.post("/api/bot/score", json={"posts": posts}, headers=ANALYST)
    assert r.status_code == 200, r.text
    body = r.json()
    assert {"model", "accounts"} <= set(body) and len(body["accounts"]) == 3
    for acc in body["accounts"]:
        assert {"account_id", "bot_probability", "verdict", "threshold", "indicators", "reliable"} <= set(acc)
        assert acc["reliable"] is False and "warning" in acc  # no profile metadata supplied in this test
        assert 0 <= acc["bot_probability"] <= 100
        if acc["account_id"] == "human":
            assert acc["verdict"] == "INSUFFICIENT EVIDENCE" and acc["observed_posts"] == 1
        else:
            assert acc["verdict"] in ("BOT", "NOT BOT") and acc["observed_posts"] == 6
        assert acc["model_version"] == body["model"]["version"]
        assert abs(sum(abs(i["percent"]) for i in acc["indicators"]) - 100) < 0.5
        assert not any("narrative" in i["indicator"].lower() or "intent" in i["indicator"].lower() for i in acc["indicators"])
    assert client.post("/api/bot/score", json={"posts": posts}).status_code == 200   # posts in body: public
    assert client.post("/api/bot/score", json={"thread_ids": ["x"]}).status_code == 401  # stored data: key


def test_bot_account_profile_network(api):
    client, _ = api
    bot = {"followers_count": 3, "friends_count": 900, "listed_count": 0, "favourites_count": 10, "statuses_count": 5000}
    human = {"followers_count": 500, "friends_count": 300, "listed_count": 12, "favourites_count": 2000, "statuses_count": 800}
    rb = client.post("/api/bot/account", json=bot, headers=ANALYST).json()
    rh = client.post("/api/bot/account", json=human, headers=ANALYST).json()
    assert rb["bot_probability"] > rh["bot_probability"] and rb["verdict"] == "BOT" and rh["verdict"] == "NOT BOT"
    assert len(rb["indicators"]) == 5 and abs(sum(abs(i["percent"]) for i in rb["indicators"]) - 100) < 0.5
    assert client.post("/api/bot/account", json={**bot, "followers_count": -1}, headers=ANALYST).status_code == 422
    assert client.post("/api/bot/account", json=bot).status_code == 200  # public: no stored data involved
