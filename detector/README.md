# HTCCIP bot detector

**One question: is this account a bot?**

The output is a calibrated **BOT probability** plus the **percentage contribution of every indicator**. There is no UI.
Intent and narrative signals are model inputs only. They shift the learned weights and appear in the output as a single
line, "Context weighting".

```json
{"account_id": "yt_3k9…", "bot_probability": 91.4, "verdict": "BOT", "threshold": 47.0,
 "indicators": [{"indicator": "Near-duplicate posts shared with other accounts", "percent": 28.6, "direction": "+"},
                {"indicator": "Accounts it posts in sync with (log)", "percent": 17.9, "direction": "+"},
                {"indicator": "Account age (days, log)", "percent": 12.2, "direction": "+"}, …]}
```

## How it works

1. **The graph (a knowledge graph you can open in Neo4j).**
   - Nodes are accounts.
   - Edges are typed and weighted:
     - `COPY`: the two accounts shared a near-duplicate post;
     - `SYNC`: they posted the same text within 10 minutes;
     - `LINK`: they posted the same URL;
     - `REPLY`: one replied to the other;
     - `CO_THREAD`: they were active in the same thread within 2 minutes.
   - Each account carries about 30 raw indicators: account age and followers, posting rhythm and burstiness, repetition,
     text style, script mixing, network degree and group size, and context.
2. **Learned weights, no hand-set rules.**
   - **LightGBM** learns how much each indicator matters, and in which combinations.
   - A **relational graph network** (`gnn.py`) learns one weight per relation type, i.e. how strongly a `SYNC`
     neighbour's evidence counts compared with a `LINK` neighbour's. Those learned relation weights are exported to
     Neo4j as edge weights.
3. **Calibration.** The two models are averaged, then isotonic regression on a held-out validation split turns the score
   into a probability. A score of 80% then means about 80% of such accounts were bots in validation.
4. **Explanation.** Each account's percentages are LightGBM's exact per-prediction contributions (TreeSHAP), normalised
   so that |percentages| sum to 100.

## Data

| Data | Use |
|---|---|
| **Cresci-2017** (Indiana University Bot Repository, human-labelled Twitter accounts: genuine accounts plus several bot families) | Training and benchmark |
| Real **YouTube comments** on Kosovo/Serbia videos, collected by the backend (`backend/`), authors pseudonymised | Scoring, plus a blind labelling sheet for the local F1 |

## Commands

```bash
pip install -r detector/requirements.txt
python -m detector.datasets cresci detector/data/raw/cresci-2017.csv.zip   # → detector/data/processed/cresci2017_*
python -m detector train                      # trains, evaluates, writes detector/model/ and detector/REPORT.md
python -m detector.datasets backend backend/htccip.db
python -m detector score --data youtube_local # BOT % per account, Neo4j export, blind label_sheet.csv
python -m detector local-f1 --labels label_sheet.csv --scores scores.csv   # Kosovo F1 + annotator agreement
python -m pytest -q detector/tests
```

API (backend, analyst key): `POST /api/bot/score` with `{"thread_ids": [...]}`, or with `{"accounts": [...], "posts": [...]}`.

**Neo4j:** copy `…/neo4j/*.csv` into the database's `import/` folder and run `import.cypher` in Neo4j Browser.

## Reading the numbers honestly

- **`REPORT.md`** gives precision, recall and F1 with 95% bootstrap intervals on a random split, plus
  **leave-one-bot-family-out** results, where the test bot type was never seen in training. The second is the honest
  generalisation number.
- **The benchmark is Twitter, not Kosovo YouTube.** Until two people label the blind sheet and `local-f1` reports
  agreement (Cohen's kappa) and F1, treat YouTube scores as ranked leads for a human analyst, not verdicts.
- **No attribution:** the detector says *automated/coordinated or not*, never *who* is behind an account.
