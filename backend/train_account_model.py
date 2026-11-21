"""Train the five-count account classifier and evaluate frozen checkpoints."""
import hashlib
import numpy as np
from account_bot import MODEL, discover, forward

DATASETS = ("gilani-2017", "cresci-rtbust-2019", "midterm-2018", "cresci-stock-2018",
            "pronbots-2019", "celebrity-2019", "botwiki-2019", "vendor-purchased-2019",
            "verified-2019", "political-bots-2019", "botometer-feedback-2019")
EXTERNAL = set(DATASETS[-3:])


def probabilities(model, x):
    return forward((x - model["mean"]) / model["scale"], *(model[k] for k in ("w","b","v","c")))[1]


def metrics(model, x, y):
    pred = probabilities(model, x) >= model["threshold"]
    return dict(TP=int(sum(pred & (y == 1))), FN=int(sum(~pred & (y == 1))),
                TN=int(sum(~pred & (y == 0))), FP=int(sum(pred & (y == 0))))


def train_expanded():
    rng = np.random.default_rng(42)
    records = {}
    conflict = set()
    for name, rows in discover(DATASETS):
        print(name, len(rows), flush=True)
        for uid, (counts, label) in rows.items():
            if uid in records:
                old_source, _, old_label = records[uid]
                if old_label != label:
                    conflict.add(uid)
                if old_source in EXTERNAL or name not in EXTERNAL:
                    continue
            records[uid] = (name, counts, label)
    groups = {"train": [], "validation": [], "test": [], **{k: [] for k in EXTERNAL}}
    for uid, (source, counts, label) in records.items():
        if uid in conflict:
            continue
        bucket = int.from_bytes(hashlib.sha256(uid.encode()).digest()[:4], "big") % 100
        split = "train" if bucket < 80 else "validation" if bucket < 90 else "test"
        # Entire development populations guide checkpoint selection.
        if source == "vendor-purchased-2019":
            split = "validation"
        groups[source if source in EXTERNAL else split].append((counts, label, source))
    def arrays(rows):
        counts, labels, _ = zip(*rows)
        return np.log1p(counts), np.array(labels)
    x, y = arrays(groups["train"])
    vx, vy = arrays(groups["validation"])
    def population_weights(rows):
        keys = [f"{source}/{label}" for _, label, source in rows]
        _, index, sizes = np.unique(keys, return_inverse=True, return_counts=True)
        weights = 1.0 / sizes[index]
        return weights / weights.mean()
    training_weights = population_weights(groups["train"])
    validation_weights = population_weights(groups["validation"])
    mean, scale = x.mean(0), np.maximum(x.std(0), 1e-6)
    x = (x - mean) / scale
    params = [rng.normal(0,.3,(5,64)), np.zeros(64), rng.normal(0,.15,64), np.zeros(())]
    first, second = [[np.zeros_like(p) for p in params] for _ in range(2)]
    steps, best = 0, -1
    for epoch in range(60):
        for idx in np.array_split(rng.permutation(len(y)), (len(y)+255)//256):
            hidden, prob = forward(x[idx], *params)
            err = (prob-y[idx])*training_weights[idx]/len(idx)
            grad = err[:,None] * params[2] * (1-hidden**2)
            steps += 1
            for p,m,v,g in zip(params, first, second,
                               (x[idx].T@grad, grad.sum(0), hidden.T@err, err.sum())):
                m *= .9
                m += .1*g
                v *= .999
                v += .001*g*g
                p -= .002*(m/(1-.9**steps))/(np.sqrt(v/(1-.999**steps))+1e-8)
        model = dict(zip(("w","b","v","c"), params), mean=mean, scale=scale)
        vp = probabilities(model, vx)
        # Equal influence per population within each class, independent of its size.
        order = np.argsort(vp)
        ts, start = np.unique(vp[order], return_index=True)
        def below(label):
            weights = validation_weights[order] * (vy[order] == label)
            return np.r_[0, np.cumsum(weights)][start] / weights.sum()
        recall = 1 - below(1)
        false_alarm = 1 - below(0)
        # Recall-oriented F2 with equal class priors across source populations.
        objective = 5 * recall / (4 + recall + false_alarm)
        i = objective.argmax()
        if objective[i] > best:
            best = objective[i]
            checkpoint = {k: np.copy(v) for k,v in model.items()}
            checkpoint["threshold"] = ts[i]
    print("Training", len(y), "validation", len(vy), "conflicting IDs excluded", len(conflict))
    prior_path = MODEL.with_name("account_bot_expanded.npz")
    if MODEL.exists() and not prior_path.exists():
        with prior_path.open("xb") as backup:
            backup.write(MODEL.read_bytes())
    for name in ("test", *sorted(EXTERNAL)):
        tx, ty = arrays(groups[name])
        print(name, "expanded", metrics(checkpoint, tx, ty))
        if prior_path.exists():
            with np.load(prior_path) as prior:
                print(name, "original", metrics(prior, tx, ty))
    temp = MODEL.with_suffix(".next.npz")
    np.savez(temp, **checkpoint)
    temp.replace(MODEL)
