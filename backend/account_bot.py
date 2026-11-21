"""Research prototype. Run with 'train', or 'score' followed by five counts in FIELDS order."""
import argparse
import io
import json
import tarfile
from pathlib import Path
from urllib.parse import urljoin

import numpy as np

FIELDS = ("followers_count", "friends_count", "listed_count", "favourites_count", "statuses_count")
MODEL = Path(__file__).with_name("account_bot_weights.npz")
CATALOG = "https://botometer.osome.iu.edu/bot-repository/datasets/"


def discover(datasets=("gilani-2017", "cresci-rtbust-2019", "botometer-feedback-2019")):
    from urllib.request import urlopen

    def fetch(url):
        with urlopen(url, timeout=90) as response:
            return response.read()

    names = fetch(CATALOG + "index").decode().split()
    for name in datasets:
        if name not in names:
            raise RuntimeError(f"Dataset missing from catalog: {name}")
        base = urljoin(CATALOG, name + "/")
        info = json.loads(fetch(base + "info.json"))
        data = fetch(base + info["filename"])
        with tarfile.open(fileobj=io.BytesIO(data)) as archive:
            labels = dict(line.decode().strip().split() for line in archive.extractfile(name + ".tsv") if line.strip())
            processed = name == "midterm-2018"
            suffix = "_processed_user_objects.json" if processed else "_tweets.json"
            tweets = json.load(archive.extractfile(name + suffix))
        rows = {}
        for tweet in tweets:
            user = tweet if processed else tweet["user"]
            uid = str(user["user_id"] if processed else user["id"])
            if labels.get(uid) in ("bot", "human") and all(isinstance(user.get(f), int) and user[f] >= 0 for f in FIELDS):
                rows[uid] = ([user[f] for f in FIELDS], int(labels[uid] == "bot"))
        yield name, rows


def forward(x, w, b, v, c):
    hidden = np.tanh(x @ w + b)
    return hidden, 1 / (1 + np.exp(-np.clip(hidden @ v + c, -40, 40)))


def train():
    sources = dict(discover())
    test = sources.pop("botometer-feedback-2019")
    pool = {}
    conflicts = set()
    for rows in sources.values():
        for uid, row in rows.items():
            if uid in pool and row[1] != pool[uid][1]:
                conflicts.add(uid)
            pool[uid] = row
    for uid in set(test) | conflicts:
        pool.pop(uid, None)
    rng = np.random.default_rng(42)
    ids = rng.permutation(sorted(pool))
    split = int(.8 * len(ids))

    def unpack(rows):
        counts, labels = zip(*rows)
        return np.log1p(counts), np.array(labels)

    x, y = unpack(pool[i] for i in ids[:split])
    valid, vy = unpack(pool[i] for i in ids[split:])
    tx, ty = unpack(test.values())
    mean, scale = x.mean(0), np.maximum(x.std(0), 1e-6)
    x = (x - mean) / scale
    w = rng.normal(0, .3, (len(FIELDS), 16))
    b = np.zeros(16)
    v = rng.normal(0, .3, 16)
    c = 0.
    for epoch in range(140):
        for idx in np.array_split(rng.permutation(len(x)), max(1, len(x) // 64)):
            hidden, pred = forward(x[idx], w, b, v, c)
            err = (pred - y[idx]) / len(idx)
            grad = err[:, None] * v * (1 - hidden**2)
            w -= .05 * x[idx].T @ grad
            b -= .05 * grad.sum(0)
            v -= .05 * hidden.T @ err
            c -= .05 * err.sum()
    vp = forward((valid - mean) / scale, w, b, v, c)[1]
    def balanced(t):
        return ((vp[vy == 1] >= t).mean() + (vp[vy == 0] < t).mean()) / 2
    threshold = max(np.linspace(.05, .95, 181), key=balanced)
    prediction = forward((tx - mean) / scale, w, b, v, c)[1] >= threshold
    np.savez(MODEL, w=w, b=b, v=v, c=c, mean=mean, scale=scale, threshold=threshold)
    print("train, validation, independent test:", len(x), len(valid), len(tx))
    print("TP FP TN FN:", *(int(((prediction == p) & (ty == label)).sum()) for p, label in [(1,1),(1,0),(0,0),(0,1)]))


def score(counts, *, high_recall=False):
    x = np.asarray(counts, dtype=float)
    if x.shape != (5,) or not np.isfinite(x).all() or (x < 0).any() or (x != np.floor(x)).any():
        raise ValueError("Supply five nonnegative integer counts in this order: " + ", ".join(FIELDS))
    previous = MODEL.with_name("account_bot_expanded.npz")
    path = MODEL if high_recall or not previous.exists() else previous
    with np.load(path, allow_pickle=False) as model:
        normalized = (np.log1p(x) - model["mean"]) / model["scale"]
        value = float(forward(normalized, *(model[k] for k in ("w", "b", "v", "c")))[1])
        return {"bot_score": value, "label": "bot" if value >= model["threshold"] else "human"}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("train")
    scoring = commands.add_parser("score")
    scoring.add_argument("counts", type=int, nargs=5, help=", ".join(FIELDS))
    scoring.add_argument("--high-recall", action="store_true", help="Use the source-balanced F2 model")
    args = parser.parse_args()
    if args.command == "train":
        from train_account_model import train_expanded
        train_expanded()
    else:
        print(json.dumps(score(args.counts, high_recall=args.high_recall)))
