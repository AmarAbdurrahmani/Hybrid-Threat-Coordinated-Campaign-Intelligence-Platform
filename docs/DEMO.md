# HTCCIP: pitch demo script

Five minutes from the live dashboard. Every step below names the panel the audience sees.
Rule for the whole talk: **simulated data is always called an exercise**, and every score is an
**indicator for a human analyst, never attribution.**

## Before you go on stage (2 minutes)

1. Open the dashboard **5 minutes early** so a sleeping Render instance can wake up. You only need the LIVE bar to go green
   if you plan to show live threads.
2. If the analyst key isn't remembered on this laptop, open `…/#key=<API_KEY>` once. The LIVE bar should turn green and the key
   field should collapse to a 🔑 **Key** button.
3. Optional real-data step: paste a YouTube Data API v3 key into the YouTube key field. Auto-Scan then works even if the
   backend is down, because it runs in the browser and says so.
4. Click **Reset demo** so the stage starts on the built-in exercise thread. Its header chip reads **EXERCISE · SIMULATED**.

## The walk-through

| # | Do | Point at | Say |
|---|----|----------|-----|
| 1 | Nothing: it is already on screen | Header chip **EXERCISE · SIMULATED**, globe, **Coordination overview** gauge | "This is a simulated exercise thread. Everything you see on it is labelled `(sim)`." |
| 2 | Scroll to **Comment timeline** | The red spike at T+3 min | "Twelve comments in under four minutes, starting three minutes after publication, eleven of them flagged: a swarm, not a conversation." |
| 3 | **Account network** | The red cluster (**K1 · 13 accounts** in the cluster card) | "Linked by shared friends, near-duplicate talking points and shared hosting. Tap a node to see the evidence." |
| 4 | **Threat replay** (already playing; 60×/120×/300×) | Comments arriving from INF-01/02/03 nodes | "Replay of how the swarm arrived, so an analyst can brief it." |
| 5 | **Bot / human evaluation lab** → Kosovo tab | Chips under each verdict, e.g. `repeated phrasing` `language mixing` `burst` next to **BOT 86%** | "Every score shows its top reasons. A slogan alone never makes a bot: xk-2 is an established account posting the same slogan, and it stays **HUMAN 2%**." |
| 6 | Same panel, dossier | **EXERCISE**, **PUBLIC RECORD** and **UNVERIFIED** tags | "The hypothesis is explicitly unverified. The detector measures coordination; attribution needs platform data, a legal process or all-source intelligence." |
| 7 | **Generate SitRep**, then **Export SitRep PDF** | "Operator identity and state nexus: NOT ESTABLISHED" | "The briefing is ready to circulate after review, and it never claims attribution." |
| 8 | **Simulate attack** (LIVE bar) | Thread list gains an `[EXERCISE]` thread, header chip **EXERCISE · SIMULATED** | "Twelve sock-puppets hit two threads 25 minutes apart. The cross-thread link (yellow edges) is what a single-thread tool misses." |
| 9 | **Auto-Scan YouTube** | Status line, then the scan list and header chip **REAL COMMENTS · INDICATORS ONLY** | "Now real public comments on Kosovo/Serbia/KFOR videos from the last 7 days, scored by the same engine." |

**Real data is usually quieter, and that is the point.** A typical scan shows Level 3–4 with a few flagged accounts. Say so:
the engine does not invent campaigns.

## If something fails on stage

| Symptom | What to do |
|---------|------------|
| LIVE bar red / "Server has no API_KEY configured" | Carry on: steps 1–7 and 9 need no backend. Skip step 8. |
| Auto-Scan says "Missing YouTube API key" | Paste the YouTube key into the form and click again; it runs in the browser. The red message clears itself after 20 s or when you load other data. |
| Auto-Scan "quota exceeded" | Click **Reset demo** and continue on the exercise thread. Repeat scans within 15 min are served from cache and cost nothing. |
| Globe blank | Scroll; it re-renders. The 2D panels carry the story on their own. |

## What is simulated and what is real

| Shown on screen | Status |
|-----------------|--------|
| Demo thread, its accounts, infrastructure nodes, UAS tracks, influence-farm map | **Simulated**: labelled `(sim)` / **EXERCISE · SIMULATED** |
| **Simulate attack** threads | **Simulated**: titled `[EXERCISE]`, labelled **EXERCISE · SIMULATED** |
| Lab corpus and account metadata | Hand-written **exercise** set; the hypothesis is **UNVERIFIED** |
| "Public precedent" in a dossier | **Public record**, cited for context only |
| YouTube Fetch / Auto-Scan comments | **Real public comments**; authors pseudonymised; scores are indicators only |

## Next steps (what we ask for)

These map to the panels above; see the README section "Next steps" for details.

1. **Analyst and mentor validation**: experts label a sample of real flagged and unflagged accounts from
   **Account network** clusters and SitReps. That gives precision/recall instead of exercise-only pass rates.
2. **False-positive threshold calibration**: tune the alert levels on real threads. Today a single 3-account cluster
   lifts a thread to Level 2 even when only ~6% of comments are flagged.
3. **More data sources**: Telegram channels (connector exists), portal comment sections, X/Facebook via
   research APIs. Cross-platform co-activity is what **Threat replay** and the cross-thread edges are built to show.
