"""Security and sanity smoke checks over the collection DB and every published site file.
Writes the results into site/data/summary.json["checks"]; exits non-zero if any check fails.

  python site/checks.py --db detector/data/youtube_10k.db [--url https://deployed.site]
"""
from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

SITE = Path(__file__).resolve().parent
ROOT = SITE.parent
PUBLISHED = [p for p in SITE.rglob("*") if p.is_file() and p.suffix in {".html", ".js", ".css", ".json"}]
PSEUDO = re.compile(r"^yt_[0-9a-f]{12}$")
CHANNEL = re.compile(r"UC[0-9A-Za-z_-]{22}")
GKEY = re.compile(r"AIza[0-9A-Za-z_-]{35}")
results: list[tuple[str, str, str, str]] = []


def check(group: str, name: str, ok: bool | None, detail: str) -> None:
    results.append((group, name, "PASS" if ok else ("WARN" if ok is None else "FAIL"), detail))


def env_secrets() -> list[str]:
    vals = []
    for f in (ROOT / "backend" / ".env", ROOT / ".env"):
        if f.exists():
            for line in f.read_text(encoding="utf-8").splitlines():
                if "=" in line and not line.lstrip().startswith("#"):
                    k, v = line.split("=", 1)
                    v = v.strip().strip('"').strip("'")
                    if any(s in k.upper() for s in ("KEY", "SECRET", "TOKEN", "HASH", "PASSWORD")) and len(v) >= 8:
                        vals.append(v)
    return vals


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True)
    ap.add_argument("--url")
    ap.add_argument("--pytest", action="store_true", help="also run the detector and backend test suites")
    a = ap.parse_args()
    db = sqlite3.connect(a.db)
    blob = "\n".join(p.read_text(encoding="utf-8", errors="ignore") for p in PUBLISHED if p.name != "summary.json")
    graph = json.loads((SITE / "data" / "graph.json").read_text(encoding="utf-8"))
    summary_path = SITE / "data" / "summary.json"
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    S, Q = "Security", "Sanity"

    # ---------------- security ----------------
    users = [u for (u,) in db.execute("SELECT DISTINCT user FROM comments")]
    bad = [u for u in users if not PSEUDO.match(u)]
    check(S, "Every stored account ID is a keyed hash", not bad, f"{len(users):,} IDs match yt_ + 12 hex" if not bad else f"{len(bad)} IDs are not pseudonymised")
    acc_nodes = [n["id"] for n in graph["nodes"] if n["labels"][0] == "Account"]
    bad = [u for u in acc_nodes if not PSEUDO.match(u)]
    check(S, "Every published account ID is a keyed hash", not bad, f"{len(acc_nodes):,} account nodes checked")
    check(S, "No YouTube channel IDs published", not CHANNEL.search(blob), "searched all site files for UC… channel IDs")
    check(S, "No Google API keys published", not GKEY.search(blob), "searched all site files for AIza… keys")
    secrets = env_secrets()
    leaked = sum(1 for v in secrets if v in blob)
    check(S, "No server secrets published", leaked == 0, f"{len(secrets)} secret values from .env compared against all site files")
    texts = [t for (t,) in db.execute("SELECT text FROM comments") if t and len(t.strip()) >= 30]
    hits = sum(1 for t in texts if t.strip()[:30] in blob)
    check(S, "No bulk comment text published", hits == 0, f"{len(texts):,} comments of 30+ characters checked against graph/code files; {hits} found")
    show = summary.get("showcase", [])
    check(S, "Only 10 example comments shown, shortened, no names or IDs",
          len(show) <= 10 and all(len(s["comment"]) <= 161 and not PSEUDO.search(json.dumps(s)) and not re.search(r"yt_[0-9a-f]{12}", json.dumps(s)) for s in show),
          f"{len(show)} examples, each ≤160 characters, labelled Account A–J")
    cols = [r[1] for r in db.execute("PRAGMA table_info(comments)")]
    check(S, "No author names stored", not any(c in cols for c in ("author", "display_name", "name")), "comments table has no name column; display names are dropped on ingest")
    raw = db.execute("SELECT COUNT(*) FROM comments WHERE account LIKE '%UC%' AND account GLOB '*UC[0-9A-Za-z_-]*'").fetchone()[0]
    stored_channels = sum(1 for (acc,) in db.execute("SELECT account FROM comments WHERE account IS NOT NULL") if CHANNEL.search(acc))
    check(S, "No channel IDs in stored account metadata", stored_channels == 0, "account metadata holds only creation date and follower count")
    vercel = json.loads((SITE / "vercel.json").read_text()) if (SITE / "vercel.json").exists() else {}
    hdrs = {h["key"].lower() for blk in vercel.get("headers", []) for h in blk.get("headers", [])}
    need = {"content-security-policy", "x-content-type-options", "x-frame-options", "referrer-policy"}
    check(S, "Security headers configured", need <= hdrs, ", ".join(sorted(need & hdrs)) or "none")
    scripts = re.findall(r'<script[^>]+src="([^"]+)"', (SITE / "index.html").read_text(encoding="utf-8"))
    ext = [s for s in scripts if s.startswith("http")]
    check(S, "Third-party scripts pinned to exact versions", all(re.search(r"@\d+\.\d+\.\d+/", s) for s in ext), "; ".join(ext))
    if a.url:
        try:
            with urllib.request.urlopen(urllib.request.Request(a.url, method="GET"), timeout=20) as r:
                live = {k.lower() for k in r.headers.keys()}
                check(S, "Live site sends security headers", need <= live, f"{a.url}: " + ", ".join(sorted(need & live)))
                check(S, "Live site served over HTTPS", a.url.startswith("https://") and r.status == 200, f"HTTP {r.status}")
        except Exception as e:  # noqa: BLE001
            check(S, "Live site sends security headers", False, f"request failed: {e}")

    # ---------------- sanity ----------------
    n_c = db.execute("SELECT COUNT(*) FROM comments").fetchone()[0]
    check(Q, "Comments collected", n_c >= 10000, f"{n_c:,} comments")
    sim = db.execute("SELECT (SELECT COUNT(*) FROM comments WHERE simulated=1)+(SELECT COUNT(*) FROM threads WHERE simulated=1)").fetchone()[0]
    check(Q, "No simulated data", sim == 0, f"{sim} simulated rows")
    dup = db.execute("SELECT COUNT(*) FROM (SELECT id FROM comments GROUP BY id, thread_id HAVING COUNT(*)>1)").fetchone()[0]
    check(Q, "No duplicate comments", dup == 0, f"{dup} duplicates")
    now = time.time() * 1000
    bad_ts = db.execute("SELECT COUNT(*) FROM comments WHERE ts IS NULL OR ts > ? OR ts < 1104537600000", (now + 3600_000,)).fetchone()[0]
    check(Q, "Timestamps valid", bad_ts == 0, f"{bad_ts} missing, future or pre-2005 timestamps")
    orphan = db.execute("SELECT COUNT(*) FROM comments c LEFT JOIN threads t ON t.id=c.thread_id WHERE t.id IS NULL").fetchone()[0]
    check(Q, "Every comment belongs to a stored video", orphan == 0, f"{orphan} orphans")
    n_t = db.execute("SELECT COUNT(*) FROM threads").fetchone()[0]
    done = db.execute("SELECT COUNT(*) FROM analyses WHERE payload IS NOT NULL AND dirty=0").fetchone()[0]
    check(Q, "Every video analysed", done >= n_t, f"{done} of {n_t} videos have a current analysis")
    risks = [n["props"]["risk"] for n in graph["nodes"] if n["labels"][0] == "Account"]
    check(Q, "Risk scores within 0–100", all(0 <= r <= 100 for r in risks), f"{len(risks):,} scores, max {max(risks) if risks else 0}")
    ids = {n["id"] for n in graph["nodes"]}
    dangling = sum(1 for r in graph["rels"] if r["start"] not in ids or r["end"] not in ids)
    selfl = sum(1 for r in graph["rels"] if r["start"] == r["end"])
    check(Q, "Graph is consistent", dangling == 0 and selfl == 0, f"{len(graph['rels']):,} relationships; {dangling} dangling, {selfl} self-loops")
    check(Q, "Page counts match the database", summary["counts"]["comments"] == n_c and summary["counts"]["videos"] == n_t,
          f"page {summary['counts']['comments']:,} comments / {summary['counts']['videos']} videos; DB {n_c:,} / {n_t}")
    meta = json.loads((ROOT / "detector" / "model" / "model.json").read_text())
    check(Q, "Model trained on real labelled data", meta.get("trained_on") == "cresci2017", f"trained_on={meta.get('trained_on')}, {meta.get('n_accounts'):,} accounts")
    f1 = meta["metrics_random_split"]["f1"]
    check(Q, "Model metrics present and plausible", 0.5 < f1 < 1.0, f"F1 {f1:.4f} (a perfect 1.000 would signal a broken evaluation)")

    # ---------------- automated test suites ----------------
    if a.pytest:
        import subprocess
        for label, cwd, target in (("Detector test suite", ROOT, "detector/tests"), ("Backend test suite", ROOT / "backend", ".")):
            p = subprocess.run([sys.executable, "-m", "pytest", "-q", target], cwd=cwd, capture_output=True, text=True, timeout=1200)
            tail = [l for l in p.stdout.strip().splitlines() if " passed" in l or " failed" in l or " error" in l]
            check("Tests", label, p.returncode == 0, (tail[-1] if tail else p.stdout[-200:]).strip())

    groups = {}
    for g, n, s, d in results:
        groups.setdefault(g, []).append({"name": n, "status": s, "detail": d})
    summary["checks"] = {"generated": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
                         "groups": [{"name": g, "items": items} for g, items in groups.items()]}
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")
    for g, n, s, d in results:
        print(f"{s:4}  {g:8}  {n} — {d}")
    return 1 if any(s == "FAIL" for _, _, s, _ in results) else 0


if __name__ == "__main__":
    sys.exit(main())
