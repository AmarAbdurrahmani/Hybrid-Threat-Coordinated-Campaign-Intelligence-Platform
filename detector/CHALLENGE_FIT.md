# Challenge fit: bot detector only

Challenge 01, "AI-Powered OSINT & Hybrid Threat Intelligence". This page keeps the team honest about what we claim.

## 1. Product definition

A bot detector that takes a social-media account from public sources and answers one question, "is this account a bot?", with a calibrated BOT probability and the percentage contribution of every indicator, using weights learned from labelled data.

## 2. Challenge phrase to answer

| Challenge phrase | How the bot detector answers it |
|---|---|
| "uses AI" | Learned models only: LightGBM over account, timing, repetition, text and network features, plus a relational graph neural network. No hand-set weights. |
| "automate collection ... open sources" | YouTube Data API collector (real comments on Kosovo/Serbia videos); public Cresci-2017 dataset for training. |
| "social networks" | YouTube comments (application data) and Twitter (benchmark data). Other platforms are not covered; Meta, TikTok and X need paid or research access. |
| "processing and analysis" | Feature extraction, account graph, scoring, per-indicator percentage contributions. |
| "organized activities / hybrid threats" | Coordinated inauthentic accounts are the organised part of an information operation. The graph model scores shared text, timing and links between accounts. We detect the amplifier, not the sponsor. |
| "emerging patterns" | New bot families: leave-one-bot-family-out evaluation tests whether the model catches a family it never saw. |
| "traceable sources" | Every score links back to the account's own posts and URLs; each indicator points at the evidence behind it. |
| "timelines" | Posting-time features (bursts, hour-of-day, gaps, time since account creation) shown as evidence per account. We do not build event timelines. |
| "contextual analysis" | Indicator breakdown plus the Neo4j account graph (who is linked to whom, and why). The intent/narrative inputs shift weights but are never shown. |
| "confidence estimates" | Calibrated probability, bootstrap confidence intervals on F1, ECE (calibration error). |
| "situational awareness / decision-making" | An analyst gets a ranked, explained list of suspect accounts to review. A human decides. |
| "security incidents, cyber incidents, infrastructure disruptions, regional developments" | **Out of scope by design.** Depth over breadth: we do one task and measure it. Bots are the vector that amplifies these threats, so detecting them protects the information space around them. |
| "local and international media, public databases" | **Out of scope by design.** Media monitoring is a crowded, solved-ish problem; our contribution is whether the voices in the comments are real. |
| "identify events" | **Out of scope by design.** We identify accounts, not events. |

## 3. Judging criteria

| Criterion | Show | Say |
|---|---|---|
| Innovation | One account, BOT %, and its indicator percentages; the Neo4j graph with coordinated accounts linked. | "Most tools give a score; ours tells you exactly what drove it, in percent, and shows the network behind it." |
| Technical depth / AI | LightGBM plus relational GNN, learned weights, leave-one-family-out table. | "Nothing is hand-tuned; we test on bot families the model has never seen." |
| Feasibility / usability for Kosovo institutions | CLI scoring of a YouTube video's commenters; CSV/Neo4j export; runs on a laptop with free APIs. | "An analyst pastes a video URL and gets a ranked list in minutes, using free public data." |
| Impact | Coordinated accounts found in real Kosovo/Serbia comment threads (labelled as indicators). | "Bots are how narratives get amplified; flagging them lets institutions discount fake consensus." |
| Evidence / evaluation | F1 with bootstrap CI (random split and leave-one-family-out), calibration plot/ECE, labelling kit with kappa. | "We report an interval, not a number, and we say which numbers are benchmark and which are Kosovo." |
| Ethics / legal | The ethics box below; pseudonymised IDs; human review step. | "It flags accounts for a human to check. It never names who is behind them." |

## 4. Scope guardrails

Do:
- Show BOT % and the indicator percentages, always together.
- Show F1 with CI, the leave-one-family-out table, the calibration curve and ECE.
- Show the Neo4j graph view.
- Label every Cresci number "Twitter benchmark" and every YouTube number "unlabelled, indicators only" until the labelling kit gives a Kosovo F1.
- Say plainly what is out of scope (section 2).
- Report weak results (a bad held-out family) as they are.

Don't:
- Show or name intents, motives or narratives. They are hidden inputs.
- Claim attribution to a state, group or person.
- Present Twitter-benchmark F1 as Kosovo accuracy.
- Add dashboards, maps, SitReps, threat-landscape pages or new features.
- Call simulated data real. Use "exercise".
- Call a human-labelled-by-two-annotators sample "ground truth" before kappa is reported.

Existing dashboard (`frontend/`, `backend/`): the earlier coordination-monitoring web app. It stays as supporting infrastructure, for example its YouTube ingestion, pseudonymisation and SQLite store (`datasets.load_backend_db`) feed the detector. It is not the pitch and is not demoed as a product.

## 5. Hard judge questions

1. **"Your F1 is on Twitter. What is it on Kosovo?"** Unknown until labelled. We built a kit (two annotators, Cohen's kappa) so we can measure it; until then the YouTube output is an indicator, not a validated accuracy.
2. **"Bots on 2017 Twitter do not look like bots on YouTube today."** Agreed. That is why we report leave-one-family-out (unseen bot types) and do not claim transfer. Account, timing and repetition features exist on both platforms; text features are retrained on in-domain labels.
3. **"Is a flagged account a Russian/Serbian operation?"** We cannot say, and do not try. We estimate automation or coordination. Attribution needs platform data, legal process or all-source intelligence.
4. **"Can an adversary evade it?"** Yes; a careful operator can mimic humans. Learned weights over many indicator groups plus graph links make evasion costlier but not impossible. We report recall per bot family so weak spots are visible.
5. **"Why is this a hybrid-threat tool and not a bot classifier?"** Because the organised, coordinated use of fake accounts is the mechanism of many hybrid information operations. We cover that mechanism deeply and leave other threat types to other tools by choice.

## 6. Ethics and legal

- **Data protection**: Kosovo Law No. 06/L-082 on Protection of Personal Data and GDPR principles: public data only, purpose limitation (bot detection), data minimisation, retention limits.
- **Pseudonymisation**: account IDs are replaced by keyed hashes before storage and display; the key is kept separately. Analysts re-identify only through a logged, authorised step.
- **Platform terms**: collect only through the official YouTube Data API within its quota and terms; the Cresci dataset is used under its published research terms. No scraping of closed platforms.
- **Human in the loop**: output is a probability with reasons for an analyst to review. No automated blocking, reporting or enforcement.
- **No attribution**: we score behaviour, never identity, nationality, politics or sponsor. A high score can be a false positive; report the error rates.
- **Fairness**: check false-positive rates on genuine users (language mixing, high-volume humans) and publish them.
