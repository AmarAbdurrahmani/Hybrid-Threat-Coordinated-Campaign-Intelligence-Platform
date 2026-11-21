"""Evaluation for the bot detector: metrics, confidence intervals, honest splits, report.

Conventions: y is 1 = bot, 0 = human; p is the BOT probability in [0, 1];
an account is predicted BOT when p >= threshold.
Pure numpy / pandas / scikit-learn, no torch.
"""
from __future__ import annotations

import math
from typing import Callable, Iterator

import numpy as np
import pandas as pd
from sklearn.metrics import average_precision_score, cohen_kappa_score, roc_auc_score

__all__ = [
    "metrics", "bootstrap_ci", "best_threshold", "calibration_table",
    "split_stratified", "leave_one_family_out", "write_report", "evaluate_in_domain",
]


# ----------------------------------------------------------------------------- helpers

def _clean(y, p):
    """Return (y int array, p float array), dropping rows where either is NaN."""
    y = np.asarray(y, dtype=float).ravel()
    p = np.asarray(p, dtype=float).ravel()
    if y.shape != p.shape:
        raise ValueError(f"y and p differ in length: {y.shape[0]} vs {p.shape[0]}")
    keep = ~(np.isnan(y) | np.isnan(p))
    y, p = y[keep], p[keep]
    if y.size and not np.isin(y, (0.0, 1.0)).all():
        raise ValueError("y must contain only 0 and 1")
    if p.size and (p.min() < 0.0 or p.max() > 1.0):
        raise ValueError("p must be a probability in [0, 1] (divide percentages by 100)")
    return y.astype(int), p


def _confusion(y, pred):
    tp = int(np.sum((pred == 1) & (y == 1)))
    fp = int(np.sum((pred == 1) & (y == 0)))
    fn = int(np.sum((pred == 0) & (y == 1)))
    tn = int(np.sum((pred == 0) & (y == 0)))
    return tp, fp, fn, tn


def _div(a, b):
    return float(a) / float(b) if b else 0.0


def _bin_index(p, bins):
    # equal-width bins over [0, 1]; p == 1.0 belongs to the last bin
    return np.minimum((p * bins).astype(int), bins - 1)


def _ece(y, p, bins=10):
    if y.size == 0:
        return None
    idx = _bin_index(p, bins)
    n = np.bincount(idx, minlength=bins).astype(float)
    sum_p = np.bincount(idx, weights=p, minlength=bins)
    sum_y = np.bincount(idx, weights=y, minlength=bins)
    nz = n > 0
    gap = np.abs(sum_p[nz] / n[nz] - sum_y[nz] / n[nz])
    return float(np.sum(n[nz] / y.size * gap))


# ----------------------------------------------------------------------------- metrics

def metrics(y, p, threshold=0.5) -> dict:
    """Classification + ranking + calibration metrics for BOT probabilities.

    Undefined values are None (never an exception): roc_auc needs both classes,
    pr_auc needs at least one bot. precision/recall/f1 are 0.0 when their
    denominator is empty.
    """
    y, p = _clean(y, p)
    n, pos = int(y.size), int(y.sum())
    out = {
        "precision": None, "recall": None, "f1": None, "accuracy": None,
        "pr_auc": None, "roc_auc": None, "brier": None, "ece": None,
        "support": {"n": n, "positives": pos, "negatives": n - pos},
        "confusion": {"tp": 0, "fp": 0, "fn": 0, "tn": 0},
        "threshold": float(threshold),
    }
    if n == 0:
        return out
    tp, fp, fn, tn = _confusion(y, (p >= threshold).astype(int))
    out.update(
        precision=_div(tp, tp + fp),
        recall=_div(tp, tp + fn),
        f1=_div(2 * tp, 2 * tp + fp + fn),
        accuracy=_div(tp + tn, n),
        brier=float(np.mean((p - y) ** 2)),
        ece=_ece(y, p, 10),
        confusion={"tp": tp, "fp": fp, "fn": fn, "tn": tn},
    )
    if pos > 0:
        out["pr_auc"] = float(average_precision_score(y, p))
    if 0 < pos < n:
        out["roc_auc"] = float(roc_auc_score(y, p))
    return out


def bootstrap_ci(y, p, fn: Callable, n=1000, seed=0):
    """Percentile 95% CI of fn(y, p) by resampling rows with replacement.

    Resamples where fn is undefined (returns None/NaN or raises ValueError, e.g.
    AUC on a single-class resample) are skipped. Returns (None, None) if no
    resample is defined.
    """
    y, p = _clean(y, p)
    if y.size == 0:
        return (None, None)
    rng = np.random.default_rng(seed)
    vals = []
    for _ in range(int(n)):
        idx = rng.integers(0, y.size, y.size)
        try:
            v = fn(y[idx], p[idx])
        except ValueError:
            continue
        if v is None:
            continue
        v = float(v)
        if math.isnan(v):
            continue
        vals.append(v)
    if not vals:
        return (None, None)
    lo, hi = np.percentile(vals, [2.5, 97.5])
    return (float(lo), float(hi))


def best_threshold(y, p, metric="f1") -> float:
    """Threshold maximising `metric` on the data given. Pass VALIDATION data only;
    choosing it on the test set inflates the reported score.

    metric: 'f1', 'accuracy' or 'youden' (recall + specificity - 1).
    The returned cut sits midway between the two neighbouring scores so it does
    not hinge on one exact probability value. Falls back to 0.5 when undefined.
    """
    if metric not in ("f1", "accuracy", "youden"):
        raise ValueError(f"unknown metric: {metric!r}")
    y, p = _clean(y, p)
    n, pos = int(y.size), int(y.sum())
    neg = n - pos
    if n == 0 or (metric in ("f1", "youden") and (pos == 0 or neg == 0)):
        return 0.5
    order = np.argsort(-p, kind="mergesort")
    ps, ys = p[order], y[order]
    last = np.r_[np.nonzero(np.diff(ps))[0], n - 1]  # last row of each distinct score
    tp = np.cumsum(ys)[last].astype(float)
    fp = (last + 1) - tp
    if metric == "f1":
        score = 2 * tp / (tp + fp + pos)
    elif metric == "accuracy":
        score = (tp + (neg - fp)) / n
    else:
        score = tp / pos - fp / neg
    cuts = ps[last]  # predicting BOT for p >= cuts[i] gives score[i]
    best = np.nonzero(score >= score.max() - 1e-12)[0]
    i = int(best[np.argmin(np.abs(cuts[best] - 0.5))])  # ties: closest to 0.5
    below = cuts[i + 1] if i + 1 < cuts.size else cuts[i]
    return float((cuts[i] + below) / 2.0)


def calibration_table(y, p, bins=10) -> list:
    """Reliability table over equal-width bins. Empty bins keep n=0 and None means."""
    y, p = _clean(y, p)
    idx = _bin_index(p, bins) if y.size else np.zeros(0, dtype=int)
    rows = []
    for b in range(bins):
        m = idx == b
        k = int(m.sum())
        rows.append({
            "bin_lo": b / bins,
            "bin_hi": (b + 1) / bins,
            "n": k,
            "mean_pred": float(p[m].mean()) if k else None,
            "frac_pos": float(y[m].mean()) if k else None,
        })
    return rows


# ----------------------------------------------------------------------------- splits

def _labelled(accounts: pd.DataFrame) -> pd.DataFrame:
    df = accounts.loc[accounts["label"].notna(), ["account_id", "label", "family"]].copy()
    df["account_id"] = df["account_id"].astype(str)
    df["family"] = df["family"].fillna("unknown").astype(str)
    if df["account_id"].duplicated().any():
        raise ValueError("duplicate account_id in accounts: the same account could land in train and test")
    return df


def _shuffled_groups(df, by, rng):
    """Yield (key, shuffled ids) per group, independent of the input row order."""
    for key in sorted(df[by].unique()):
        ids = np.sort(df.loc[df[by] == key, "account_id"].to_numpy())
        yield key, ids[rng.permutation(ids.size)]


def split_stratified(accounts, test=0.2, val=0.1, seed=0):
    """Random split stratified by `family` (so also by label). Labelled rows only.

    Returns (train_ids, val_ids, test_ids) as disjoint lists of account_id strings.
    """
    if test < 0 or val < 0 or test + val >= 1:
        raise ValueError("need test >= 0, val >= 0 and test + val < 1")
    df = _labelled(accounts)
    rng = np.random.default_rng(seed)
    train, va, te = [], [], []
    for _, ids in _shuffled_groups(df, "family", rng):
        n = ids.size
        n_te, n_va = int(round(n * test)), int(round(n * val))
        while n_te + n_va >= n and n_te + n_va > 0:  # tiny family: keep one for training
            if n_va >= n_te:
                n_va -= 1
            else:
                n_te -= 1
        te += ids[:n_te].tolist()
        va += ids[n_te:n_te + n_va].tolist()
        train += ids[n_te + n_va:].tolist()
    return train, va, te


def leave_one_family_out(accounts, human_test=0.2, seed=0) -> Iterator[tuple]:
    """Yield (family, train_ids, test_ids) for each bot family.

    Test = the whole held-out bot family + a stratified `human_test` share of
    human accounts; train = everything else. The model never sees the held-out
    bot type, so this measures generalisation to an unseen kind of bot. The
    human hold-out is the same in every fold, so folds are comparable.
    """
    df = _labelled(accounts)
    bot_families = sorted(df.loc[df["label"] == 1, "family"].unique())
    humans = df[(df["label"] == 0) & ~df["family"].isin(bot_families)]
    rng = np.random.default_rng(seed)
    human_test_ids = []
    for _, ids in _shuffled_groups(humans, "family", rng):
        k = int(round(ids.size * human_test))
        if ids.size >= 2:
            k = min(max(k, 1), ids.size - 1)
        human_test_ids += ids[:k].tolist()
    held_humans = set(human_test_ids)
    for fam in bot_families:
        in_fam = (df["family"] == fam).to_numpy()
        ids = df["account_id"].to_numpy()
        test_ids = ids[in_fam].tolist() + human_test_ids
        train_ids = [a for a in ids[~in_fam].tolist() if a not in held_humans]
        yield fam, train_ids, test_ids


# ----------------------------------------------------------------------------- report

_HOW_TO_READ = """## How to read this

- **Precision**: of the accounts we flagged as bots, the share that really are bots. Low precision means real people get accused.
- **Recall**: of the real bots, the share we caught. Low recall means bots slip through.
- **F1**: one number that balances precision and recall (their harmonic mean). It is only high when both are high. 1.0 is perfect.
- **F1 95% CI**: the range the F1 would plausibly fall in if we had drawn a different sample of accounts (bootstrap). A wide range means the test set is small and the number is soft.
- **PR-AUC / ROC-AUC**: how well the score ranks bots above humans across every possible threshold, not just the one we picked. 1.0 is perfect; ROC-AUC 0.5 is a coin flip.
- **Calibration, Brier, ECE**: whether the percentage means what it says. If we say "80% bot" for 100 accounts, about 80 should be bots. ECE is the average gap between the stated probability and what actually happened (0 is perfect, 0.05 means we are off by about 5 points on average). Brier is the mean squared error of the probability (lower is better).
- **Threshold**: the probability above which we say BOT. It is chosen on validation data only, never on the test data, so the test score is not flattered.
- **Random split vs leave-one-family-out**: in the random split the model has already seen examples of every bot type during training, so the score is optimistic. In leave-one-family-out a whole bot family is hidden during training and used only for testing. That number is usually lower, and it is the honest one: it estimates how we do against a kind of bot we have never seen, which is the real-world situation.
- **Domain caveat**: the benchmark data is Twitter. These numbers do not prove the same accuracy on YouTube or on Balkan-language accounts. That needs locally labelled accounts (two annotators, agreement measured with Cohen's kappa), scored with `evaluate_in_domain`.
"""

_COLS = [("precision", "Precision"), ("recall", "Recall"), ("f1", "F1"), ("accuracy", "Accuracy"),
         ("pr_auc", "PR-AUC"), ("roc_auc", "ROC-AUC"), ("brier", "Brier"), ("ece", "ECE")]


def _fmt(v, nd=3):
    if v is None:
        return "n/a"
    if isinstance(v, (bool, np.bool_)):
        return str(bool(v))
    if isinstance(v, (int, np.integer)):
        return str(int(v))
    try:
        v = float(v)
    except (TypeError, ValueError):
        return str(v)
    return "n/a" if math.isnan(v) else f"{v:.{nd}f}"


def _fmt_ci(ci):
    try:
        lo, hi = ci
    except (TypeError, ValueError):
        return "n/a"
    if lo is None or hi is None:
        return "n/a"
    return f"{_fmt(lo)} to {_fmt(hi)}"


def _support(m):
    s = m.get("support")
    if not isinstance(s, dict):
        return "n/a", "n/a"
    return _fmt(s.get("n")), _fmt(s.get("positives"))


def _is_metrics(d):
    return isinstance(d, dict) and any(k in d for k, _ in _COLS)


def _metric_block(m):
    n, pos = _support(m)
    lines = ["| Metric | Value |", "|---|---|"]
    for key, label in _COLS:
        if key in m:
            lines.append(f"| {label} | {_fmt(m[key])} |")
        if key == "f1" and "f1_ci" in m:
            lines.append(f"| F1 95% CI | {_fmt_ci(m['f1_ci'])} |")
    if "threshold" in m:
        lines.append(f"| Threshold | {_fmt(m['threshold'])} |")
    lines.append(f"| Accounts tested | {n} |")
    lines.append(f"| of which bots | {pos} |")
    return lines


def _calibration_block(table):
    lines = ["", "Calibration (predicted vs. observed bot rate):", "",
             "| Predicted range | Accounts | Mean predicted | Observed bot rate |", "|---|---|---|---|"]
    for r in table:
        if not isinstance(r, dict):
            continue
        rng = f"{_fmt(r.get('bin_lo'), 1)} to {_fmt(r.get('bin_hi'), 1)}"
        lines.append(f"| {rng} | {_fmt(r.get('n'))} | {_fmt(r.get('mean_pred'))} | {_fmt(r.get('frac_pos'))} |")
    return lines


def _family_table(folds):
    lines = ["| Held-out bot family | Precision | Recall | F1 | F1 95% CI | PR-AUC | ROC-AUC | ECE | Tested | Bots |",
             "|---|---|---|---|---|---|---|---|---|---|"]
    f1s = []
    for fam, m in folds.items():
        if not isinstance(m, dict):
            continue
        n, pos = _support(m)
        lines.append(
            f"| {fam} | {_fmt(m.get('precision'))} | {_fmt(m.get('recall'))} | {_fmt(m.get('f1'))} | "
            f"{_fmt_ci(m.get('f1_ci'))} | {_fmt(m.get('pr_auc'))} | {_fmt(m.get('roc_auc'))} | "
            f"{_fmt(m.get('ece'))} | {n} | {pos} |")
        if isinstance(m.get("f1"), (int, float)) and not math.isnan(m["f1"]):
            f1s.append(float(m["f1"]))
    if f1s:
        lines += ["", f"Unseen-family F1 across {len(f1s)} families: mean {_fmt(np.mean(f1s))}, "
                      f"worst {_fmt(min(f1s))}, best {_fmt(max(f1s))}. "
                      "The mean is unweighted (every bot family counts the same)."]
    return lines


_TITLES = {
    "random_split": "Random split (seen bot types, optimistic)",
    "leave_one_family_out": "Leave-one-family-out (unseen bot type, the honest number)",
    "in_domain": "In-domain (locally labelled accounts)",
}


def write_report(results: dict, path) -> str:
    """Write a Markdown evaluation report. Tolerant of missing keys. Returns the text."""
    results = results if isinstance(results, dict) else {}
    lines = ["# Bot detector: evaluation report", ""]
    if results.get("dataset") is not None:
        lines.append(f"- Dataset: {results['dataset']}")
    if results.get("model") is not None:
        lines.append(f"- Model: {results['model']}")
    lines += ["- Question answered: is this account a bot? Output is a calibrated BOT probability.", ""]

    protocols = results.get("protocols")
    protocols = protocols if isinstance(protocols, dict) else {}
    if not protocols:
        lines += ["No evaluation results were provided.", ""]
    for name, body in protocols.items():
        lines += [f"## {_TITLES.get(name, str(name).replace('_', ' ').capitalize())}", ""]
        if _is_metrics(body):
            lines += _metric_block(body)
            if isinstance(body.get("calibration"), (list, tuple)) and body["calibration"]:
                lines += _calibration_block(body["calibration"])
        elif isinstance(body, dict) and body:
            lines += _family_table(body)
        else:
            lines.append("No results.")
        lines.append("")

    notes = results.get("notes")
    if isinstance(notes, str):
        notes = [notes]
    if notes:
        lines += ["## Notes", ""] + [f"- {n}" for n in notes] + [""]

    lines.append(_HOW_TO_READ)
    text = "\n".join(lines)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)
    return text


# ----------------------------------------------------------------------------- in-domain

def _f1_at(threshold):
    def fn(y, p):
        tp, fp, fn_, _ = _confusion(y, (p >= threshold).astype(int))
        return _div(2 * tp, 2 * tp + fp + fn_)
    return fn


def evaluate_in_domain(labels_csv, scores_csv, threshold=0.5, n_boot=1000, seed=0) -> dict:
    """Score the detector against locally hand-labelled accounts (e.g. Kosovo / YouTube).

    labels_csv: account_id, annotator_a, annotator_b (0/1, blanks allowed).
    scores_csv: account_id, bot_probability (0-100).
    Ground truth = rows where both annotators labelled AND agree; disagreements
    and single-annotator rows are counted but not scored.
    """
    labels = pd.read_csv(labels_csv, dtype={"account_id": str})
    scores = pd.read_csv(scores_csv, dtype={"account_id": str})
    for col in ("account_id", "annotator_a", "annotator_b"):
        if col not in labels.columns:
            raise ValueError(f"labels CSV is missing column {col!r}")
    for col in ("account_id", "bot_probability"):
        if col not in scores.columns:
            raise ValueError(f"scores CSV is missing column {col!r}")
    labels = labels.drop_duplicates("account_id", keep="last")
    scores = scores.drop_duplicates("account_id", keep="last")

    a = pd.to_numeric(labels["annotator_a"], errors="coerce")
    b = pd.to_numeric(labels["annotator_b"], errors="coerce")
    for name, col in (("annotator_a", a), ("annotator_b", b)):
        if not col.dropna().isin([0, 1]).all():
            raise ValueError(f"{name} must be 0, 1 or blank")
    both = a.notna() & b.notna()
    agree = both & (a == b)

    kappa = None
    if both.sum() > 0:
        k = cohen_kappa_score(a[both].astype(int), b[both].astype(int), labels=[0, 1])
        kappa = None if math.isnan(k) else float(k)  # undefined when both used one class only

    truth = pd.DataFrame({"account_id": labels.loc[agree, "account_id"], "y": a[agree].astype(int)})
    prob = pd.to_numeric(scores["bot_probability"], errors="coerce") / 100.0
    merged = truth.merge(pd.DataFrame({"account_id": scores["account_id"], "p": prob}).dropna(),
                         on="account_id", how="left")
    scored = merged.dropna(subset=["p"])
    y, p = scored["y"].to_numpy(), scored["p"].to_numpy()

    m = metrics(y, p, threshold)
    return {
        "kappa": kappa,
        "n_labelled_rows": int(len(labels)),
        "n_both_labelled": int(both.sum()),
        "n_agreed": int(agree.sum()),
        "n_disagreements": int((both & (a != b)).sum()),
        "n_single_annotator": int((a.notna() ^ b.notna()).sum()),
        "n_agreed_without_score": int(len(merged) - len(scored)),
        "n_evaluated": int(len(scored)),
        "metrics": m,
        "f1_ci": list(bootstrap_ci(y, p, _f1_at(threshold), n=n_boot, seed=seed)),
    }
