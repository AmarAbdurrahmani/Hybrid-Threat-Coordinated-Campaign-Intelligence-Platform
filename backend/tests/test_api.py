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
