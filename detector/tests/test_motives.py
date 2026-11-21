import numpy as np
import pandas as pd

from detector.motives import export_neo4j_motives, motive_clades


def test_motive_clades_find_the_copy_paste_push(tmp_path):
    rng = np.random.default_rng(0)
    posts, accs = [], []
    chatter = ["went to the market today with my family", "great match yesterday what a goal", "coffee in prizren is the best",
               "who else watched the concert last night", "new phone battery lasts forever", "rain again this weekend sadly"]
    for i in range(60):
        accs.append(f"h{i}")
        for k in range(3):
            posts.append({"post_id": f"h{i}_{k}", "account_id": f"h{i}", "text": chatter[(i + k) % len(chatter)] + f" {i}", "thread_id": "v1"})
    for i in range(25):
        accs.append(f"b{i}")
        posts.append({"post_id": f"b{i}", "account_id": f"b{i}", "text": "Kosovo is Serbia forever share this", "thread_id": "v2"})
    clades, assign = motive_clades(pd.DataFrame({"account_id": accs}), pd.DataFrame(posts), k=4, threads={"v2": "Dispute video"})
    top = clades[0]
    assert top["coordinated_accounts"] >= 20 and top["copy_paste_share"] > 0.8
    assert any("video: Dispute video" in t for t in top["targets"])
    export_neo4j_motives(tmp_path, clades, assign)
    for f in ("nodes_motive.csv", "rels_pursues.csv", "rels_targets.csv", "motives.cypher"):
        assert (tmp_path / f).exists()
