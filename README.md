# Hybrid Threat & Coordinated Campaign Intelligence Platform — backend

FastAPI + NetworkX + sentence-transformers backend for the tactical dashboard. It
ingests real comments from YouTube and public Telegram channels, scores coordination,
streams results to the dashboard over WebSocket, and sends a SitRep to a Telegram
command chat when a thread reaches **LEVEL 1 (Strategic Threat)**.

```
 YouTube Data API v3 ─┐                                   ┌─► REST  /api/threads/{id}/analysis
 Telegram (Telethon) ─┼─► ingest.py ─► store (SQLite) ─► engine.py ─┼─► WS    /ws/live          ─► dashboard (served at /)
 Your portal webhook ─┤      pseudonymise IDs,           co-activity │
 /api/simulate-attack ┘      truncate IPs                graph (48h) └─► alerts.py ─► Telegram bot (SitRep)
```

## Quick start (about 10 minutes)

```bash
cd backend
python -m venv .venv && source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install torch --index-url https://download.pytorch.org/whl/cpu   # optional, keeps the install small
pip install -r requirements.txt
cp .env.example .env                                        # set API_KEY and PSEUDONYM_SECRET at least
pytest -q                                                   # 10 tests, about 2 s
uvicorn main:app --host 0.0.0.0 --port 8000
```

Open **http://localhost:8000**. The dashboard detects the backend and shows the LIVE bar
(thread picker, API key, **Simulate attack**, Demo data). OpenAPI docs: `/docs`.

The first start downloads `paraphrase-multilingual-MiniLM-L12-v2` (about 470 MB) from
Hugging Face. **Download it before the venue Wi-Fi does it for you.** Without it the
engine falls back to TF-IDF character n-grams (`/api/health` → `"embedder": "tfidf"`).

## Deploy (Render, free)

`render.yaml` deploys API + dashboard as one service. On render.com: **New → Blueprint →** this repo →
fill in the secret env vars → **Apply**. Live at `https://htccip-XXXX.onrender.com` (dashboard at `/`, docs at `/docs`).
Free tier: sleeps after 15 min idle (about a minute to wake: open it before you present), SQLite is
wiped on restart, and it runs the lean `requirements-deploy.txt` (TF-IDF embeddings, no torch).

## Stage demo script (3 minutes)

1. Open the dashboard → LIVE bar is green. Pick a real YouTube thread (or say "monitoring").
2. Click **Simulate attack**. Twelve sock-puppet accounts hit **two** threads 25 min apart;
   the co-activity graph links them across threads, the thread hits LEVEL 1.
3. Walk the panels: timeline spike → threat vectors → map / infrastructure → network
   (yellow = cross-thread co-activity) → inspector evidence.
4. Show the phone: the SitRep arrives in the command chat tagged **EXERCISE**
   (set `ALERT_DRY_RUN=false` first; test with `POST /api/alerts/test/{thread_id}`).
5. **Generate Operational Briefing / SitRep**: the full report, generated server-side.

## Endpoints

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/health` | Status, embedder in use, which sources are configured |
| GET | `/api/threads` | Threads with comment count and current alert level |
| GET | `/api/threads/{id}/analysis` | Full analysis (contract v1, see `frontend/contract.ts`) |
| GET | `/api/threads/{id}/sitrep` | Plain-text SitRep |
| GET | `/api/graph` | Global co-activity graph (nodes/edges JSON, Louvain communities) |
| POST* | `/api/simulate-attack` | Inject an exercise swarm (`spread_threads`, `accounts`, `window_s`, `lang`: en/sq) |
| POST* | `/api/ingest/comments` | Push any batch (`thread_id`, `comments[{user, ts, text, account?, infra?}]`) |
| POST* | `/api/ingest/portal` | Your own portal's comments **with server-side IP** → ASN/geo via GeoLite2 |
| POST* | `/api/sources` | Watch a YouTube video ID or Telegram channel |
| POST* | `/api/sources/youtube/poll` | Poll YouTube now |
| GET | `/api/alerts` | Alert log (sent / dry-run) |
| POST* | `/api/alerts/test/{id}` | Force a SitRep for a thread |
| WS | `/ws/live?key=…` | Push: `{"type":"analysis","thread_id","level","alert"}` |

`*` requires header `X-API-Key` when `API_KEY` is set.

## How the engine scores (engine.py)

`risk = 0.30·Temporal + 0.25·Linguistic + 0.25·Narrative + 0.20·Network`, renormalised over
the signals that exist for a comment. High ≥ 60, Medium ≥ 35. LEVEL 1 = ≥ 30 % coordinated
**and** a cluster of ≥ 5 accounts.

- **Temporal:** density in a sliding 15-min window vs the thread's own baseline rate (with a
  Poisson tail p-value), ±2-min micro-bursts, nearest-neighbour gaps, posting right after publication.
- **Linguistic:** same-word Geg/standard doublets, Serbian→Albanian calques (and their literal
  English renderings), verbatim trigram reuse across accounts, optional LLM review
  (`LLM_ENABLED=true`, only for comments already ≥ `LLM_MIN_SCORE`).
- **Narrative:** threat-vector lexicon (sq + en), multilingual embedding similarity to vector
  exemplars, and near-duplicate talking points (cosine ≥ 0.82 with MiniLM).
- **Network:** account age clustering, follower counts, friend-list overlap where available,
  **cross-thread co-activity** (accounts within 5 min of each other in ≥ 2 distinct threads,
  Louvain communities), shared non-residential infrastructure.

Thresholds are starting points. **Calibrate on labelled data** (e.g. the Kosovo/Albania sets
from fact-checkers or platform takedown archives) and report precision/recall to the jury.

## What each source can and cannot give you

| Source | Comments | Account age / followers | IP / ASN | Notes |
|---|---|---|---|---|
| YouTube | ✓ (`commentThreads.list`, 1 unit/page) | ✓ via `channels.list` (subscribers may be hidden) | ✗ | 10,000 quota units/day by default; `search.list` costs 100 |
| Telegram | ✓ replies in a channel's linked discussion group | ✗ | ✗ | Needs a user session (Telethon); bots can't read arbitrary channels |
| Your portal | ✓ | if your CMS has it | ✓ server logs | The only source where infrastructure mapping is real |
| Simulator | ✓ | ✓ | ✓ documentation ranges | Always `simulated=true`, alerts tagged EXERCISE |

**The infrastructure map only fills with real data for platforms you operate.** YouTube and
Telegram never expose commenters' IPs. Say this to the jury before they ask.

## Connecting the dashboard

- **Simplest:** the backend serves `frontend/index.html` at `/` (same origin, no CORS). Live mode
  switches on automatically when `/api/health` answers with `contract: "1"`. Opening the file
  from disk or claude.ai keeps the offline demo.
- **React / Vite / Next.js:** types and a `useLiveThread` hook are in `frontend/contract.ts`.
  Proxy `/api` and `/ws` to `:8000` (Vite: `server.proxy`), or add your origin to `CORS_ORIGINS`.
- The panels read the same fields the built-in engine produces, so a backend result renders
  without changes (`fromContract()` in `index.html` only rebuilds Maps and Dates).

## Legal & safety notes for a defence setting

- User IDs are pseudonymised (HMAC) before storage; display names are dropped by default;
  portal IPs are truncated to /24. Kosovo's personal-data protection law still applies to
  pseudonymised data. Agree a retention period and a lawful basis with the ministry's legal office.
- Follow the YouTube API Services Developer Policies (data retention/refresh) and Telegram's ToS.
  Use a dedicated research account for Telethon.
- Scores are **indicators of coordination, not attribution**. Every SitRep says so, and
  "state nexus" is never set automatically. Keep a human analyst between the alert and any action.
- `ALERT_DRY_RUN=true` is the default, so nothing is sent until you turn it off.

## Tested vs untested (be honest on stage)

Tested here (`pytest`): engine on the 25-comment seed thread (matches the dashboard: 25/25,
LEVEL 1, 13-account cluster), Albanian and English calque/doublet detection, cross-thread
co-activity + Louvain, simulator → LEVEL 1, alert dry-run + cooldown + idempotent re-ingest,
SitRep, YouTube response parsing (mocked), IP truncation. The dashboard's live mode was driven
in a browser against the real pipeline through a stand-in HTTP server.

Not run here (packages unavailable in the build sandbox): the FastAPI app under uvicorn, the
WebSocket, real YouTube/Telegram calls, the MiniLM model, Telegram bot delivery. Run
`pytest`, then the demo script once on the venue network, before you present.
