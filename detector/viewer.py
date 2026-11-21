"""Static, self-contained graph view of the detector deciding (no server): python -m detector.viewer

Built from the held-out benchmark split (accounts the model never trained on):
  * nodes shade from light grey (human) through red to near-black with BOT probability; size grows with it;
  * accounts are grouped into motive clades (motives.py), drawn as labelled hulls;
  * "Run detector" animates the model scoring every account;
  * clicking an account draws its decision: indicators → indicator model, neighbours → graph network → calibrated BOT %.
Account ids are pseudonymised because the page is published.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent


def _pseudo(a: str) -> str:
    return "acct_" + hashlib.blake2b(str(a).encode(), digest_size=4, key=b"htccip-viewer").hexdigest()


def _clade_name(c: dict) -> str:
    if c.get("true_bot_share", c.get("bot_share", 1)) < 0.5:
        return "Mostly ordinary accounts"
    tags = [t.rsplit(" (", 1) for t in c["targets"] if t.startswith("#")]
    tags = [(t, int(n.rstrip(")"))) for t, n in tags]
    if tags and tags[0][1] >= 15:
        return f"{tags[0][0]} push"
    import html
    ex = html.unescape((c["examples"] or [""])[0]).replace("RT @", "@").strip()
    return "“" + (ex[:44] + "…" if len(ex) > 44 else ex) + "”" if ex else " / ".join(c["motive_terms"][:3])


def build(out: Path, data: str = "cresci2017", per_clade: int = 42, max_nodes: int = 520) -> Path:
    from . import datasets, evaluate as E, model as M
    from .graph import RELATIONS
    from .motives import motive_clades
    from .weights import percent_contributions

    accounts, posts = datasets.load(data)
    accounts = accounts.drop_duplicates("account_id")
    accounts = accounts[accounts.account_id.isin(set(posts.account_id))]
    _, _, te = E.split_stratified(accounts, test=0.2, val=0.1, seed=0)
    accounts = accounts[accounts.account_id.isin(set(te))]
    posts = posts[posts.account_id.isin(set(te))]
    m = M.load(HERE / "model")
    X, graph = M.design(accounts, posts)
    o = M.predict(m, X, graph)
    pct = percent_contributions(o["contrib"], m["features"])
    ids = graph.ids
    lab = accounts.set_index("account_id")["label"].reindex(ids).to_numpy()
    p = 100 * o["ensemble"]
    thr = 100 * m["threshold"]
    scores = pd.DataFrame({"account_id": ids, "bot_probability": p, "verdict": np.where(p >= thr, "BOT", "NOT BOT"), "label": lab})
    clades, assign = motive_clades(accounts, posts, scores, k=12, top=12)
    # clades that get the same name (e.g. four "#talnts push" waves) are merged into one
    name_of = {c["clade"]: _clade_name(c) for c in clades}
    merged = {}
    for c in clades:
        nm = name_of[c["clade"]]
        if nm not in merged:
            merged[nm] = {**c, "_ids": [c["clade"]]}
        else:
            mm = merged[nm]; n0, n1 = mm["accounts"], c["accounts"]
            for k in ("bot_share", "true_bot_share"):
                if k in c and k in mm:
                    mm[k] = (mm[k] * n0 + c[k] * n1) / (n0 + n1)
            mm["accounts"] = n0 + n1; mm["_ids"].append(c["clade"])
    canon = {cid: mm["_ids"][0] for mm in merged.values() for cid in mm["_ids"]}
    assign = assign.map(lambda x: canon.get(int(x), -1))
    clades = list(merged.values())
    cname = {c["clade"]: nm for nm, c in merged.items()}
    # nodes: per clade a sample (most connected first), plus every wrongly classified account
    gates = m["meta"]["relation_gates"]
    coord = sum(graph.relations[r] * gates.get(r, 0) for r in RELATIONS if gates.get(r, 0) > 0)
    deg = np.asarray((coord > 0).sum(axis=1)).ravel()
    pos = {a: i for i, a in enumerate(ids)}
    wrong = [a for a, pp, l in zip(ids, p, lab) if l == l and (pp >= thr) != bool(l)]
    pick = list(wrong)
    for c in clades:
        members = [a for a in assign.index[assign == c["clade"]] if a in pos]
        members.sort(key=lambda a: -deg[pos[a]])
        pick += members[:per_clade]
    pick = list(dict.fromkeys(pick))[:max_nodes]
    keep = np.array([pos[a] for a in pick])
    sub = coord[keep][:, keep].tocoo()
    links = [{"s": int(i), "t": int(j), "w": round(float(w), 2)} for i, j, w in zip(sub.row, sub.col, sub.data) if i < j]
    links.sort(key=lambda l: -l["w"]); links = links[: max_nodes * 5]
    neigh_mean = np.asarray(coord.multiply(1 / np.maximum(coord.sum(axis=1), 1e-9)) @ o["ensemble"]).ravel()
    nodes = []
    for a in pick:
        i = pos[a]
        rows = pct[pct.row == i]
        nodes.append({"id": _pseudo(a), "p": round(float(p[i]), 1), "t": None if lab[i] != lab[i] else int(lab[i]),
                      "c": int(assign.get(a, -1)) if int(assign.get(a, -1)) in cname else -1,
                      "lgb": round(100 * float(o["lightgbm"][i]), 1), "gnn": round(100 * float(o["gnn"][i]), 1),
                      "nb": int(deg[i]), "nbp": round(100 * float(neigh_mean[i]), 1),
                      "ind": [[r.indicator, r.percent] for r in rows.itertuples() if abs(r.percent) >= 0.4][:12]})
    lofo = m["meta"].get("lofo_f1", {})
    data_out = {
        "nodes": nodes, "links": links, "thr": round(thr, 1),
        "clades": [{"id": c["clade"], "name": cname[c["clade"]], "n": int(c["accounts"]), "bot": c.get("bot_share"),
                    "true": c.get("true_bot_share"), "ex": __import__("html").unescape((c["examples"] or [""])[0])[:140],
                    "targets": [t for t in c["targets"][:3]]} for c in clades],
        "stats": {"f1": m["meta"]["metrics_random_split"]["f1"], "ci": m["meta"].get("f1_ci"),
                  "unseen": [min(lofo.values()), max(lofo.values())] if lofo else None,
                  "ece": m["meta"]["metrics_random_split"]["ece"], "gates": {k: v for k, v in gates.items() if v > 0},
                  "n": len(ids), "bots": int((p >= thr).sum()), "wrong": len(wrong)}}
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(TEMPLATE.replace("__DATA__", json.dumps(data_out, ensure_ascii=False)), encoding="utf-8")
    return out


TEMPLATE = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Bot detector · decision graph</title>
<link rel="preconnect" href="https://fonts.googleapis.com"><link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&family=JetBrains+Mono:wght@500;600&display=swap" rel="stylesheet">
<script src="https://cdnjs.cloudflare.com/ajax/libs/d3/7.9.0/d3.min.js"></script>
<style>
:root{--bg:#f4f5f7;--card:#fff;--line:#e5e7eb;--ink:#0b0f19;--mut:#6b7280;--red:#dc2626;--grn:#059669}
*{box-sizing:border-box}html,body{margin:0;height:100%;background:var(--bg);color:var(--ink);font:14px/1.45 Inter,system-ui,sans-serif;-webkit-font-smoothing:antialiased}
header{height:72px;display:flex;align-items:center;gap:22px;padding:0 26px;background:var(--card);border-bottom:1px solid var(--line)}
.brand{display:flex;align-items:center;gap:12px}.logo{width:34px;height:34px;border-radius:9px;background:#0b0f19;display:grid;place-items:center}
.logo i{width:12px;height:12px;border-radius:50%;background:linear-gradient(135deg,#fca5a5,#dc2626 55%,#1a0505)}
h1{font-size:17px;margin:0;font-weight:700;letter-spacing:-.015em}.sub{color:var(--mut);font-size:12.5px}
.kpis{display:flex;gap:26px;margin-left:auto}.kpi{text-align:right}.kpi b{display:block;font:700 19px/1.1 Inter;letter-spacing:-.02em;font-variant-numeric:tabular-nums}
.kpi span{font-size:11px;color:var(--mut);text-transform:uppercase;letter-spacing:.06em}
button.run{font:600 13px Inter;border:0;border-radius:9px;padding:11px 18px;background:#0b0f19;color:#fff;cursor:pointer;box-shadow:0 1px 2px #0003}
button.run:hover{background:#1f2937}button.run:disabled{opacity:.5;cursor:default}
main{display:grid;grid-template-columns:minmax(0,1fr) 400px;height:calc(100% - 72px)}
#stage{position:relative;overflow:hidden}svg#g{width:100%;height:100%;display:block;cursor:grab}
.hull{stroke-width:1.2;stroke-linejoin:round}.hlabel{font:600 12px Inter;fill:#111827;paint-order:stroke;stroke:#f4f5f7;stroke-width:4px}
.hsub{font:500 11px Inter;fill:#6b7280;paint-order:stroke;stroke:#f4f5f7;stroke-width:4px}
.legend{position:absolute;left:20px;bottom:18px;background:#fffffff2;border:1px solid var(--line);border-radius:12px;padding:12px 14px;font-size:12px;color:var(--mut);box-shadow:0 4px 14px #0b0f1910}
.ramp{width:240px;height:10px;border-radius:6px;margin:7px 0 4px;background:linear-gradient(90deg,#e5e7eb,#fca5a5 35%,#dc2626 62%,#7f1d1d 84%,#120202)}
.rl{display:flex;justify-content:space-between;font:500 11px 'JetBrains Mono'}
.status{position:absolute;left:50%;top:18px;transform:translateX(-50%);background:#0b0f19;color:#fff;font:600 12.5px Inter;padding:8px 14px;border-radius:999px;box-shadow:0 6px 18px #0b0f1930;opacity:0;transition:opacity .3s}
aside{background:var(--card);border-left:1px solid var(--line);overflow:auto;padding:22px 24px}
h2{font-size:15px;margin:0 0 4px;font-weight:700;letter-spacing:-.01em}h3{font-size:11px;text-transform:uppercase;letter-spacing:.07em;color:var(--mut);margin:22px 0 10px;font-weight:600}
.muted{color:var(--mut);font-size:12.5px}.mono{font-family:'JetBrains Mono',monospace}
.big{font:800 46px/1 Inter;letter-spacing:-.04em;font-variant-numeric:tabular-nums}
.pill{display:inline-block;font:700 11px Inter;letter-spacing:.06em;padding:5px 9px;border-radius:7px;vertical-align:12px;margin-left:10px}
.clade{display:grid;grid-template-columns:12px 1fr auto;gap:10px;align-items:center;padding:8px 0;border-bottom:1px solid #f3f4f6;cursor:pointer;font-size:12.5px}
.clade:hover .nm{text-decoration:underline}.sw{width:12px;height:12px;border-radius:4px}.nm{font-weight:600}
.bar{height:6px;border-radius:3px;background:#f3f4f6;overflow:hidden}.bar i{display:block;height:100%}
.tip{position:absolute;pointer-events:none;background:#0b0f19;color:#fff;font:500 12px Inter;padding:7px 10px;border-radius:7px;opacity:0;white-space:nowrap;box-shadow:0 6px 18px #0003}
@media(max-width:980px){main{grid-template-columns:1fr;grid-template-rows:62vh auto}.kpis{display:none}}
</style></head><body>
<header><div class="brand"><div class="logo"><i></i></div><div><h1>Is this account a bot?</h1><div class="sub" id="sub"></div></div></div>
<div class="kpis" id="kpis"></div><button class="run" id="run">▶ Run detector</button></header>
<main><div id="stage"><svg id="g"></svg><div class="status" id="status"></div><div class="tip" id="tip"></div>
<div class="legend"><b style="color:#0b0f19">BOT probability</b> — shade &amp; size<div class="ramp"></div><div class="rl"><span>0%</span><span>50%</span><span>100%</span></div>
<div style="margin-top:8px;display:flex;gap:14px"><span><svg width="12" height="12"><circle cx="6" cy="6" r="4.5" fill="#fff" stroke="#f59e0b" stroke-width="2.4"/></svg> model wrong</span><span>outline = motive clade</span></div></div></div>
<aside id="side"></aside></main>
<script>
const D = __DATA__, S = D.stats;
const shade = d3.scaleLinear().domain([0, 35, 62, 84, 100]).range(["#e5e7eb", "#fca5a5", "#dc2626", "#7f1d1d", "#120202"]).clamp(true);
const R = (p) => 5 + 9 * Math.pow(p / 100, 1.2), f3 = (x) => x == null ? "–" : (+x).toFixed(3);
const CL = new Map(D.clades.map((c, i) => [c.id, { ...c, i }]));
document.getElementById("sub").textContent = `Held-out benchmark accounts the model never trained on · ${D.nodes.length} of ${S.n.toLocaleString()} shown · ${D.clades.length} motive clades`;
const kp = document.getElementById("kpis");
kp.innerHTML = [[`${f3(S.f1)}`, "F1 held-out"], [S.unseen ? `${f3(S.unseen[0])}–${f3(S.unseen[1]).slice(1)}` : "–", "F1 unseen bot types"],
  [`${(100 * S.ece).toFixed(1)} pts`, "calibration error"], [`<em id="flag" style="font-style:normal">0</em> / ${S.n.toLocaleString()}`, "flagged BOT"]]
  .map(([v, l]) => `<div class="kpi"><b>${v}</b><span>${l}</span></div>`).join("");

const svg = d3.select("#g"), root = svg.append("g"), W = () => svg.node().clientWidth, H = () => svg.node().clientHeight;
const zoom = d3.zoom().scaleExtent([0.25, 5]).on("zoom", (e) => root.attr("transform", e.transform)); svg.call(zoom);
const gH = root.append("g"), gL = root.append("g"), gN = root.append("g"), gT = root.append("g");
const nodes = D.nodes.map((d, i) => ({ ...d, i, shown: 0 })), links = D.links.map((l) => ({ ...l, source: l.s, target: l.t }));
// clade centres on an ellipse, "other" in the middle
const nC = D.clades.length, cx = (k) => W() / 2 + (k < 0 ? 0 : Math.cos(2 * Math.PI * k / nC - 1.3) * W() * 0.36), cy = (k) => H() / 2 + (k < 0 ? 0 : Math.sin(2 * Math.PI * k / nC - 1.3) * H() * 0.36);
const ci = (d) => (CL.get(d.c) || { i: -1 }).i;
const wmax = d3.max(links, (l) => l.w) || 1;
const link = gL.selectAll("line").data(links).join("line").attr("stroke", "#9ca3af").attr("stroke-opacity", (l) => l.s === l.t ? 0 : 0.22).attr("stroke-width", (l) => 0.5 + 2.2 * Math.sqrt(l.w / wmax));
const isWrong = (d) => d.t != null && (d.t === 1) !== (d.p >= D.thr);
const node = gN.selectAll("circle").data(nodes).join("circle").attr("r", 6).attr("fill", "#e5e7eb")
  .attr("stroke", (d) => isWrong(d) ? "#f59e0b" : "#fff").attr("stroke-width", (d) => isWrong(d) ? 3 : 1.3).style("cursor", "pointer");
const tip = document.getElementById("tip");
node.on("mousemove", (e, d) => { tip.style.opacity = 1; tip.style.left = e.offsetX + 14 + "px"; tip.style.top = e.offsetY + 12 + "px";
  tip.textContent = d.shown ? `BOT ${d.p.toFixed(1)}%${d.t != null ? " · truly " + (d.t ? "bot" : "human") : ""}` : "not scored yet — press Run detector"; })
  .on("mouseleave", () => tip.style.opacity = 0).on("click", (e, d) => { e.stopPropagation(); decide(d); });
const sim = d3.forceSimulation(nodes)
  .force("link", d3.forceLink(links).distance(18).strength((l) => l.source.c === l.target.c ? 0.08 : 0))
  .force("charge", d3.forceManyBody().strength(-14))
  .force("x", d3.forceX((d) => cx(ci(d))).strength(0.22)).force("y", d3.forceY((d) => cy(ci(d))).strength(0.22))
  .force("collide", d3.forceCollide((d) => (d.shown ? R(d.p) : 6) + 1.6))
  .on("tick", tick).on("end", fit);
function tick() {
  link.attr("x1", (l) => l.source.x).attr("y1", (l) => l.source.y).attr("x2", (l) => l.target.x).attr("y2", (l) => l.target.y);
  node.attr("cx", (d) => d.x).attr("cy", (d) => d.y); hulls();
}
const hullSel = () => gH.selectAll("path").data(D.clades, (c) => c.id);
function hulls() {
  const pts = d3.group(nodes.filter((d) => d.c >= 0), (d) => d.c);
  gH.selectAll("path").data(D.clades, (c) => c.id).join("path").attr("class", "hull")
    .attr("fill", (c) => c.bot > 0.5 ? "#dc262608" : "#05966908").attr("stroke", (c) => c.bot > 0.5 ? "#dc262640" : "#05966940")
    .attr("d", (c) => { const ps = (pts.get(c.id) || []).map((d) => [d.x, d.y]); if (ps.length < 3) return null;
      const h = d3.polygonHull(ps.flatMap(([x, y]) => [[x - 14, y - 14], [x + 14, y - 14], [x - 14, y + 14], [x + 14, y + 14]])); return h ? "M" + h.join("L") + "Z" : null; })
    .style("cursor", "pointer").on("click", (e, c) => { e.stopPropagation(); cladeView(c); });
  gT.selectAll("g").data(D.clades, (c) => c.id).join((en) => { const g = en.append("g"); g.append("text").attr("class", "hlabel"); g.append("text").attr("class", "hsub").attr("dy", 15); return g; })
    .attr("transform", (c) => { const ps = pts.get(c.id) || []; if (!ps.length) return "translate(-999,-999)";
      return `translate(${d3.mean(ps, (d) => d.x)},${d3.min(ps, (d) => d.y) - 26})`; })
    .call((g) => { g.select(".hlabel").attr("text-anchor", "middle").text((c) => c.name);
      g.select(".hsub").attr("text-anchor", "middle").text((c) => `${c.n} accounts · ${c.bot != null ? Math.round(100 * c.bot) + "% BOT" : ""}${c.true != null ? " · true " + Math.round(100 * c.true) + "%" : ""}`); });
}
function fit() { const b = root.node().getBBox(); if (!b.width) return; const k = Math.min(W() / (b.width + 80), H() / (b.height + 80), 1.4);
  svg.transition().duration(700).call(zoom.transform, d3.zoomIdentity.translate(W() / 2 - k * (b.x + b.width / 2), H() / 2 - k * (b.y + b.height / 2)).scale(k)); }

// ---- run: the model scores every account, lowest first, colour + size animate in
const st = document.getElementById("status");
document.getElementById("run").onclick = run;
function run() {
  const btn = document.getElementById("run"); btn.disabled = true; st.style.opacity = 1;
  nodes.forEach((d) => d.shown = 0); node.attr("fill", "#e5e7eb").attr("r", 6);
  const order = d3.shuffle(nodes.slice()); let k = 0, flagged = 0; const per = Math.ceil(order.length / 70);
  const timer = d3.interval(() => {
    for (let j = 0; j < per && k < order.length; j++, k++) { const d = order[k]; d.shown = 1; if (d.p >= D.thr) flagged++;
      d3.select(node.nodes()[d.i]).transition().duration(420).attr("fill", shade(d.p)).attr("r", R(d.p)); }
    document.getElementById("flag").textContent = Math.round(flagged / D.nodes.length * S.bots).toLocaleString();
    st.textContent = `Scoring account ${Math.min(k, order.length)} of ${order.length} · indicator model + graph network → calibrated BOT %`;
    if (k >= order.length) { timer.stop(); document.getElementById("flag").textContent = S.bots.toLocaleString();
      st.textContent = `Done · ${S.bots.toLocaleString()} of ${S.n.toLocaleString()} flagged · ${S.wrong} wrong against the true labels`; setTimeout(() => st.style.opacity = 0, 2600);
      sim.alpha(0.3).restart(); btn.disabled = false; btn.textContent = "↻ Run again"; }
  }, 40);
}

// ---- right panel
const side = document.getElementById("side");
function overview() {
  node.attr("opacity", 1); link.attr("stroke-opacity", 0.22);
  const g = Object.entries(S.gates), gm = d3.max(g, (x) => x[1]) || 1, nm = { COPY: "Copy-paste", SYNC: "Synchronised posting", REPLY: "Replies", LINK: "Shared links", CO_THREAD: "Same thread, same minutes" };
  side.innerHTML = `<h2>One question: is this account a bot?</h2>
  <p class="muted">Press <b>Run detector</b> to watch the model score every account. Click any account to open its decision. Click a cluster to see what it pushes.</p>
  <h3>How a decision is made</h3>${pipeline()}
  <h3>Learned relation weights · graph network</h3>
  ${g.map(([k, v]) => `<div style="display:flex;justify-content:space-between;font-size:12.5px;margin:6px 0 4px"><span>${nm[k] || k}</span><span class="mono">${v.toFixed(2)}</span></div><div class="bar"><i style="width:${v / gm * 100}%;background:#111827"></i></div>`).join("")}
  <h3>Motive clades</h3>
  ${D.clades.map((c) => `<div class="clade" data-c="${c.id}"><span class="sw" style="background:${shade(100 * (c.bot ?? 0))}"></span><span class="nm">${c.name}</span><span class="mono" style="color:var(--mut)">${c.n} · ${Math.round(100 * (c.bot ?? 0))}%</span></div>`).join("")}`;
  side.querySelectorAll(".clade").forEach((el) => el.onclick = () => cladeView(D.clades.find((c) => c.id == el.dataset.c)));
}
function pipeline(d) {
  const box = (x, y, w, t, s, col) => `<rect x="${x}" y="${y}" width="${w}" height="44" rx="9" fill="#fff" stroke="${col || "#d1d5db"}"/><text x="${x + w / 2}" y="${y + 19}" text-anchor="middle" font-size="11.5" font-weight="600" fill="#111827">${t}</text><text x="${x + w / 2}" y="${y + 34}" text-anchor="middle" font-size="11" fill="#6b7280" font-family="JetBrains Mono">${s}</text>`;
  const arr = (x1, y1, x2, y2) => `<path d="M${x1} ${y1} C ${(x1 + x2) / 2} ${y1}, ${(x1 + x2) / 2} ${y2}, ${x2} ${y2}" fill="none" stroke="#9ca3af" stroke-width="1.4" marker-end="url(#ah)"/>`;
  return `<svg viewBox="0 0 352 128" width="100%"><defs><marker id="ah" viewBox="0 0 8 8" refX="7" refY="4" markerWidth="7" markerHeight="7" orient="auto"><path d="M0 0L8 4L0 8z" fill="#9ca3af"/></marker></defs>
    ${box(0, 6, 112, "Indicators", d ? d.ind.length + " active" : "30 per account")}${box(0, 74, 112, "Coordination graph", d ? d.nb + " neighbours" : "5 relation types")}
    ${box(132, 6, 98, "Indicator model", d ? d.lgb.toFixed(1) + "%" : "LightGBM")}${box(132, 74, 98, "Graph network", d ? d.gnn.toFixed(1) + "%" : "relational GNN")}
    ${box(252, 40, 100, "Calibrated", d ? "BOT " + d.p.toFixed(1) + "%" : "BOT %", d ? shade(d.p) : "#111827")}
    ${arr(112, 28, 130, 28)}${arr(112, 96, 130, 96)}${arr(230, 28, 250, 56)}${arr(230, 96, 250, 70)}</svg>`;
}
function decide(d) {
  const nb = new Set([d.i]); links.forEach((l) => { if (l.source === d) nb.add(l.target.i); if (l.target === d) nb.add(l.source.i); });
  node.attr("opacity", (n) => nb.has(n.i) ? 1 : 0.18); link.attr("stroke-opacity", (l) => l.source === d || l.target === d ? 0.75 : 0.05);
  const bot = d.p >= D.thr, wrong = isWrong(d), c = CL.get(d.c);
  const ind = d.ind.slice(0, 10), mx = d3.max(ind, (x) => Math.abs(x[1])) || 1, H2 = ind.length * 25 + 10;
  // decision network: indicator nodes → Σ (indicator model) → BOT, line width = learned share
  const net = `<svg viewBox="0 0 352 ${H2}" width="100%">${ind.map(([n, v], k) => { const y = 14 + k * 25, w = 0.8 + 5 * Math.abs(v) / mx, col = v > 0 ? "#dc2626" : "#059669";
      return `<path d="M168 ${y} C 230 ${y}, 240 ${H2 / 2}, 292 ${H2 / 2}" fill="none" stroke="${col}" stroke-opacity=".75" stroke-width="${w}"><animate attributeName="stroke-dasharray" values="0 400;400 0" dur=".8s" fill="freeze"/></path>
      <circle cx="168" cy="${y}" r="4.5" fill="${col}"/><text x="160" y="${y + 4}" text-anchor="end" font-size="10.5" fill="#374151">${n.length > 30 ? n.slice(0, 29) + "…" : n}</text>
      <text x="176" y="${y - 4}" font-size="9.5" fill="${col}" font-family="JetBrains Mono">${v > 0 ? "+" : "−"}${Math.abs(v).toFixed(1)}%</text>`; }).join("")}
    <circle cx="318" cy="${H2 / 2}" r="26" fill="${shade(d.p)}" stroke="#fff" stroke-width="3"/><text x="318" y="${H2 / 2 + 4}" text-anchor="middle" font-size="12" font-weight="800" fill="${d.p > 45 ? "#fff" : "#111827"}">${Math.round(d.p)}%</text></svg>`;
  side.innerHTML = `<div class="muted">Account · held-out, never seen in training</div><div class="mono" style="font-size:14px;font-weight:600">${d.id}</div>
  <div style="margin-top:14px"><span class="big" style="color:${shade(Math.max(d.p, 58))}">${d.p.toFixed(1)}%</span><span class="pill" style="background:${bot ? "#fee2e2" : "#d1fae5"};color:${bot ? "#991b1b" : "#065f46"}">${bot ? "BOT" : "NOT BOT"}</span></div>
  <div class="muted" style="margin-top:6px">BOT probability${d.t != null ? ` · true label <b style="color:#111827">${d.t ? "bot" : "human"}</b> · ${wrong ? '<b style="color:#b45309">model wrong</b>' : '<b style="color:#047857">correct</b>'}` : ""}${c ? ` · clade <b style="color:#111827">${c.name}</b>` : ""}</div>
  <h3>The decision</h3>${pipeline(d)}
  <h3>Why · each indicator's share (learned)</h3>${net}
  <div class="muted">Red pushes towards bot, green towards human; line width = share of the decision. ${d.nb ? `Graph: ${d.nb} coordination neighbours, average neighbour BOT ${d.nbp.toFixed(0)}%.` : "No coordination neighbours."}</div>
  <p style="margin-top:16px"><a href="#" id="back" style="color:#111827;font-weight:600">← overview</a></p>`;
  document.getElementById("back").onclick = (e) => { e.preventDefault(); overview(); };
}
function cladeView(c) {
  node.attr("opacity", (n) => n.c === c.id ? 1 : 0.15); link.attr("stroke-opacity", (l) => l.source.c === c.id && l.target.c === c.id ? 0.6 : 0.04);
  side.innerHTML = `<div class="muted">Motive clade</div><h2 style="font-size:18px">${c.name}</h2>
  <div style="display:flex;gap:26px;margin:14px 0"><div><div class="big" style="font-size:32px">${c.n}</div><div class="muted">accounts</div></div>
  <div><div class="big" style="font-size:32px;color:${shade(100 * (c.bot ?? 0))}">${Math.round(100 * (c.bot ?? 0))}%</div><div class="muted">flagged BOT</div></div>
  ${c.true != null ? `<div><div class="big" style="font-size:32px">${Math.round(100 * c.true)}%</div><div class="muted">truly bots</div></div>` : ""}</div>
  <h3>Typical copied post</h3><div style="font-size:13px;background:#f9fafb;border:1px solid var(--line);border-radius:9px;padding:10px 12px">${(c.ex || "").replace(/</g, "&lt;")}</div>
  <h3>Aimed at</h3>${c.targets.map((t) => `<div style="font-size:12.5px;margin:4px 0" class="mono">${t.replace(/</g, "&lt;")}</div>`).join("")}
  <p class="muted" style="margin-top:14px">The motive only groups accounts. The verdict on each account is its own BOT %.</p>
  <p><a href="#" id="back" style="color:#111827;font-weight:600">← overview</a></p>`;
  document.getElementById("back").onclick = (e) => { e.preventDefault(); overview(); };
}
svg.on("click", () => overview());
overview(); setTimeout(run, 900);
</script></body></html>
"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(HERE.parent / "frontend" / "graph.html"))
    ap.add_argument("--data", default="cresci2017")
    a = ap.parse_args()
    print(build(Path(a.out), a.data))


if __name__ == "__main__":
    main()
