# Pitch playbook: bot detector (Challenge 01)

Numbers below are filled from `detector/REPORT.md` (Cresci-2017 run, 21 Nov 2026). The original note said: the current
`REPORT.md` and `detector/model/` are from the **synthetic** smoke-test set; do not quote them. Retrain, then fill in:
`0.996`, `0.993–0.998`, `0.999`, `0.006 (0.6 points)`, `0.984`, `0.972 (fake followers)` (+ family name),
`1,822 of 2,039 (true bots: 1,822; 9 false alarms, 9 missed)` (YouTube accounts over threshold), `COPY 0.84 · SYNC 0.83 · REPLY 0.70 (LINK and CO_THREAD have no training edges, so they are switched off)` (learned relation weights).

## 1. The narrative (say this in 25 seconds)

1. **Problem.** In Kosovo's information space, a handful of automated or coordinated accounts can make a fringe message look like public opinion, and an analyst reading comment threads cannot tell real voices from manufactured ones.
2. **What we built.** A detector that takes public accounts, answers one question, "is this account a bot?", with a calibrated probability, and shows what percentage of that decision each indicator carries, plus the account graph behind it.
3. **Why trust it.** Every weight is learned from human-labelled data, we report the score on bot types the model never saw, and we say plainly which numbers are Twitter benchmark and which are Kosovo.

Tagline if needed: **"One question, one probability, every reason in percent."**


> **Kosovo YouTube data, said honestly (replace any older YouTube demo line with this):** we collected 632 real accounts /
> 697 comments from 25 Kosovo–Serbia videos. Run on them, the Twitter-trained model is *not* reliable: most accounts have
> one comment, and benchmark signals such as account age encode when the Twitter groups were collected. Trained on Twitter
> accounts cut to 1–3 posts with YouTube-available features only, unseen-bot-type F1 drops to 0.89–0.95
> (`REPORT_youtube.md`). So in the pitch YouTube is the **next step**: collector built, blind labelling sheet ready,
> `local-f1` gives the Kosovo number once two people label it. Do not show YouTube BOT verdicts as findings.

## 2. Five-minute demo (terminal + Neo4j Browser + report)

Before the pitch (not live): `python -m detector train` (Cresci-2017), `python -m detector score --data cresci2017 --split test --prior -1`,
copy the four CSVs from `detector/data/scores_cresci2017_test/neo4j/` into Neo4j's `import/` folder (Neo4j Desktop: DBMS →
`...` → Open folder → Import), paste `import.cypher` into Neo4j Browser and run it (multi-statement editor on; or
`cypher-shell -u neo4j -p <pw> -f import.cypher`). Then run once: `MATCH (a:Account {verdict:'BOT'}) SET a:Bot;`
(a display label so bots get their own colour). Free options: Neo4j Desktop (local, recommended, works offline) or
AuraDB Free (no `file:///`; host the CSVs at an https URL or use Data Importer, so only as a backup).

| Time | Screen | Do | Say |
|---|---|---|---|
| 0:00–0:30 | Slide or blank terminal | Nothing | The three sentences from section 1. |
| 0:30–1:20 | Terminal | `python -m detector score --data cresci2017 --split test --prior -1` (pre-run; show the output) | "2,039 human-labelled accounts the model never trained on. Each line: account, BOT %, top indicators in percent. 1,822 flagged; against the true labels that is 9 false alarms and 9 missed bots: F1 0.995." |
| 1:20–2:10 | Terminal | `python -m detector explain --data cresci2017 --split test` (≈30 s; no id = highest-scoring account; it also prints the TRUE label) | "One decision opened up. Indicator model says X, graph network says Y, calibrated result Z. Every bar is a share of the decision; plus pushes towards bot, minus towards human. Below: its neighbours and the learned weight of each link type." |
| 2:10–2:50 | Neo4j Browser | Query A | "This is the model, not a drawing of it. Each indicator points at BOT with the weight the model learned. Nobody typed these numbers." |
| 2:50–3:30 | Neo4j Browser | Query B | "The same account as in the terminal, now as evidence edges. The percentages match because both come from the same model output." |
| 3:30–4:10 | Neo4j Browser | Query C, then Query D | "Accounts that copied text, posted in sync or shared links. Red are over the threshold. The number on each edge is the learned weight: the network decided how much a SYNC link counts against a LINK link. Query D prints those five learned values." |
| 4:10–4:45 | `REPORT.md` (editor preview) | Scroll to the two tables | "Seen bot types: F1 `0.996`, interval `0.993–0.998`. Bot types never seen in training: mean `0.984`, worst `0.972 (fake followers)`. That second line is the honest one. Calibration error `0.006 (0.6 points)`." |
| 4:45–5:00 | Terminal or slide | Show `label_sheet.csv` filename | "This is Twitter-trained. The Kosovo number comes from this blind sheet, two annotators, one command. It flags accounts for a human; it never says who is behind them." |

**Cypher (Neo4j 5; `bot_probability` is 0–100).**

```cypher
// A. The learned indicator → BOT weights (star around the Verdict node)
MATCH (i:Indicator)-[w:WEIGHT]->(v:Verdict {name:'BOT'})
WHERE w.importance_pct > 0
RETURN i, w, v;

// B. The highest-scoring account with its evidence (graph view; switch to Table for the numbers)
MATCH (a:Account) WITH a ORDER BY a.bot_probability DESC LIMIT 1
MATCH (a)-[e:EVIDENCE]->(i:Indicator)
RETURN a, e, i;
// table variant:
// MATCH (a:Account {account_id:'PASTE_ID'})-[e:EVIDENCE]->(i:Indicator)
// RETURN i.name AS indicator, e.direction AS dir, abs(e.percent) AS percent ORDER BY percent DESC;

// C. Coordinated cluster around flagged accounts, with learned edge weights
MATCH p = (a:Account)-[:COPY|SYNC|LINK|REPLY|CO_THREAD*1..2]-(b:Account)
WHERE a.bot_probability >= 50
RETURN p LIMIT 150;

// D. The learned relation weights, read back from the edges (learned_weight = edge count × learned gate)
MATCH (:Account)-[r:COPY|SYNC|LINK|REPLY|CO_THREAD]->(:Account)
RETURN type(r) AS relation, count(*) AS edges,
       round(avg(r.learned_weight / r.weight), 3) AS learned_gate,
       round(sum(r.learned_weight), 1) AS total_learned_weight
ORDER BY learned_gate DESC;
```

If C returns nothing, lower `50` to the threshold printed by `score`, or drop the `WHERE`. Save A–D as Browser
Favourites so nobody types during the pitch.

**Styling.** Neo4j Browser styles per label, not per value, so: click the `Account` chip under the graph → caption
`bot_probability`; `Bot` chip → red, largest size; `Indicator` → caption `name`; `WEIGHT` → caption `importance_pct`;
`EVIDENCE` → caption `percent`; coordination edges → caption `learned_weight`. Or save as `style.grass` and drag into Browser:

```
node.Account { color: #A5ABB6; diameter: 40px; caption: '{bot_probability}'; }
node.Bot { color: #D63333; diameter: 70px; caption: '{bot_probability}'; }
node.Indicator { color: #4C8EDA; diameter: 60px; caption: '{name}'; }
node.Verdict { color: #000000; diameter: 80px; caption: '{name}'; text-color-internal: #FFFFFF; }
relationship.WEIGHT { caption: '{importance_pct}'; shaft-width: 3px; }
relationship.EVIDENCE { caption: '{percent}'; }
relationship.COPY, relationship.SYNC, relationship.LINK, relationship.REPLY, relationship.CO_THREAD { caption: '{learned_weight}'; }
```

For true size/colour by value, use Neo4j Bloom (bundled with Desktop): rule-based styling on `Account.bot_probability`,
range → size and a grey-to-red colour gradient. Test whichever you choose the night before.

**Fallback.** Screenshots of A–D results in a folder open in the image viewer (take them after the final import). If
Neo4j will not start, stay in the terminal: `explain` already prints the evidence bars, the graph neighbourhood and the
learned relation weights. If the terminal fails, open `scores.json` and `REPORT.md`. Never debug on stage.

## 3. Why this is AI, and ours

- **Learned, not hand-set.** About 30 indicators per account. LightGBM learns their weights and interactions from labelled accounts; a relational graph network learns one weight per link type (COPY, SYNC, LINK, REPLY, CO_THREAD). There is no rule file and no scoring formula we wrote.
- **A parameter changes the output.** Show `COPY 0.84 · SYNC 0.83 · REPLY 0.70 (LINK and CO_THREAD have no training edges, so they are switched off)` (last line of `explain`, or Query D). Retrain on different data and those numbers move, and so does every score: the synthetic run and the Cresci run give different indicator rankings. If asked for proof, show the two weight tables side by side (keep the synthetic `REPORT.md` as `REPORT_synthetic.md`). Be exact: a relation with no edges in the training data keeps its initial value, and the report lists which.
- **Per-account percentages are exact.** They are TreeSHAP contributions of the LightGBM model for that account, normalised so the absolute values sum to 100. They explain the indicator model; the graph network's share is shown separately as "model parts" and as learned edge weights.
- **Calibration.** The two models are averaged and passed through isotonic regression fitted on a validation split. "80%" means about 80 of 100 such accounts were bots in held-out data. ECE `0.006 (0.6 points)` is the average gap.
- **What the graph network adds.** A single account can look human; five accounts posting the same text within minutes do not. The network lets a neighbour's evidence count, by an amount it learned per link type. The leave-one-family-out table shows LightGBM, GNN and ensemble separately, so the claim is checkable.
- **Context weighting.** One folded line; it shifts weights and is never shown or named. Do not elaborate.

## 4. The evaluation story

1. **Seen bot types (random split):** F1 `0.996`, 95% bootstrap interval `0.993–0.998`, PR-AUC `0.999`. Say "optimistic" yourself, before a judge does.
2. **Unseen bot type (leave-one-family-out):** train without one bot family, test on it. Mean `0.984`, worst `0.972 (fake followers)`. "This is the number that matters, because the next campaign will not look like the last." Name the weakest family and why (e.g. fake followers barely post, so timing and copy indicators have little to work with).
3. **Calibration:** ECE `0.006 (0.6 points)`, and the predicted-vs-observed table in the report. The threshold is chosen on validation data only.
4. **Transfer (Twitter → YouTube):** "We do not claim the Twitter F1 holds on Kosovo YouTube. So we built the measurement: `score` writes a blind sheet with no scores on it, two people label it, `python -m detector local-f1 --labels label_sheet.csv --scores scores.csv` reports Cohen's kappa and F1 with an interval." If even a small sample is labelled before the pitch, report it with n and kappa; it is the strongest slide you can have.
5. **Framing:** most teams show one accuracy number on their own data. We show an interval, a harder test, and the limit. That is the reason to believe the rest.

## 5. Mapping to the challenge text

| Challenge asks for | We deliver |
|---|---|
| AI that automates collection, processing, analysis | YouTube Data API collector → features + account graph → learned scoring, one command |
| Social networks, open sources | Real YouTube comment threads on Kosovo–Serbia videos; public Cresci-2017 benchmark |
| Organised activities, hybrid threats, emerging patterns | Coordinated inauthentic accounts (the amplifier of an information operation); unseen-family test for new bot types |
| Traceable sources | Every score traces to the account's own posts, links and graph edges |
| Timelines | Posting-time evidence per account (gaps, bursts, hour spread). No event timelines |
| Contextual analysis | Indicator percentages + the Neo4j neighbourhood |
| Confidence estimates | Calibrated probability, ECE, bootstrap intervals |
| Decision support | Ranked, explained list for a human analyst |

**Out of scope by design:** events, media monitoring, cyber and infrastructure incidents. "We chose one part of the
challenge and measured it properly. Bots are how the other threats get amplified."

## 6. Rubric playbook

| Criterion | Top marks need | Our proof point |
|---|---|---|
| Innovation | Something other teams do not have | Decision shown as a learned network in Neo4j: indicator → BOT weights, per-account evidence in percent, learned link-type weights |
| Technical depth | Real models, understood by the team | LightGBM + relational GNN + isotonic calibration + TreeSHAP; component scores reported separately |
| Impact / Kosovo relevance | Real local data, a named user | 632 real accounts from 25 Kosovo–Serbia videos; analyst triage of comment threads |
| Feasibility / deployment | Runs now, cheap | Laptop, free API quota, CLI + CSV + Neo4j; backend endpoint `POST /api/bot/score` exists |
| Evaluation rigour | Honest, reproducible numbers | Intervals, unseen-family test, calibration, in-domain labelling kit with kappa; tests in `detector/tests` |
| Ethics / legal | Concrete safeguards | Pseudonymised IDs, official API only, human review, no attribution, Law No. 06/L-082 and GDPR principles |
| Presentation | Clear, on time, no crash | One question, four queries saved as favourites, rehearsed fallback |

## 7. Ten judge questions

1. **Why no UI?** The analyst's tools already exist: a terminal, a CSV, Neo4j. We spent the time on the model and its evaluation. The output is JSON, so any interface can sit on top.
2. **How do you know it is a bot?** We do not know; we estimate. The model learned from accounts humans labelled, and it gives a probability with the reasons. A person decides.
3. **False positives on real Kosovars?** They will happen, especially for very active people and copy-pasted slogans. That is why the output is a probability with evidence, precision is in the report, nothing is automated, and the labelling sheet measures the local false-positive rate.
4. **Albanian and Serbian?** Most indicators are language-independent: timing, repetition, account age, graph links. Text indicators are style counts (length, capitals, links, script mixing), not vocabulary, so there is no English-only model inside.
5. **Can bot makers evade it?** A careful one can. They must fake rhythm, vary text and avoid acting together all at once, which costs them scale. The unseen-family table shows where we are weak today.
6. **Data protection?** Public comments only, via the official API. Author IDs are replaced by keyed hashes before storage. Purpose-limited to bot detection, in line with Law No. 06/L-082 and GDPR principles.
7. **What does 87% mean?** Of accounts the model scores around 87%, about 87 in 100 were bots in held-out benchmark data. On YouTube that mapping is not yet verified, so read it as a ranking until the local labels are in.
8. **Why Twitter data?** It is the best public bot dataset with human labels and several bot families; none exists for Kosovo. We train where labels exist and measure transfer instead of assuming it. Some account fields (followers, following) are missing on YouTube; the model handles missing values, and we say so.
9. **How would KSF / AKI / police use it tomorrow?** Give it video IDs, get a ranked list in minutes, open the top accounts in Neo4j, review the evidence, decide. It runs on one laptop inside their network; no data leaves.
10. **What is next?** Label the 632 accounts and publish the Kosovo F1; retrain with those labels; add platforms as lawful access allows; periodic retraining as bot behaviour changes.

Also ready: **"Who runs these bots?"** "We cannot say and do not try. We measure behaviour, not identity or sponsor."

## 8. Pre-pitch checklist

- [ ] Retrained on Cresci-2017; `REPORT.md` first line says `Dataset: cresci2017`; placeholders here filled.
- [ ] `score` and `explain` run cleanly on the demo laptop, offline, in under 30 s each (time them; if slower, run `score` before walking up and show the scroll-back).
- [ ] Neo4j started, data imported, `Bot` label set, styling applied, queries A–D saved as favourites and each run once.
- [ ] Terminal: font 20 pt+, light theme, window maximised, UTF-8 (`chcp 65001`) so the bars render, history cleared.
- [ ] Screenshots of all four queries + both terminal outputs in one folder; also on a USB stick and a phone.
- [ ] Charger, HDMI/USB-C adapter, notifications off, Wi-Fi not required.
- [ ] Timer visible; two full timed run-throughs; hard stop at 4:50.
- [ ] Roles (3 people): **A** speaks the narrative, evaluation and close; **B** drives terminal and Neo4j and says nothing while typing; **C** takes technical questions and holds the fallback laptop/screenshots. Two people: A speaks, B drives and answers model questions.
- [ ] One person answers each question; nobody adds "and also".

## 9. Risks that lose hackathons

| Risk | How we avoid it |
|---|---|
| Live demo breaks | Everything pre-run; favourites; screenshots; terminal-only path rehearsed |
| "This is just a classifier" | Lead with the Kosovo problem; show the learned graph and real Kosovo threads; say the scope choice out loud |
| Overclaiming accuracy | Never quote Twitter F1 as Kosovo accuracy; lead with the unseen-family number |
| Quoting the synthetic 1.000 | Retrain first; a perfect score reads as a broken evaluation |
| Attribution slip ("Serbian bots") | Say "automated or coordinated accounts". Never name a state, group or person |
| Showing hidden inputs | "Context weighting" is one line; do not open it |
| Showing the old dashboard | It is not the product; keep it closed |
| Running over time | Timer, hard stop, close with the labelling plan, not a feature list |
| Jargon | Say "learned weights", "share of the decision", "bot types never seen"; keep SHAP and isotonic for Q&A |
| Vague impact | One user, one action: an analyst reviews a ranked, explained list |
# External snapshot check (21 November 2026)

The five-count profile network and the full LightGBM/GNN ensemble were compared on the same public account snapshots. Thresholds for this experiment were fixed using the 95th percentile of human scores on Botometer Feedback 2019 (higher order statistic, then the next representable value to handle ties). These are experimental thresholds, separate from the shipping thresholds. Duplicate accounts across sources were excluded.

| Held-out population | Accounts | Profile above threshold | Ensemble above threshold |
|---|---:|---:|---:|
| Political Bots 2019 | 62 bots | 0 | 0 |
| Verified 2019 | 1,985 human-labelled accounts | 0 | 0 |

Both systems incurred zero false alarms on the verified cohort. Neither recovered a political bot at this operating point. The ensemble saturated at 1 on enough validation humans that its conservative threshold exceeded 1. All test accounts had one observed post. The evidence-aware verdict abstains for all of them. This experiment establishes no graph advantage on sparse histories. Kosovo accuracy requires independent local labels.

