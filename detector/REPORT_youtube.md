# Bot detector: evaluation report

- Dataset: cresci2017 (profile youtube, ≤3 posts/account)
- Model: LightGBM + relational GNN, isotonic-calibrated ensemble
- Question answered: is this account a bot? Output is a calibrated BOT probability.

## Random split (seen bot types, optimistic)

| Metric | Value |
|---|---|
| Precision | 0.941 |
| Recall | 0.969 |
| F1 | 0.955 |
| F1 95% CI | 0.948 to 0.962 |
| Accuracy | 0.918 |
| PR-AUC | 0.986 |
| ROC-AUC | 0.919 |
| Brier | 0.060 |
| ECE | 0.017 |
| Threshold | 0.528 |
| Accounts tested | 2039 |
| of which bots | 1822 |

Calibration (predicted vs. observed bot rate):

| Predicted range | Accounts | Mean predicted | Observed bot rate |
|---|---|---|---|
| 0.0 to 0.1 | 43 | 0.000 | 0.233 |
| 0.1 to 0.2 | 0 | n/a | n/a |
| 0.2 to 0.3 | 5 | 0.214 | 0.000 |
| 0.3 to 0.4 | 99 | 0.341 | 0.384 |
| 0.4 to 0.5 | 14 | 0.411 | 0.500 |
| 0.5 to 0.6 | 1 | 0.522 | 1.000 |
| 0.6 to 0.7 | 55 | 0.655 | 0.491 |
| 0.7 to 0.8 | 79 | 0.737 | 0.684 |
| 0.8 to 0.9 | 287 | 0.877 | 0.875 |
| 0.9 to 1.0 | 1456 | 0.988 | 0.985 |

## Leave-one-family-out (unseen bot type, the honest number)

| Held-out bot family | Precision | Recall | F1 | F1 95% CI | PR-AUC | ROC-AUC | ECE | Tested | Bots |
|---|---|---|---|---|---|---|---|---|---|
| fake_followers | 0.974 | 0.912 | 0.942 | 0.935 to 0.948 | 0.987 | 0.872 | 0.119 | 3419 | 3202 |
| social_spambots_1 | 0.922 | 0.985 | 0.952 | 0.942 to 0.962 | 0.967 | 0.908 | 0.042 | 1208 | 991 |
| social_spambots_2 | 0.980 | 0.840 | 0.905 | 0.898 to 0.912 | 0.985 | 0.852 | 0.125 | 3674 | 3457 |
| social_spambots_3 | 0.837 | 0.955 | 0.892 | 0.874 to 0.912 | 0.925 | 0.878 | 0.115 | 681 | 464 |
| traditional_spambots_1 | 0.906 | 0.960 | 0.932 | 0.920 to 0.944 | 0.970 | 0.903 | 0.053 | 1217 | 1000 |

Unseen-family F1 across 5 families: mean 0.925, worst 0.892, best 0.952. The mean is unweighted (every bot family counts the same).

## Notes

- Benchmark = Cresci-2017 (Twitter, human-labelled). These numbers are NOT Kosovo/YouTube accuracy; use `local-f1` with two annotators for that.
- Transductive graph: test accounts' features and edges are visible to the GNN, their labels are not.
- Leave-one-family-out = the bot type in the test set was never seen in training; it is the honest generalisation number.
- Relations with no edges in training data keep their initial weight (not learned): LINK, REPLY, CO_THREAD.

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
| Near-duplicate posts shared with other accounts | repetition | 30.9% | 35% of accounts |
| Share of posts with @mentions | text | 17.5% | 64% of accounts |
| Typical gap between posts (log s) | timing | 11.0% | 58% of accounts |
| Capital letters | text | 7.6% | 53% of accounts |
| Size of the largest copy-paste group joined (log) | repetition | 6.1% | 35% of accounts |
| Typical post length (log) | text | 5.2% | 50% of accounts |
| Posts observed (log) | timing | 4.5% | 64% of accounts |
| Size of its coordinated group (log) | network | 4.4% | 26% of accounts |
| Digits | text | 2.8% | 68% of accounts |
| Share of posts with hashtags | text | 2.6% | 84% of accounts |
| Irregularity of posting gaps | timing | 1.6% | 57% of accounts |
| Share of posts that are replies | text | 1.6% | 38% of accounts |
| Exclamation marks | text | 1.4% | 27% of accounts |
| Accounts it copy-pastes with (log) | network | 1.2% | 35% of accounts |
| Share of posts < 60 s apart | timing | 0.7% | 43% of accounts |
| Burstiness of posting | timing | 0.5% | 54% of accounts |
| Spread of activity over the day | timing | 0.4% | 53% of accounts |
| Repeats its own posts | repetition | 0.0% | 0% of accounts |
| Share of posts with links | repetition | 0.0% | 0% of accounts |
| Re-posts the same links | repetition | 0.0% | 0% of accounts |
| Cyrillic/Latin mixed inside a word | text | 0.0% | 0% of accounts |
| Accounts it posts in sync with (log) | network | 0.0% | 0% of accounts |
| Accounts sharing its links (log) | network | 0.0% | 0% of accounts |
| Reply partners (log) | network | 0.0% | 0% of accounts |
| Accounts active with it in the same minutes (log) | network | 0.0% | 0% of accounts |
| Context weighting | context | 0.0% | 0% of accounts |

## Learned relation weights (GNN gates)

| Relation | Weight | Share |
|---|---|---|
| COPY | 0.841 | 54.0% |
| SYNC | 0.716 | 46.0% |
| LINK | 0.000 | 0.0% |
| REPLY | 0.000 | 0.0% |
| CO_THREAD | 0.000 | 0.0% |
