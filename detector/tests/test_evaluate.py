import json

import numpy as np
import pandas as pd
import pytest
from sklearn.metrics import f1_score

from detector import evaluate as ev


def _accounts(seed=0):
    rng = np.random.default_rng(seed)
    sizes = {"genuine": 200, "celebrity": 50, "social_spambots_1": 80,
             "fake_followers": 60, "traditional_spambots_1": 33}
    rows = []
    for fam, n in sizes.items():
        label = 0.0 if fam in ("genuine", "celebrity") else 1.0
        rows += [{"account_id": f"{fam}_{i}", "label": label, "family": fam} for i in range(n)]
    rows += [{"account_id": f"yt_{i}", "label": np.nan, "family": "youtube"} for i in range(40)]
    df = pd.DataFrame(rows)
    return df.iloc[rng.permutation(len(df))].reset_index(drop=True)


# ----------------------------------------------------------------------------- metrics

def test_perfect_predictor():
    y = np.array([0, 0, 1, 1, 0, 1])
    m = ev.metrics(y, y.astype(float))
    for k in ("precision", "recall", "f1", "accuracy", "pr_auc", "roc_auc"):
        assert m[k] == pytest.approx(1.0)
    assert m["brier"] == pytest.approx(0.0)
    assert m["ece"] == pytest.approx(0.0)
    assert m["support"]["n"] == 6 and m["support"]["positives"] == 3
    assert m["threshold"] == 0.5


def test_random_predictor():
    rng = np.random.default_rng(1)
    y = rng.integers(0, 2, 20000)
    p = rng.random(20000)
    m = ev.metrics(y, p)
    assert abs(m["roc_auc"] - 0.5) < 0.02
    assert abs(m["pr_auc"] - y.mean()) < 0.02
    assert abs(m["f1"] - 0.5) < 0.02
    assert abs(m["accuracy"] - 0.5) < 0.02
    assert abs(m["brier"] - 1 / 3) < 0.02


def test_metrics_match_sklearn_and_hand_count():
    y = np.array([1, 1, 1, 0, 0, 0, 0, 1])
    p = np.array([0.9, 0.8, 0.2, 0.7, 0.1, 0.3, 0.5, 0.5])  # p == threshold counts as BOT
    m = ev.metrics(y, p, threshold=0.5)
    assert m["confusion"] == {"tp": 3, "fp": 2, "fn": 1, "tn": 2}
    assert m["precision"] == pytest.approx(3 / 5)
    assert m["recall"] == pytest.approx(3 / 4)
    assert m["f1"] == pytest.approx(f1_score(y, (p >= 0.5).astype(int)))
    json.dumps(m)  # plain python types only


def test_single_class_does_not_crash():
    p = np.array([0.1, 0.4, 0.6, 0.9])
    all_human = ev.metrics(np.zeros(4), p)
    assert all_human["roc_auc"] is None and all_human["pr_auc"] is None
    assert all_human["f1"] == 0.0 and all_human["accuracy"] == pytest.approx(0.5)
    all_bot = ev.metrics(np.ones(4), p)
    assert all_bot["roc_auc"] is None and all_bot["pr_auc"] == pytest.approx(1.0)
    assert all_bot["recall"] == pytest.approx(0.5)
    empty = ev.metrics([], [])
    assert empty["f1"] is None and empty["support"]["n"] == 0
    assert ev.best_threshold(np.zeros(4), p) == 0.5
    assert ev.bootstrap_ci(np.zeros(4), p, lambda a, b: ev.metrics(a, b)["roc_auc"], n=50) == (None, None)


def test_rejects_percentages():
    with pytest.raises(ValueError):
        ev.metrics([0, 1], [10.0, 90.0])


# ----------------------------------------------------------------------------- bootstrap / threshold

def _noisy(n=600, seed=2):
    rng = np.random.default_rng(seed)
    y = rng.integers(0, 2, n)
    p = np.clip(0.5 + (y - 0.5) * 0.4 + rng.normal(0, 0.25, n), 0, 1)
    return y, p


def test_bootstrap_ci_contains_point_estimate_and_is_deterministic():
    y, p = _noisy()
    f1 = lambda a, b: ev.metrics(a, b)["f1"]
    lo, hi = ev.bootstrap_ci(y, p, f1, n=300, seed=0)
    assert lo < f1(y, p) < hi
    assert 0 < hi - lo < 0.15
    assert (lo, hi) == ev.bootstrap_ci(y, p, f1, n=300, seed=0)


def test_bootstrap_skips_undefined_resamples():
    y = np.array([0] * 30 + [1])  # many resamples have no positive: roc_auc undefined
    p = np.linspace(0, 1, 31)
    lo, hi = ev.bootstrap_ci(y, p, lambda a, b: ev.metrics(a, b)["roc_auc"], n=200)
    assert lo == pytest.approx(1.0) and hi == pytest.approx(1.0)


def test_best_threshold():
    y = np.array([0, 0, 0, 1, 1, 1])
    p = np.array([0.05, 0.10, 0.20, 0.30, 0.35, 0.40])
    t = ev.best_threshold(y, p)
    assert 0.20 < t < 0.30
    assert ev.metrics(y, p, t)["f1"] == pytest.approx(1.0)
    assert ev.metrics(y, p, 0.5)["f1"] == 0.0

    y, p = _noisy()
    for metric in ("f1", "accuracy"):
        t = ev.best_threshold(y, p, metric)
        brute = max(ev.metrics(y, p, c)[metric] for c in np.unique(p))
        assert ev.metrics(y, p, t)[metric] == pytest.approx(brute)


# ----------------------------------------------------------------------------- calibration

def test_perfectly_calibrated_predictor_has_small_ece():
    rng = np.random.default_rng(3)
    p = rng.random(50000)
    y = (rng.random(50000) < p).astype(int)
    assert ev.metrics(y, p)["ece"] < 0.02
    overconfident = np.where(p > 0.5, 0.99, 0.01)
    assert ev.metrics(y, overconfident)["ece"] > 0.2

    table = ev.calibration_table(y, p, bins=10)
    assert len(table) == 10 and sum(r["n"] for r in table) == 50000
    for r in table:
        assert set(r) == {"bin_lo", "bin_hi", "n", "mean_pred", "frac_pos"}
        assert r["bin_lo"] <= r["mean_pred"] <= r["bin_hi"]
        assert abs(r["mean_pred"] - r["frac_pos"]) < 0.03


def test_calibration_table_edges_and_empty_bins():
    table = ev.calibration_table([0, 1, 1], [0.0, 1.0, 0.95], bins=10)
    assert table[0]["n"] == 1 and table[9]["n"] == 2  # p == 1.0 falls in the last bin
    assert table[5]["n"] == 0 and table[5]["mean_pred"] is None and table[5]["frac_pos"] is None


# ----------------------------------------------------------------------------- splits

def test_split_stratified():
    acc = _accounts()
    train, val, test = ev.split_stratified(acc, test=0.2, val=0.1, seed=0)
    labelled = set(acc.loc[acc["label"].notna(), "account_id"])
    assert set(train) | set(val) | set(test) == labelled
    assert len(train) + len(val) + len(test) == len(labelled)  # disjoint, unlabelled excluded
    assert all(isinstance(i, str) for i in train + val + test)

    fam = acc.set_index("account_id")["family"]
    sizes = fam[list(labelled)].value_counts()
    for part, frac in ((test, 0.2), (val, 0.1)):
        counts = fam[part].value_counts()
        for f, n in sizes.items():
            assert abs(counts.get(f, 0) - n * frac) <= 1

    assert ev.split_stratified(acc, seed=0) == (train, val, test)
    assert ev.split_stratified(acc.iloc[::-1], seed=0)[2] == test  # row order does not matter
    assert ev.split_stratified(acc, seed=1)[2] != test


def test_leave_one_family_out_never_leaks():
    acc = _accounts()
    fam = acc.set_index("account_id")["family"]
    label = acc.set_index("account_id")["label"]
    labelled = set(acc.loc[acc["label"].notna(), "account_id"])
    folds = list(ev.leave_one_family_out(acc))
    assert [f for f, _, _ in folds] == ["fake_followers", "social_spambots_1", "traditional_spambots_1"]

    human_tests = []
    for held, train, test in folds:
        assert held not in set(fam[train])  # the held-out bot family never reaches train
        assert not set(train) & set(test)
        assert set(train) | set(test) == labelled
        test_bots = [i for i in test if label[i] == 1]
        assert set(fam[test_bots]) == {held}
        assert len(test_bots) == (fam[list(labelled)] == held).sum()  # whole family held out
        humans = sorted(i for i in test if label[i] == 0)
        assert (fam[humans] == "genuine").sum() == 40 and (fam[humans] == "celebrity").sum() == 10
        assert set(label[train]) == {0.0, 1.0}  # train still has both classes
        human_tests.append(humans)
    assert human_tests[0] == human_tests[1] == human_tests[2]


# ----------------------------------------------------------------------------- report

def test_write_report(tmp_path):
    y, p = _noisy()
    m = ev.metrics(y, p)
    results = {
        "dataset": "cresci-2017", "model": "lightgbm",
        "protocols": {
            "random_split": {**m, "f1_ci": [0.8, 0.9], "calibration": ev.calibration_table(y, p)},
            "leave_one_family_out": {"social_spambots_1": m, "fake_followers": ev.metrics(np.ones(4), [.1, .4, .6, .9])},
        },
        "notes": ["threshold chosen on validation"],
    }
    path = tmp_path / "report.md"
    text = ev.write_report(results, path)
    assert path.read_text(encoding="utf-8") == text
    for needle in ("cresci-2017", "lightgbm", "0.800 to 0.900", "social_spambots_1", "fake_followers",
                   "threshold chosen on validation", "How to read this", "Twitter", "YouTube", "n/a"):
        assert needle in text

    for sparse in ({}, {"protocols": {"random_split": {}}}, {"protocols": {"x": {"f1": None}}, "notes": "one"}):
        assert "How to read this" in ev.write_report(sparse, tmp_path / "sparse.md")


# ----------------------------------------------------------------------------- in-domain

def test_evaluate_in_domain(tmp_path):
    labels = tmp_path / "labels.csv"
    scores = tmp_path / "scores.csv"
    # both labelled on a1..a6 and a9: po = 5/7, pe = (3/7)^2 + (4/7)^2 = 25/49 -> kappa = 5/12
    labels.write_text(
        "account_id,annotator_a,annotator_b\n"
        "a1,1,1\na2,1,1\na3,0,0\na4,0,0\na5,1,0\na6,0,1\n"
        "a7,1,\na8,,\na9,0,0\n", encoding="utf-8")
    # a9 agreed but has no score; zz is scored but unlabelled
    scores.write_text(
        "account_id,bot_probability\n"
        "a1,90\na2,40\na3,10\na4,70\na5,99\na6,1\na7,50\nzz,50\n", encoding="utf-8")
    out = ev.evaluate_in_domain(str(labels), str(scores))
    assert out["kappa"] == pytest.approx(5 / 12)
    assert out["n_both_labelled"] == 7
    assert out["n_agreed"] == 5
    assert out["n_disagreements"] == 2
    assert out["n_single_annotator"] == 1
    assert out["n_agreed_without_score"] == 1
    assert out["n_evaluated"] == 4
    m = out["metrics"]  # a1 TP, a2 FN, a3 TN, a4 FP
    assert m["confusion"] == {"tp": 1, "fp": 1, "fn": 1, "tn": 1}
    assert m["f1"] == pytest.approx(0.5)
    lo, hi = out["f1_ci"]
    assert 0.0 <= lo <= m["f1"] <= hi <= 1.0
    json.dumps(out)


def test_evaluate_in_domain_perfect_agreement(tmp_path):
    labels = tmp_path / "labels.csv"
    scores = tmp_path / "scores.csv"
    labels.write_text("account_id,annotator_a,annotator_b\nx,1,1\ny,0,0\nz,1,1\n", encoding="utf-8")
    scores.write_text("account_id,bot_probability\nx,80\ny,20\nz,95\n", encoding="utf-8")
    out = ev.evaluate_in_domain(labels, scores)
    assert out["kappa"] == pytest.approx(1.0)
    assert out["n_disagreements"] == 0
    assert out["metrics"]["f1"] == pytest.approx(1.0)
