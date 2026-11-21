# Bot detector: evaluation report

- Dataset: cresci2017
- Model: LightGBM + relational GNN, isotonic-calibrated ensemble
- Question answered: is this account a bot? Output is a calibrated BOT probability.

## Random split (seen bot types, optimistic)

| Metric | Value |
|---|---|
| Precision | 0.995 |
| Recall | 0.996 |
| F1 | 0.996 |
| F1 95% CI | 0.993 to 0.998 |
| Accuracy | 0.992 |
| PR-AUC | 0.999 |
| ROC-AUC | 0.996 |
| Brier | 0.006 |
| ECE | 0.006 |
| Threshold | 0.333 |
| Accounts tested | 2039 |
| of which bots | 1822 |

Calibration (predicted vs. observed bot rate):

| Predicted range | Accounts | Mean predicted | Observed bot rate |
|---|---|---|---|
| 0.0 to 0.1 | 215 | 0.000 | 0.033 |
| 0.1 to 0.2 | 0 | n/a | n/a |
| 0.2 to 0.3 | 0 | n/a | n/a |
| 0.3 to 0.4 | 0 | n/a | n/a |
| 0.4 to 0.5 | 0 | n/a | n/a |
| 0.5 to 0.6 | 0 | n/a | n/a |
| 0.6 to 0.7 | 7 | 0.673 | 0.286 |
| 0.7 to 0.8 | 17 | 0.735 | 0.824 |
| 0.8 to 0.9 | 0 | n/a | n/a |
| 0.9 to 1.0 | 1800 | 1.000 | 0.999 |

## Leave-one-family-out (unseen bot type, the honest number)

| Held-out bot family | Precision | Recall | F1 | F1 95% CI | PR-AUC | ROC-AUC | ECE | Tested | Bots |
|---|---|---|---|---|---|---|---|---|---|
| fake_followers | 0.997 | 0.948 | 0.972 | 0.968 to 0.975 | 0.997 | 0.971 | 0.056 | 3419 | 3202 |
| social_spambots_1 | 1.000 | 0.965 | 0.982 | 0.976 to 0.988 | 0.999 | 0.997 | 0.012 | 1208 | 991 |
| social_spambots_2 | 0.999 | 0.996 | 0.997 | 0.996 to 0.998 | 1.000 | 0.997 | 0.036 | 3674 | 3457 |
| social_spambots_3 | 0.970 | 0.978 | 0.974 | 0.964 to 0.984 | 0.997 | 0.995 | 0.013 | 681 | 464 |
| traditional_spambots_1 | 0.986 | 1.000 | 0.993 | 0.989 to 0.997 | 1.000 | 1.000 | 0.007 | 1217 | 1000 |

Unseen-family F1 across 5 families: mean 0.984, worst 0.972, best 0.997. The mean is unweighted (every bot family counts the same).

## Notes

- Benchmark = Cresci-2017 (Twitter, human-labelled). These numbers are NOT Kosovo/YouTube accuracy; use `local-f1` with two annotators for that.
- Transductive graph: test accounts' features and edges are visible to the GNN, their labels are not.
- Leave-one-family-out = the bot type in the test set was never seen in training; it is the honest generalisation number.
- Relations with no edges in training data keep their initial weight (not learned): LINK, CO_THREAD.

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
| Account age (days, log) | account | 22.2% | 78% of accounts |
| Near-duplicate posts shared with other accounts | repetition | 21.3% | 77% of accounts |
| Lifetime post count (log) | account | 9.4% | 80% of accounts |
| Followers vs. following balance | account | 9.3% | 52% of accounts |
| Share of posts with @mentions | text | 7.2% | 48% of accounts |
| Share of posts that are replies | text | 6.0% | 50% of accounts |
| Accounts followed (log) | account | 3.7% | 40% of accounts |
| Digits | text | 2.3% | 71% of accounts |
| Share of posts with hashtags | text | 2.0% | 72% of accounts |
| Typical post length (log) | text | 2.0% | 60% of accounts |
| Spread of activity over the day | timing | 1.9% | 55% of accounts |
| Exclamation marks | text | 1.8% | 62% of accounts |
| Followers / subscribers (log) | account | 1.4% | 73% of accounts |
| Typical gap between posts (log s) | timing | 1.4% | 24% of accounts |
| Capital letters | text | 1.4% | 71% of accounts |
| Lifetime posts per day (log) | account | 1.3% | 73% of accounts |
| Share of posts < 60 s apart | timing | 1.2% | 50% of accounts |
| Irregularity of posting gaps | timing | 1.1% | 55% of accounts |
| Burstiness of posting | timing | 0.9% | 57% of accounts |
| Accounts it copy-pastes with (log) | network | 0.8% | 55% of accounts |
| Repeats its own posts | repetition | 0.6% | 37% of accounts |
| Size of the largest copy-paste group joined (log) | repetition | 0.4% | 64% of accounts |
| Context weighting | context | 0.2% | 98% of accounts |
| Posts observed (log) | timing | 0.1% | 32% of accounts |
| Accounts it posts in sync with (log) | network | 0.1% | 54% of accounts |
| Share of posts with links | repetition | 0.0% | 0% of accounts |
| Re-posts the same links | repetition | 0.0% | 0% of accounts |
| Cyrillic/Latin mixed inside a word | text | 0.0% | 0% of accounts |
| Accounts sharing its links (log) | network | 0.0% | 0% of accounts |
| Reply partners (log) | network | 0.0% | 97% of accounts |
| Accounts active with it in the same minutes (log) | network | 0.0% | 0% of accounts |
| Size of its coordinated group (log) | network | 0.0% | 13% of accounts |

## Learned relation weights (GNN gates)

| Relation | Weight | Share |
|---|---|---|
| COPY | 0.840 | 22.4% |
| SYNC | 0.829 | 22.1% |
| REPLY | 0.698 | 18.6% |
| LINK | 0.693 | 18.5% |
| CO_THREAD | 0.693 | 18.4% |
