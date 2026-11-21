"""Motive clades: group accounts by what they push, and say where it is aimed.

The detector's verdict is only BOT %. This layer looks for motives behind accounts and clusters ("clades") them:
  1. every account becomes one document (all its posts);
  2. TF-IDF (word 1-2-grams) + MiniBatchKMeans groups accounts by content;
  3. each clade is labelled by its strongest terms and the known campaign narratives (backend lexicon) it matches;
  4. targets = where the clade posts: video/thread titles, @mentions, #hashtags, link domains;
  5. if BOT scores are given, each clade gets its share of BOT accounts and mean BOT %.
Ranked by "coordination mass" = accounts × share of copy-pasted posts, so organised pushes rise above chatter.
"""
from __future__ import annotations

import collections
import re
import sys
from pathlib import Path

import numpy as np
import pandas as pd

from .features import template

try:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))
    from lexicons import TROPES, fold  # type: ignore
except Exception:  # pragma: no cover
    TROPES, fold = {}, (lambda s: s.lower())

_TAG, _MENTION, _DOMAIN = re.compile(r"#\w+"), re.compile(r"@\w+"), re.compile(r"https?://([^/\s]+)")
# filler words in English, Serbian/Bosnian/Croatian/Montenegrin (Latin) and Albanian, so clades form around content
STOP = set("""the and for that this with you your are was were have has had not but all they them their there what
when who will would can could just like from about into out more most some than then too very its it's i'm im you're
dont don't does did been being also only one our ours his her him she he we us me my mine an a of to in on at is be as
or if so no yes by do up rt via amp http https www com
i u je da se na su za od ne to sa ali kao sve samo ili jer sam si smo ste ga mu im ih ja ti mi vi oni ona ono ovo taj
ta te što sto kako koji koja koje kad kada nije bio bila bilo biti će ce bi već vec još jos ima nema tako pa li do iz
po pre bez ovaj ova ove onda sad sada evo eto
dhe në ne është eshte nuk që qe të te me për per një nje se si ka ishte janë jane do edhe por ky kjo ato ata ai ajo
na ju ti unë une ne nga tek pa mos apo vetëm vetem shumë shume""".split())
_PATS = {k: [re.compile(x) for kk, v in s.items() if kk not in ("exemplars", "label", "severity") and isinstance(v, list)
             for x in v if isinstance(x, str)] for k, s in TROPES.items()}


def _narratives(text: str) -> list[str]:
    t = fold(text)
    return [TROPES[k]["label"] for k, ps in _PATS.items() if any(p.search(t) for p in ps)]


def motive_clades(accounts: pd.DataFrame, posts: pd.DataFrame, scores: pd.DataFrame | None = None,
                  k: int | None = None, threads: dict | None = None, seed: int = 0, top: int = 10) -> tuple[list[dict], pd.Series]:
    """Returns (clades ranked by coordination mass, account_id -> clade id)."""
    from sklearn.cluster import MiniBatchKMeans
    from sklearn.feature_extraction.text import TfidfVectorizer

    p = posts[["account_id", "text", "thread_id"]].copy()
    p["text"] = p["text"].fillna("").astype(str)
    p["tpl"] = p["text"].map(template)
    shared = p[p.tpl != ""].groupby("tpl")["account_id"].nunique()
    p["copied"] = p["tpl"].map(shared).fillna(1) > 1
    docs = p.groupby("account_id")["text"].apply(lambda s: " ".join(s)[:4000])
    docs = docs[docs.str.len() > 0]
    vec = TfidfVectorizer(ngram_range=(1, 2), min_df=3, max_df=0.5, max_features=40000, sublinear_tf=True,
                          token_pattern=r"(?u)\b[^\W\d_]{3,}\b", stop_words=sorted(STOP))
    X = vec.fit_transform(docs.to_numpy())
    k = k or int(np.clip(round(np.sqrt(len(docs) / 4)), 8, 40))
    km = MiniBatchKMeans(n_clusters=k, random_state=seed, n_init=3, batch_size=2048).fit(X)
    assign = pd.Series(km.labels_, index=docs.index, name="clade")
    terms = np.array(vec.get_feature_names_out())
    sc = scores.set_index("account_id") if scores is not None else None
    out = []
    for c in range(k):
        ids = assign.index[assign == c]
        if not len(ids):
            continue
        cp = p[p.account_id.isin(ids)]
        cent = km.cluster_centers_[c]
        top_terms = [t for t in terms[np.argsort(cent)[::-1][:12]]][:8]
        narr = collections.Counter(n for t in cp.text for n in _narratives(t))
        copied = cp[cp.copied]
        examples = (copied if len(copied) else cp).groupby("tpl")["account_id"].nunique().sort_values(ascending=False)
        ex_text = [cp[cp.tpl == t].text.iloc[0][:140] for t in examples.index[:3] if t]
        tgt = collections.Counter()
        for t in cp.text:
            tgt.update("#" + x[1:].lower() for x in _TAG.findall(t)); tgt.update("@" + x[1:] for x in _MENTION.findall(t))
            tgt.update("link:" + d.lower() for d in _DOMAIN.findall(t))
        if threads:
            tgt.update("video: " + threads.get(th, th) for th in cp.thread_id if th)
        clade = {"clade": int(c), "accounts": int(len(ids)), "posts": int(len(cp)),
                 "copy_paste_share": round(float(cp.copied.mean()), 3),
                 "motive_terms": top_terms, "known_narratives": [f"{n} ({v})" for n, v in narr.most_common(3)],
                 "examples": ex_text, "targets": [f"{t} ({v})" for t, v in tgt.most_common(6)]}
        if sc is not None:
            s = sc.reindex(ids)
            clade["bot_share"] = round(float((s["verdict"] == "BOT").mean()), 3)
            clade["mean_bot_pct"] = round(float(s["bot_probability"].mean()), 1)
            if "label" in s and s["label"].notna().any():
                clade["true_bot_share"] = round(float(s["label"].mean()), 3)
        # accounts that posted at least one copy-pasted text: the organised core of the clade
        clade["coordinated_accounts"] = int(cp[cp.copied].account_id.nunique())
        clade["coordination_mass"] = clade["coordinated_accounts"]
        out.append(clade)
    out.sort(key=lambda d: -d["coordination_mass"])
    return out[:top], assign


def export_neo4j_motives(out_dir: Path, clades: list[dict], assign: pd.Series) -> None:
    """nodes_motive.csv, rels_pursues.csv, rels_targets.csv + motives.cypher: (:Account)-[:PURSUES]->(:Motive)-[:TARGETS]->(:Target)."""
    out_dir = Path(out_dir); out_dir.mkdir(parents=True, exist_ok=True)
    keep = {c["clade"] for c in clades}
    pd.DataFrame([{"motive_id": f"M{c['clade']}", "name": " / ".join(c["motive_terms"][:3]), "accounts": c["accounts"],
                   "copy_paste_share": c["copy_paste_share"], "bot_share": c.get("bot_share", ""),
                   "narratives": "; ".join(c["known_narratives"])} for c in clades]).to_csv(out_dir / "nodes_motive.csv", index=False)
    a = assign[assign.isin(keep)]
    pd.DataFrame({"account_id": a.index, "motive_id": ["M" + str(x) for x in a.to_numpy()]}).to_csv(out_dir / "rels_pursues.csv", index=False)
    rows = [{"motive_id": f"M{c['clade']}", "target": t.rsplit(" (", 1)[0], "count": int(t.rsplit("(", 1)[1].rstrip(")"))}
            for c in clades for t in c["targets"]]
    pd.DataFrame(rows).to_csv(out_dir / "rels_targets.csv", index=False)
    (out_dir / "motives.cypher").write_text("""// Motive clades. Copy the three CSVs into Neo4j's import/ folder, then run.
CREATE CONSTRAINT motive_id IF NOT EXISTS FOR (m:Motive) REQUIRE m.motive_id IS UNIQUE;
CREATE CONSTRAINT target_name IF NOT EXISTS FOR (t:Target) REQUIRE t.name IS UNIQUE;
LOAD CSV WITH HEADERS FROM 'file:///nodes_motive.csv' AS row
MERGE (m:Motive {motive_id: row.motive_id})
SET m.name = row.name, m.accounts = toInteger(row.accounts), m.copy_paste_share = toFloat(row.copy_paste_share),
    m.bot_share = CASE row.bot_share WHEN '' THEN null ELSE toFloat(row.bot_share) END, m.narratives = row.narratives;
LOAD CSV WITH HEADERS FROM 'file:///rels_pursues.csv' AS row
MATCH (a:Account {account_id: row.account_id}) MATCH (m:Motive {motive_id: row.motive_id})
MERGE (a)-[:PURSUES]->(m);
LOAD CSV WITH HEADERS FROM 'file:///rels_targets.csv' AS row
MATCH (m:Motive {motive_id: row.motive_id}) MERGE (t:Target {name: row.target})
MERGE (m)-[r:TARGETS]->(t) SET r.count = toInteger(row.count);
// View: MATCH p=(m:Motive)-[:TARGETS]->(t:Target) RETURN p
// View: MATCH (a:Bot)-[:PURSUES]->(m:Motive) RETURN m.name, count(a) AS bots ORDER BY bots DESC
""", encoding="utf-8")
