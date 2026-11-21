"""Static graph view of scored accounts: one self-contained HTML file, no server.

  python -m detector.viewer [--scores DIR] [--out FILE] [--max-nodes 450]

Node colour is a continuous ramp on BOT %: light grey (human) → red → near-black (certain bot); size grows with BOT %.
Edges = coordination links (COPY / SYNC / REPLY …) weighted by the GNN's learned relation weight. Wrongly classified
accounts (when true labels exist) get an amber ring. Clicking a node lists every indicator's share of the decision.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd

HERE = Path(__file__).resolve().parent


def _pseudo(a: str) -> str:
    import hashlib
    return "acct_" + hashlib.blake2b(str(a).encode(), digest_size=4, key=b"htccip-viewer").hexdigest()


def build(scores_dir: Path, out: Path, max_nodes: int = 450, title: str = "", pseudonymize: bool = True) -> Path:
    recs = json.loads((scores_dir / "scores.json").read_text(encoding="utf-8"))
    sc = pd.read_csv(scores_dir / "scores.csv", dtype={"account_id": str})
    rels = pd.read_csv(scores_dir / "neo4j" / "rels_account.csv", dtype={"src": str, "dst": str})
    meta = json.loads((HERE / "model" / "model.json").read_text(encoding="utf-8"))
    label = dict(zip(sc.account_id, sc.label))
    by_id = {r["account_id"]: r for r in recs}
    # pick a readable subgraph: the most connected accounts, every mistake, and a sample of clear humans
    deg = pd.concat([rels.src, rels.dst]).value_counts()
    rels["w"] = rels.learned_weight.fillna(0) * rels.weight.fillna(1)
    pred = {r["account_id"]: r["verdict"] == "BOT" for r in recs}
    wrong = [a for a in by_id if label.get(a) == label.get(a) and pred[a] != bool(label.get(a))]
    # balanced sample across the whole probability range: every human/uncertain account the budget allows, then
    # the most connected bots (a pure "most connected" pick is ~100% bots and hides how the model separates them)
    humans = [a for a in sc.sort_values("bot_probability").account_id if pred.get(a) is False]
    mid = [a for a in sc[(sc.bot_probability > 5) & (sc.bot_probability < 95)].account_id]
    bots = [a for a in deg.index if pred.get(a)]
    budget_bots = max(120, max_nodes - len(wrong) - min(len(humans), max_nodes // 2) - len(mid))
    pick = list(dict.fromkeys(wrong + mid + humans[: max_nodes // 2] + bots[:budget_bots]))[:max_nodes]
    keep = set(pick)
    e = rels[rels.src.isin(keep) & rels.dst.isin(keep)].sort_values("w", ascending=False).head(max_nodes * 6)
    nodes = []
    for a in pick:
        r = by_id.get(a)
        if not r:
            continue
        lab = label.get(a)
        nodes.append({"id": _pseudo(a) if pseudonymize else a, "p": r["bot_probability"], "v": r["verdict"], "t": None if lab != lab else int(lab),
                      "ind": [[i["indicator"], i["percent"]] for i in r["indicators"] if abs(i["percent"]) >= 0.5][:14],
                      "deg": int(deg.get(a, 0))})
    pid = _pseudo if pseudonymize else (lambda x: x)
    links = [{"s": pid(s), "t": pid(t), "type": ty, "w": round(float(w), 3)} for s, t, ty, w in zip(e.src, e.dst, e.type, e.w)]
    lofo = meta.get("lofo_f1", {})
    stats = {"f1": meta.get("metrics_random_split", {}).get("f1"), "ci": meta.get("f1_ci"),
             "unseen": [min(lofo.values()), max(lofo.values())] if lofo else None,
             "ece": meta.get("metrics_random_split", {}).get("ece"), "gates": meta.get("relation_gates", {}),
             "n": len(recs), "bots": sum(1 for r in recs if r["verdict"] == "BOT"), "shown": len(nodes), "edges": len(links)}
    html = TEMPLATE.replace("__DATA__", json.dumps({"nodes": nodes, "links": links, "stats": stats,
                                                    "title": title or "Held-out benchmark accounts"}, ensure_ascii=False))
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html, encoding="utf-8")
    return out


TEMPLATE = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Bot detector graph</title>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=JetBrains+Mono:wght@500&display=swap" rel="stylesheet">
<script src="https://cdnjs.cloudflare.com/ajax/libs/d3/7.9.0/d3.min.js"></script>
<style>
  :root { --bg:#f6f7f9; --panel:#ffffff; --line:#e4e7ec; --txt:#101828; --mut:#667085; --red:#d92d20; }
  * { box-sizing:border-box } html,body { margin:0; height:100%; background:var(--bg); color:var(--txt); font:14px/1.45 Inter,system-ui,sans-serif }
  header { display:flex; align-items:center; gap:18px; padding:14px 22px; background:var(--panel); border-bottom:1px solid var(--line) }
  header h1 { font-size:16px; margin:0; font-weight:650; letter-spacing:-.01em } header .sub { color:var(--mut); font-size:12.5px }
  .chips { display:flex; gap:8px; margin-left:auto; flex-wrap:wrap }
  .chip { border:1px solid var(--line); border-radius:999px; padding:5px 11px; font-size:12px; background:#fff; white-space:nowrap }
  .chip b { font-variant-numeric:tabular-nums }
  main { display:grid; grid-template-columns:minmax(0,1fr) 360px; height:calc(100% - 61px) }
  #graph { position:relative; overflow:hidden } svg { width:100%; height:100%; display:block; cursor:grab }
  aside { background:var(--panel); border-left:1px solid var(--line); padding:18px 20px; overflow:auto }
  .legend { position:absolute; left:18px; bottom:16px; background:#ffffffee; border:1px solid var(--line); border-radius:10px; padding:10px 12px; font-size:12px; color:var(--mut); box-shadow:0 1px 3px #1018280f }
  .ramp { width:220px; height:10px; border-radius:5px; margin:6px 0 3px; background:linear-gradient(90deg,#eef1f5,#fca5a5 40%,#dc2626 65%,#7f1d1d 85%,#1a0505) }
  .ramp-l { display:flex; justify-content:space-between; font-variant-numeric:tabular-nums }
  .ring { display:inline-block; width:10px; height:10px; border-radius:50%; border:2px solid #f59e0b; vertical-align:-1px; margin-right:5px }
  .big { font-size:40px; font-weight:700; letter-spacing:-.03em; font-variant-numeric:tabular-nums; line-height:1 }
  .verdict { display:inline-block; font-size:11px; font-weight:700; letter-spacing:.06em; padding:4px 8px; border-radius:6px; margin-left:8px; vertical-align:8px }
  .row { display:grid; grid-template-columns:1fr 46px; gap:8px; align-items:center; font-size:12.5px; margin:5px 0 }
  .bar { height:7px; border-radius:4px; background:#f2f4f7; position:relative; overflow:hidden; margin-top:3px }
  .bar i { position:absolute; top:0; bottom:0; border-radius:4px }
  .pct { text-align:right; font:500 12px 'JetBrains Mono',monospace; font-variant-numeric:tabular-nums }
  h3 { font-size:12px; text-transform:uppercase; letter-spacing:.06em; color:var(--mut); margin:20px 0 8px; font-weight:600 }
  .muted { color:var(--mut); font-size:12.5px } .gate { display:flex; justify-content:space-between; font-size:12.5px; margin:4px 0 }
  .tip { position:absolute; pointer-events:none; background:#101828; color:#fff; font-size:12px; padding:6px 9px; border-radius:6px; opacity:0; transition:opacity .1s; white-space:nowrap }
  @media (max-width:900px){ main{grid-template-columns:1fr; grid-template-rows:60vh auto} aside{border-left:0;border-top:1px solid var(--line)} .chips{display:none} }
</style></head><body>
<header><div><h1>Is this account a bot?</h1><div class="sub" id="sub"></div></div><div class="chips" id="chips"></div></header>
<main><div id="graph"><svg></svg><div class="tip" id="tip"></div>
  <div class="legend"><div><b style="color:#101828">BOT probability</b> · colour and size</div><div class="ramp"></div>
  <div class="ramp-l"><span>0%</span><span>50%</span><span>100%</span></div><div style="margin-top:6px"><span class="ring"></span>model was wrong (true label differs)</div>
  <div style="margin-top:3px">edge thickness = learned relation weight × shared posts</div></div></div>
<aside id="side"></aside></main>
<script>
const D = __DATA__;
const color = d3.scaleLinear().domain([0, 40, 65, 85, 100]).range(["#eef1f5", "#fca5a5", "#dc2626", "#7f1d1d", "#1a0505"]).clamp(true);
const size = (p) => 6 + 8 * Math.pow(p / 100, 1.3);
const S = D.stats, f = (x, d = 3) => x == null ? "–" : (+x).toFixed(d);
document.getElementById("sub").textContent = `${D.title} · ${S.shown} of ${S.n.toLocaleString()} accounts shown · ${S.edges.toLocaleString()} coordination links`;
document.getElementById("chips").innerHTML = [
  `F1 <b>${f(S.f1)}</b>${S.ci ? ` <span style="color:#667085">(${f(S.ci[0])}–${f(S.ci[1])})</span>` : ""}`,
  S.unseen ? `Unseen bot types <b>${f(S.unseen[0])}–${f(S.unseen[1])}</b>` : "",
  `Calibration error <b>${f(100 * S.ece, 1)} pts</b>`, `Flagged <b>${S.bots.toLocaleString()}</b> / ${S.n.toLocaleString()}`].filter(Boolean).map((c) => `<span class="chip">${c}</span>`).join("");

const svg = d3.select("svg"), g = svg.append("g"), W = () => svg.node().clientWidth, H = () => svg.node().clientHeight;
const zoom = d3.zoom().scaleExtent([0.2, 6]).on("zoom", (e) => g.attr("transform", e.transform)); svg.call(zoom);
function fit() { const b = g.node().getBBox(); if (!b.width) return; const k = Math.min(W() / (b.width + 60), H() / (b.height + 60), 1.6);
  svg.transition().duration(600).call(zoom.transform, d3.zoomIdentity.translate(W() / 2 - k * (b.x + b.width / 2), H() / 2 - k * (b.y + b.height / 2)).scale(k)); }
const nodes = D.nodes.map((d) => ({ ...d })), byId = new Map(nodes.map((n) => [n.id, n]));
const links = D.links.filter((l) => byId.has(l.s) && byId.has(l.t)).map((l) => ({ ...l, source: l.s, target: l.t }));
const wmax = d3.max(links, (l) => l.w) || 1;
const link = g.append("g").selectAll("line").data(links).join("line")
  .attr("stroke", (l) => l.type === "SYNC" ? "#475467" : "#98a2b3").attr("stroke-opacity", 0.28)
  .attr("stroke-width", (l) => 0.4 + 2.6 * Math.sqrt(l.w / wmax));
const node = g.append("g").selectAll("circle").data(nodes).join("circle")
  .attr("r", (d) => size(d.p)).attr("fill", (d) => color(d.p))
  .attr("stroke", (d) => d.t != null && (d.t === 1) !== (d.v === "BOT") ? "#f59e0b" : d.p < 40 ? "#98a2b3" : "#ffffff")
  .attr("stroke-width", (d) => d.t != null && (d.t === 1) !== (d.v === "BOT") ? 3 : 1.2).style("cursor", "pointer")
  .call(d3.drag().on("start", (e, d) => { if (!e.active) sim.alphaTarget(0.2).restart(); d.fx = d.x; d.fy = d.y; })
    .on("drag", (e, d) => { d.fx = e.x; d.fy = e.y; }).on("end", (e, d) => { if (!e.active) sim.alphaTarget(0); d.fx = d.fy = null; }));
const tip = document.getElementById("tip");
node.on("mousemove", (e, d) => { tip.style.opacity = 1; tip.style.left = e.offsetX + 14 + "px"; tip.style.top = e.offsetY + 10 + "px";
  tip.textContent = `${d.id} · BOT ${d.p.toFixed(1)}%${d.t != null ? " · truly " + (d.t ? "bot" : "human") : ""}`; })
  .on("mouseleave", () => tip.style.opacity = 0).on("click", (e, d) => show(d));
const sim = d3.forceSimulation(nodes).force("link", d3.forceLink(links).id((d) => d.id).distance(28).strength(0.25))
  .force("charge", d3.forceManyBody().strength(-34)).force("center", d3.forceCenter(W() / 2, H() / 2))
  .force("collide", d3.forceCollide((d) => size(d.p) + 1.5)).force("x", d3.forceX(W() / 2).strength(0.04)).force("y", d3.forceY(H() / 2).strength(0.04))
  .on("end", fit).on("tick", () => { link.attr("x1", (l) => l.source.x).attr("y1", (l) => l.source.y).attr("x2", (l) => l.target.x).attr("y2", (l) => l.target.y);
    node.attr("cx", (d) => d.x).attr("cy", (d) => d.y); });

function show(d) {
  node.attr("opacity", (n) => n === d || links.some((l) => (l.source === d && l.target === n) || (l.target === d && l.source === n)) ? 1 : 0.25);
  link.attr("stroke-opacity", (l) => l.source === d || l.target === d ? 0.8 : 0.08);
  const bot = d.v === "BOT", wrong = d.t != null && (d.t === 1) !== bot;
  const mx = d3.max(d.ind, (x) => Math.abs(x[1])) || 1;
  document.getElementById("side").innerHTML = `
    <div class="muted">Account</div><div style="font:600 15px 'JetBrains Mono',monospace;margin-bottom:12px">${d.id}</div>
    <div><span class="big" style="color:${color(Math.max(d.p, 55))}">${d.p.toFixed(1)}%</span>
      <span class="verdict" style="background:${bot ? "#fee4e2" : "#ecfdf3"};color:${bot ? "#b42318" : "#027a48"}">${bot ? "BOT" : "NOT BOT"}</span></div>
    <div class="muted" style="margin-top:6px">BOT probability${d.t != null ? ` · true label <b style="color:#101828">${d.t ? "bot" : "human"}</b>${wrong ? ' · <b style="color:#b54708">model was wrong</b>' : " · correct"}` : ""}</div>
    <h3>Why: share of the decision</h3>
    ${d.ind.map(([n, v]) => `<div class="row"><div>${n}<div class="bar"><i style="left:0;width:${Math.abs(v) / mx * 100}%;background:${v > 0 ? "#d92d20" : "#12b76a"}"></i></div></div>
      <div class="pct" style="color:${v > 0 ? "#b42318" : "#027a48"}">${v > 0 ? "+" : "−"}${Math.abs(v).toFixed(1)}%</div></div>`).join("")}
    <div class="muted" style="margin-top:8px">Red pushes towards bot, green towards human. Shares sum to 100%.</div>
    <h3>Coordination links</h3><div class="muted">${links.filter((l) => l.source === d || l.target === d).length} shown · ${d.deg} in the full graph</div>`;
}
function overview() {
  const g2 = Object.entries(S.gates || {}).filter(([, v]) => v > 0), gm = d3.max(g2, (x) => x[1]) || 1;
  document.getElementById("side").innerHTML = `
    <div style="font-weight:650;font-size:15px">How to read this</div>
    <p class="muted">Each dot is an account the model never saw during training. The darker and larger it is, the more likely it is a bot. Lines connect accounts that posted the same text or posted it in sync. Click any dot to see why.</p>
    <h3>Learned relation weights (graph network)</h3>
    <div class="muted" style="margin:-2px 0 6px">Relations with no examples in the training data are switched off and not shown.</div>
    ${g2.map(([k, v]) => `<div class="gate"><span>${{ COPY: "Copy-paste", SYNC: "Synchronised posting", REPLY: "Replies", LINK: "Shared links", CO_THREAD: "Same thread, same minutes" }[k] || k}</span><span class="pct">${v.toFixed(2)}</span></div>
      <div class="bar"><i style="left:0;width:${v / gm * 100}%;background:#344054"></i></div>`).join("")}
    <h3>Distribution</h3><svg id="hist" width="320" height="90"></svg>`;
  const bins = d3.bin().domain([0, 100]).thresholds(20)(nodes.map((n) => n.p)), hs = d3.select("#hist"), y = d3.scaleLinear().domain([0, d3.max(bins, (b) => b.length)]).range([0, 70]);
  hs.selectAll("rect").data(bins).join("rect").attr("x", (b) => b.x0 * 3.15).attr("width", 14).attr("y", (b) => 80 - y(b.length)).attr("height", (b) => y(b.length)).attr("rx", 2).attr("fill", (b) => color((b.x0 + b.x1) / 2));
  hs.append("text").attr("x", 0).attr("y", 90).attr("font-size", 10).attr("fill", "#667085").text("0% human");
  hs.append("text").attr("x", 320).attr("y", 90).attr("text-anchor", "end").attr("font-size", 10).attr("fill", "#667085").text("bot 100%");
}
svg.on("click", (e) => { if (e.target.tagName === "svg") { node.attr("opacity", 1); link.attr("stroke-opacity", 0.28); overview(); } });
overview();
</script></body></html>
"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--scores", default=str(HERE / "data" / "scores_cresci2017_test"))
    ap.add_argument("--out", default=str(HERE / "data" / "graph.html"))
    ap.add_argument("--max-nodes", type=int, default=450)
    a = ap.parse_args()
    print(build(Path(a.scores), Path(a.out), a.max_nodes))


if __name__ == "__main__":
    main()
