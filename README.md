# Hybrid Threat & Coordinated Campaign Intelligence Platform — backend

FastAPI + NetworkX + sentence-transformers backend for the tactical dashboard. It
ingests real comments from YouTube and public Telegram channels, scores coordination,
streams results to the dashboard over WebSocket, and queues a Telegram SitRep
when a thread reaches **LEVEL 1 (Strategic Threat)**. The Operations panel manages
campaign cases, watched sources, analyst feedback, and alert acknowledgments.

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
pytest -q
uvicorn main:app --host 0.0.0.0 --port 8000
```

Open **http://localhost:8000** for the **Hybrid Threat Operations Center** (globe / 2D map,
threat replay, counter-UAS, SitRep). Enter the analyst `API_KEY` in the LIVE bar once (it is remembered; see
"Analyst key without retyping") to load monitored data; the built-in demo thread runs without a backend. Select a five-minute
timeline bar to filter the comment inspector; `/#batch` opens the paste-a-batch analyzer.
The Stitch analyst workspace (triage queue, cases, sources, alerts) is at
**/monitor.html**, linked as **Workspace** in the dashboard. Choose **Live** there and enter
the key, or **Exercise** for a simulated investigation. OpenAPI docs: `/docs`.

The first start downloads `paraphrase-multilingual-MiniLM-L12-v2` (about 470 MB) from
Hugging Face. **Download it before the venue Wi-Fi does it for you.** Without it the
engine falls back to TF-IDF character n-grams (`/api/health` → `"embedder": "tfidf"`).

## Deploy (Render, free)

`render.yaml` deploys API + dashboard as one service. On render.com: **New → Blueprint →** this repo →
fill in the secret env vars → **Apply**. Live at `https://htccip-XXXX.onrender.com` (dashboard at `/`, docs at `/docs`).
The free deployment runs the lean `requirements-deploy.txt` and uses temporary SQLite
storage. For monitoring that must survive redeploys, set `DB_PATH` to a file on an attached
persistent disk before ingesting data. Run one backend process for the SQLite delivery queue,
pollers and WebSocket tickets.

## Stage demo script (3 minutes)

Full pitch walkthrough, on-stage fallbacks and the simulated-vs-real table: [docs/DEMO.md](docs/DEMO.md).

The dashboard uses a light theme with a Leaflet/OpenStreetMap map; the 3D globe is an optional view. `frontend/dark.html` keeps the pre-redesign dark dashboard as a fallback. The **Platforms & threat landscape** section is a public-record summary (sources on every card), not detector output.

1. Open the dashboard → LIVE bar is green. Pick a real YouTube thread (or say "monitoring").
2. Click **Simulate attack**. Twelve sock-puppet accounts hit **two** threads 25 min apart;
   the co-activity graph links them across threads, the thread hits LEVEL 1.
3. Walk the panels: timeline spike → threat vectors → map / infrastructure → network
   (yellow = cross-thread co-activity) → inspector evidence.
4. Open **Operations → Campaign cases → Exercise cases**. Open the suggested case, review the
   linked evidence, and download the case SitRep.
5. **Operations → Alerts** shows delivery status and acknowledgment. Set
   `ALERT_EXERCISE_CHAT_ID` to a separate destination and `ALERT_DRY_RUN=false` to
   deliver exercise alerts.

## Endpoints

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/health` | Status, embedder in use, which sources are configured |
| GET | `/api/threads` | Threads with comment count and current alert level |
| GET | `/api/threads/{id}/analysis` | Full analysis (contract v1, see `frontend/contract.ts`) |
| GET | `/api/threads/{id}/sitrep` | Plain-text SitRep |
| GET | `/api/graph` | Global co-activity graph (nodes/edges JSON, Louvain communities) |
| GET | `/api/infrastructure` | Regional egress and comment coverage from current thread analyses |
| POST | `/api/simulate-attack` | Inject an exercise swarm |
| POST | `/api/osint/youtube` | Fetch a public video's comments (URL or ID, optional per-request `api_key`) into the pipeline |
| POST | `/api/osint/youtube/auto-scan` | Discover recent relevant videos (`search.list`), fetch their comments and run detection on each |
| GET | `/api/osint/youtube/auto-scan/defaults` | Default auto-scan queries, lookback and cache window |
| POST | `/api/lab/evaluate` | LLM second opinion (bot / human) for the evaluation lab; 503 unless `LLM_ENABLED` |
| POST | `/api/ingest/comments` | Ingest external comments with scoped IDs and optional evidence URLs |
| POST | `/api/ingest/portal` | Ingest your portal's comments with server-side IP for GeoIP lookup |
| GET / POST | `/api/sources` | List source health or watch a new source |
| POST | `/api/sources/{id}/{action}` | Pause, resume, retry, or run a bounded backfill |
| GET | `/api/alerts` | Delivery status, attempt count and acknowledgment |
| POST | `/api/alerts/{id}/acknowledge` | Acknowledge an alert |
| POST | `/api/alerts/test/{id}` | Force a delivery job or dry-run record |
| GET / POST | `/api/cases` | List or open campaign cases |
| GET | `/api/cases/suggestions` | Group threads with repeated account-pair evidence |
| GET | `/api/cases/feedback` | Count latest decisions for reviewed cases |
| GET / PATCH | `/api/cases/{id}` | Read evidence or edit title, owner, status and thread links |
| POST | `/api/cases/{id}/reviews` | Record a review and rationale |
| GET | `/api/cases/{id}/sitrep` | Export the case and captured evidence as text |
| POST | `/api/ws-ticket` | Get a single-use live connection ticket |
| WS | `/ws/live?ticket=…` | Stream analysis and alert delivery updates |

Use `X-API-Key: <API_KEY>` for monitoring and operations. The two ingestion
endpoints use `INGEST_API_KEY` when configured and fall back to `API_KEY`.
Health and the static dashboard remain public. `ALLOW_UNAUTHENTICATED=true`
is available for isolated local demos. Use `?simulated=true` for exercise graphs,
cases, suggestions and feedback. Collector backfills revisit a bounded recent set
of source pages or posts.

`GET /api/infrastructure` returns rounded regional coordinates, comment counts, and
shared-egress coordination signals from available network telemetry. It also returns
the number of comments analyzed and mapped. Individual account identifiers and IP
addresses are omitted. Use the analyst API key, `?simulated=true` for exercises,
and `thread_id` to limit the report to one thread.

`POST /api/osint/youtube` takes `{"youtube_url_or_id": "...", "api_key": "...", "max_pages": 3}` and accepts
`watch?v=`, `youtu.be/`, `/shorts/` and `/live/` links or a raw 11-character ID. It uses the request's `api_key`
when given (never stored or logged), otherwise `YOUTUBE_API_KEY` from `backend/.env` or the Render environment.
Authors are pseudonymised; `likeCount` is kept per comment. Errors come back as clear messages: missing or invalid
key (400), quota exceeded (429), comments disabled (409), video not found (404). In the dashboard, the
**Fetch YouTube** form uses this endpoint when the backend is connected; without a backend it fetches directly
from the browser with the key typed into the form (kept in that tab's session storage only) and scores locally.

**Auto-scan (no URL needed).** `POST /api/osint/youtube/auto-scan` takes
`{"queries": ["Kosovo Serbia", ...], "order": "date"|"relevance", "max_videos": 10, "comment_pages": 1, "api_key": "..."}`
(all optional). Blank `queries` uses `YOUTUBE_AUTOSCAN_QUERIES` (`;`-separated; default
`Kosovo Serbia; Kosova Serbia; Kosovo Srbija; Kosovo NATO KFOR`). For each query it runs `search.list`
(`type=video`, `maxResults=10`, last `YOUTUBE_AUTOSCAN_LOOKBACK_H` hours). It then ranks the videos by `commentCount` with one
`videos.list` call, so zero-comment and comments-disabled videos are skipped without fetching. Next it pulls `commentThreads.list`
(`part=snippet`, `maxResults=50`) for the top videos, adds channel ages, and ingests each video as a thread, which runs the full
detection pipeline (near-duplicates, bursts, clusters, co-activity, alerts, WebSocket updates). The response
lists every video with its level, flagged accounts and clusters, plus the totals and the quota spent.
Responses are cached in memory for `YOUTUBE_AUTOSCAN_CACHE_S` (default 900 s). A repeat click spends 0 units.
Comments-disabled and not-found results are cached too. Cost is about 100 units per query plus about 12, so the default
four queries cost about 413 units. Quota or key errors stop the scan cleanly: you get a clear 4xx if nothing was scanned,
or partial results with a warning. Set `YOUTUBE_AUTOSCAN_EVERY_MIN` to run the scan unattended (off by default).
In the dashboard, **Auto-Scan YouTube** (under the Fetch YouTube bar) does all of this in one click,
loads the highest-risk video and lists the others (click one to open it). With no backend it runs the same flow in the
browser using the key from the form.

**Which path runs, and why.** On each click the dashboard checks `/api/health`. It retries for up to about a minute,
so a sleeping Render instance can wake. The status line then tells you which path ran:
- `Backend <host> · server YOUTUBE_API_KEY` (or `· your YouTube key`): the scan ran on the backend and was stored.
- `Backend offline, so this used your browser key`: the backend was unreachable.
- `Backend not usable, so this used your browser key`: the backend is up but has no `API_KEY`, or no analyst key was entered.
- `Missing YouTube API key: <reason>`: no backend path and nothing in the form.
The backend needs **`API_KEY`** (the analyst key you paste in the LIVE bar) and **`YOUTUBE_API_KEY`**. Locally both go in
`backend/.env`, which is loaded from that folder whatever directory uvicorn starts in. On Render, set them in the
service's Environment tab. Without `API_KEY`, analyst endpoints return 503 with an explicit message, and
`/api/health` shows `analyst_key_configured: false`.

**Fallback rules.** With a YouTube key in the form, any backend failure falls back to the same scan in the browser, and the status line names the reason:
- the backend is offline, or a sleeping Render instance doesn't answer within 20 s;
- the backend has no `API_KEY`, or there is no or a wrong analyst key;
- the backend returns an error (4xx/5xx).

The browser scan stays on screen across live reconnects. A key-level YouTube error stops the scan with a precise message: invalid key, quota, API not enabled, or a key restricted to other websites or IPs (`API_KEY_HTTP_REFERRER_BLOCKED` / `API_KEY_IP_ADDRESS_BLOCKED`).

**Local dev servers.** A dashboard opened on another localhost port (Live Server `:5500`, Vite, `python -m http.server`) has no `/api` of its own. It finds the backend by itself, trying the same origin, then `http://localhost:8000`, then Render. `?api=` pins one. CORS allows any `localhost` / `127.0.0.1` port by default (`CORS_ALLOW_LOCALHOST=false` disables this). `ANALYST_API_KEY` is accepted as an alias of `API_KEY`. A repo-root `.env` is loaded too, after `backend/.env`.

**Analyst key without retyping.** The dashboard (and the workspace) resolve the analyst key in this order; the first hit wins:
1. A one-time link `https://<dashboard>/#key=<API_KEY>`. The URL fragment is never sent to any server, and the page removes it
   from the address bar immediately.
2. The key remembered on this device (`localStorage`, one entry per backend origin). Typing it once in the LIVE bar saves it there.
3. Local dev only: with `DEV_AUTO_ANALYST_KEY=true` in `backend/.env`, the dashboard served by that backend on `localhost`
   fetches `API_KEY` from `GET /api/dev/analyst-key`. The endpoint returns 404 unless the client, the Host header and the
   Origin are all loopback and same-origin. Never set it on Render.

There is no build-time key (for example `VITE_ANALYST_API_KEY`): anything baked into the static Vercel page is public.
Once connected, the key field collapses into a **Key** button. Click it to change the key, or clear the field to forget it.
A saved key the backend rejects reopens the field with "Saved analyst key was rejected".

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

## Next steps

Each item builds on an output that is already on the dashboard.

1. **Analyst / mentor validation**
   - *Today:* **Account network** clusters (e.g. `K1 · 13 accounts`), the lab verdicts (`BOT 86%` with its signal chips) and the **SitRep**.
   - *Next:* domain experts label a sample of real flagged and unflagged accounts. The lab's exercise pass rate (31/31) then becomes measured precision and recall on real threads.
2. **False-positive threshold calibration**
   - *Today:* the alert level in the **Coordination overview** and the SitRep.
   - *Next:* calibrate `engine.py` levels on labelled real threads. Currently a single cluster of 3 or more accounts lifts a thread to Level 2, even when only about 6% of its comments are flagged. Make that rule depend on cluster evidence strength and on the flagged share.
3. **More data sources**
   - *Today:* YouTube (Fetch / Auto-Scan) and the Telegram connector, plus cross-thread co-activity edges and **Threat replay**.
   - *Next:* add portal comment sections and research-API access to X and Facebook, so that cross-platform swarms appear in the same network and replay.
4. **Operational hardening:** a persistent database on Render, per-analyst keys, an audit log of SitRep exports, and the retention period agreed with the legal office (see below).

## Legal & safety notes for a defence setting

- User IDs are pseudonymised (HMAC) before storage; display names are dropped by default;
  portal IPs are truncated to /24. Kosovo's personal-data protection law still applies to
  pseudonymised data. Agree a retention period and a lawful basis with the ministry's legal office.
- Follow the YouTube API Services Developer Policies (data retention/refresh) and Telegram's ToS.
  Use a dedicated research account for Telethon.
- Scores are **indicators of coordination, not attribution**. Every SitRep says so, and
  "state nexus" is never set automatically. Keep a human analyst between the alert and any action.
- `ALERT_DRY_RUN=true` is the default, so nothing is sent until you turn it off.

## Tests

The suite covers scoring, delayed cross-thread updates, persisted analyses, authenticated
REST and WebSocket access, source controls, ingestion validation and deduplication,
case review and export, and exercise alert routing. YouTube data and Telegram alert
delivery use test fixtures.
