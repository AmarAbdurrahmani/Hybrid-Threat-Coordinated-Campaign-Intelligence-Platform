# HTCCIP bot detector — interface spec

**Scope (fixed): one question only: is this account a bot?** Output = BOT probability (calibrated %) plus the percentage
contribution of every indicator. Intent / motive / narrative signals are model *inputs* that shift the weights; they are
never output or shown by name (they are folded into one line, "Context weighting"). No UI.

All weights are learned from labelled data (LightGBM + a relational graph network), never hand-set.

## Data schema (pandas DataFrames)

`accounts`: `account_id` (str), `created_ts` (float epoch s, NaN if unknown), `followers`, `following`, `posts_total`
(float, NaN if unknown), `label` (1 = bot, 0 = human, NaN = unlabelled), `family` (str: e.g. `genuine`,
`social_spambots_1`, `youtube`), `source` (str: `cresci-2017`, `youtube`).

`posts`: `post_id` (str), `account_id` (str), `ts` (float epoch s), `text` (str), `thread_id` (str, '' if none),
`parent_id` (str, '' if none — id of the post replied to), `urls` (str, space-separated expanded URLs, '' if none).

Processed files: `detector/data/processed/<name>_accounts.csv.gz`, `<name>_posts.csv.gz` (gitignored).

## Modules and owners

| File | Owner | Contract |
|---|---|---|
| `detector/datasets.py` | data worker | `load_cresci2017(zip_path, max_posts_per_account=200, seed=0) -> (accounts, posts)`; `load_backend_db(db_path, include_simulated=False) -> (accounts, posts)`; `save(name, accounts, posts)`, `load(name) -> (accounts, posts)` |
| `detector/features.py` | lead | `account_features(accounts, posts) -> DataFrame` (index = account_id); `FEATURES: dict[name -> (label, group)]`, groups: `account`, `timing`, `repetition`, `text`, `network`, `context` (context = intent/narrative, hidden in output) |
| `detector/graph.py` | lead | `build_graph(accounts, posts) -> Graph` with `relations: dict[name -> scipy.sparse.csr_matrix (n×n, weighted)]`, `ids: list[str]`; `network_features(graph) -> DataFrame` |
| `detector/model.py`, `detector/gnn.py`, `detector/cli.py` | lead | train / score / export |
| `detector/evaluate.py` | evaluation worker | `metrics(y, p, threshold=0.5) -> dict` (precision, recall, f1, accuracy, pr_auc, roc_auc, brier, ece, support); `bootstrap_ci(y, p, fn, n=1000, seed=0) -> (lo, hi)`; `best_threshold(y, p, metric='f1') -> float`; `calibration_table(y, p, bins=10) -> list[dict]`; `split_stratified(accounts, test=0.2, val=0.1, seed=0) -> (train_ids, val_ids, test_ids)`; `leave_one_family_out(accounts) -> iterator[(family, train_ids, test_ids)]`; `write_report(results: dict, path)` (Markdown) |
| `detector/weights.py` | weights worker | `percent_contributions(contrib: ndarray (n×(f+1)), feature_names) -> DataFrame` per-row signed % (|share| sums to 100, bias column dropped, `context` group folded into one "Context weighting" row); `global_importance(contrib, feature_names) -> DataFrame`; `export_neo4j(out_dir, scored, graph, contrib_pct, global_imp, relation_weights)` → `nodes_*.csv`, `rels_*.csv`, `import.cypher` |

## Scoring output (per account)

```json
{"account_id": "...", "bot_probability": 87.4, "verdict": "BOT", "threshold": 50.0,
 "indicators": [{"indicator": "Near-duplicate posts shared with other accounts", "percent": 31.2, "direction": "+"}, ...]}
```
`verdict` is `BOT` / `NOT BOT`. `percent` values are shares of the total evidence (sum of |percent| = 100).
