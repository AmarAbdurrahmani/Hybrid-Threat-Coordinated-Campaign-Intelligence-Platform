# Bot detector: evaluation report

- Dataset: synthetic
- Model: LightGBM + relational GNN, isotonic-calibrated ensemble
- Question answered: is this account a bot? Output is a calibrated BOT probability.

## Random split (seen bot types, optimistic)

| Metric | Value |
|---|---|
| Precision | 1.000 |
| Recall | 1.000 |
| F1 | 1.000 |
| F1 95% CI | 1.000 to 1.000 |
| Accuracy | 1.000 |
| PR-AUC | 1.000 |
| ROC-AUC | 1.000 |
| Brier | 0.000 |
| ECE | 0.000 |
| Threshold | 0.500 |
| Accounts tested | 128 |
| of which bots | 68 |

Calibration (predicted vs. observed bot rate):

| Predicted range | Accounts | Mean predicted | Observed bot rate |
|---|---|---|---|
| 0.0 to 0.1 | 60 | 0.000 | 0.000 |
| 0.1 to 0.2 | 0 | n/a | n/a |
| 0.2 to 0.3 | 0 | n/a | n/a |
| 0.3 to 0.4 | 0 | n/a | n/a |
| 0.4 to 0.5 | 0 | n/a | n/a |
| 0.5 to 0.6 | 0 | n/a | n/a |
| 0.6 to 0.7 | 0 | n/a | n/a |
| 0.7 to 0.8 | 0 | n/a | n/a |
| 0.8 to 0.9 | 0 | n/a | n/a |
| 0.9 to 1.0 | 68 | 1.000 | 1.000 |

## Leave-one-family-out (unseen bot type, the honest number)

| Held-out bot family | Precision | Recall | F1 | F1 95% CI | PR-AUC | ROC-AUC | ECE | Tested | Bots |
|---|---|---|---|---|---|---|---|---|---|
| bots_copy | 1.000 | 1.000 | 1.000 | 1.000 to 1.000 | 1.000 | 1.000 | 0.001 | 180 | 120 |
| bots_fast | 1.000 | 0.408 | 0.580 | 0.474 to 0.663 | 1.000 | 1.000 | 0.343 | 180 | 120 |
| bots_link | 0.000 | 0.000 | 0.000 | 0.000 to 0.000 | 1.000 | 1.000 | 0.313 | 160 | 100 |

Unseen-family F1 across 3 families: mean 0.527, worst 0.000, best 1.000. The mean is unweighted (every bot family counts the same).

## Notes

- Benchmark = Cresci-2017 (Twitter, human-labelled). These numbers are NOT Kosovo/YouTube accuracy; use `local-f1` with two annotators for that.
- Transductive graph: test accounts' features and edges are visible to the GNN, their labels are not.
- Leave-one-family-out = the bot type in the test set was never seen in training; it is the honest generalisation number.
- Relations with no edges in training data keep their initial weight (not learned): REPLY, CO_THREAD.

## How to read this

- **Precision**: of the accounts we flagged as bots, the share that really are bots. Low precision means real people get accused.
- **Recall**: of the real bots, the share we caught. Low recall means bots slip through.
- **F1**: one number that balances precision and recall (their harmonic mean). It is only high when both are high. 1.0 is perfect.
- **F1 95% CI**: the range the F1 would plausibly fall in if we had drawn a different sample of accounts (bootstrap). A wide range means the test set is small and the number is soft.
- **PR-AUC / ROC-AUC**: how well the score ranks bots above humans across every possible threshold, not just the one we picked. 1.0 is perfect; ROC-AUC 0.5 is a coin flip.
- **Calibration, Brier, ECE**: whether the percentage means what it says. If we say "80% bot" for 100 accounts, about 80 should be bots. ECE is the average gap between the stated probability and what actually happened (0 is perfect, 0.05 means we are off by about 5 points on average). Brier is the mean squared error of the probability (lower is better).
- **Threshold**: the probability above which we say BOT. It is chosen on validation data only, never on the test data, so the test score is not flattered.
- **Random split vs leave-one-family-out**: in the random split the model has already seen examples of every bot type during training, so the score is optimistic. In leave-one-family-out a whole bot family is hidden during training and used only for testing. That number is usually lower, and it is the honest one: it estimates how we do against a kind of bot we have never seen, which is the real-world situation.
- **Domain caveat**: the benchmark data is Twitter. These numbers do not prove the same accuracy on YouTube or on Balkan-language accounts. That needs locally labelled accounts (two annotators, agreement measured with Cohen's kappa), scored with `evaluate_in_domain`.

## Learned indicator weights (share of total evidence, test-time mean |SHAP|)

| Indicator | Group | Weight | Pushes towards BOT |
|---|---|---|---|
| Typical gap between posts (log s) | timing | 28.4% | 53% of accounts |
| Near-duplicate posts shared with other accounts | repetition | 24.7% | 42% of accounts |
| Account age (days, log) | account | 18.2% | 59% of accounts |
| Spread of activity over the day | timing | 12.1% | 42% of accounts |
| Size of the largest copy-paste group joined (log) | repetition | 4.7% | 42% of accounts |
| Followers / subscribers (log) | account | 4.1% | 53% of accounts |
| Share of posts < 60 s apart | timing | 3.5% | 38% of accounts |
| Repeats its own posts | repetition | 1.5% | 34% of accounts |
| Accounts it copy-pastes with (log) | network | 1.4% | 42% of accounts |
| Typical post length (log) | text | 0.8% | 38% of accounts |
| Lifetime posts per day (log) | account | 0.3% | 55% of accounts |
| Followers vs. following balance | account | 0.2% | 78% of accounts |
| Lifetime post count (log) | account | 0.1% | 79% of accounts |
| Accounts followed (log) | account | 0.0% | 0% of accounts |
| Posts observed (log) | timing | 0.0% | 0% of accounts |
| Irregularity of posting gaps | timing | 0.0% | 90% of accounts |
| Burstiness of posting | timing | 0.0% | 0% of accounts |
| Share of posts with links | repetition | 0.0% | 0% of accounts |
| Re-posts the same links | repetition | 0.0% | 0% of accounts |
| Share of posts with @mentions | text | 0.0% | 0% of accounts |
| Share of posts with hashtags | text | 0.0% | 0% of accounts |
| Capital letters | text | 0.0% | 0% of accounts |
| Exclamation marks | text | 0.0% | 0% of accounts |
| Digits | text | 0.0% | 0% of accounts |
| Cyrillic/Latin mixed inside a word | text | 0.0% | 0% of accounts |
| Share of posts that are replies | text | 0.0% | 0% of accounts |
| Accounts it posts in sync with (log) | network | 0.0% | 0% of accounts |
| Accounts sharing its links (log) | network | 0.0% | 0% of accounts |
| Reply partners (log) | network | 0.0% | 0% of accounts |
| Accounts active with it in the same minutes (log) | network | 0.0% | 0% of accounts |
| Size of its coordinated group (log) | network | 0.0% | 42% of accounts |
| Context weighting | context | 0.0% | 0% of accounts |

## Learned relation weights (GNN gates)

| Relation | Weight | Share |
|---|---|---|
| SYNC | 0.728 | 20.6% |
| COPY | 0.712 | 20.2% |
| LINK | 0.697 | 19.8% |
| REPLY | 0.693 | 19.7% |
| CO_THREAD | 0.693 | 19.7% |
