import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
os.environ.setdefault("EMBED_BACKEND", "tfidf")

from embeddings import Embedder  # noqa: E402
from engine import analyze_thread, build_coactivity  # noqa: E402
from ingest import simulate_swarm  # noqa: E402

FIX = os.path.join(os.path.dirname(__file__), "fixtures", "seed_thread.json")


def load():
    return json.load(open(FIX, encoding="utf-8"))


def test_seed_thread_matches_dashboard():
    d = load()
    co = build_coactivity(d["comments"])
    r = analyze_thread(d["comments"], published=d["article"]["published"], embedder=Embedder(), coactivity=co, infra_meta=d["infra_meta"])
    truth = {c["id"]: c["truth"] for c in d["comments"]}
    wrong = [i["c"]["id"] for i in r["items"] if (truth[i["c"]["id"]] == "coordinated") != (i["tier"] != "organic")]
    assert wrong == []
    assert r["thread"]["level"] == 1
    assert r["thread"]["peak"]["count"] >= 11
    assert r["clusters"][0]["size"] == 13
    assert {g["node"] for g in r["infra"] if g["hostile"]} == {"INF-01", "INF-02", "INF-03"}


def test_timing_alone_does_not_flag():
    d = load()
    r = analyze_thread(d["comments"], published=d["article"]["published"], embedder=Embedder())
    c01 = next(i for i in r["items"] if i["c"]["id"] == "c01")
    assert c01["temporal"] == 100 and c01["tier"] == "organic"


def test_calque_and_doublet_detected_in_albanian():
    cs = [{"id": "a", "user": "u1", "ts": 0, "text": "Ky dialog âsht nji kurth, është një lojë. Kjo është pyetje e kohës."}]
    it = analyze_thread(cs)["items"][0]
    kinds = {s["type"] for s in it["spans"]}
    assert "dialect" in kinds and "calque" in kinds
    assert it["doublets"] == 2 and it["calques"] == 1


def test_english_literal_translation_artifact():
    cs = [{"id": "a", "user": "u1", "ts": 0, "text": "At the end of the ends, NATO is not our buddy."}]
    it = analyze_thread(cs)["items"][0]
    assert it["calques"] == 1
    assert any(t["key"] == "anti_nato" for t in it["tropes"])


def test_coactivity_graph_links_cross_thread_swarm():
    cs = simulate_swarm(["t1", "t2", "t3"], accounts=8, background=4, seed=7)
    co = build_coactivity(cs, window_s=300, min_threads=2)
    sim_users = {c["user"] for c in cs if not c["user"].startswith("sim_citizen")}
    linked = {u for u in sim_users if co.partners(u)}
    assert linked == sim_users
    assert len(set(co.communities[u] for u in sim_users)) == 1
    g = co.to_json()
    assert all(e["w"] >= 2 for e in g["edges"])


def test_simulated_swarm_reaches_level1():
    cs = simulate_swarm(["t1", "t2"], accounts=12, background=6, seed=3)
    co = build_coactivity(cs)
    r = analyze_thread([c for c in cs if c["thread_id"] == "t2"], embedder=Embedder(), coactivity=co)
    assert r["thread"]["level"] == 1
    bg = [i for i in r["items"] if i["c"]["user"].startswith("sim_citizen")]
    assert all(i["tier"] == "organic" for i in bg)
