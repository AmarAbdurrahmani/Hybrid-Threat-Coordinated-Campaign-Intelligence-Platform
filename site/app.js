/* HTCCIP site: summary sections + a Neo4j Browser–style graph explorer (d3-force, SVG). */
(async function () {
  "use strict";
  const [S, G] = await Promise.all([fetch("data/summary.json").then(r => r.json()), fetch("data/graph.json").then(r => r.json())]);
  const fmt = n => typeof n === "number" ? n.toLocaleString("en-US") : n;
  const esc = s => String(s).replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const $ = id => document.getElementById(id);

  /* ---------- summary sections ---------- */
  $("kpis").innerHTML = S.kpis.map(k => `<div class="kpi"><b>${fmt(k.value)}</b><span>${esc(k.label)}</span></div>`).join("");
  $("pipe").innerHTML = S.pipeline.map(p => `<li><div class="st">${esc(p.stage)}</div><h3>${esc(p.title)}</h3>
    <div class="big">${fmt(p.value)}</div><p>${esc(p.unit)}</p><p>${p.detail}</p></li>`).join("");

  const M = S.model;
  $("modelsub").textContent = `LightGBM over ${M.n_features} account indicators plus a relational graph neural network, calibrated, trained on ${fmt(M.n_accounts)} human-labelled accounts (${M.dataset}).`;
  const wmax = Math.max(...M.weights.map(w => w.pct));
  $("weights").innerHTML = M.weights.map(w => `<div class="bar"><span class="t" title="${esc(w.name)}">${esc(w.name)}</span><span class="v">${w.pct.toFixed(1)}%</span>
    <div class="track"><div class="fill" style="width:${(w.pct / wmax * 100).toFixed(1)}%"></div></div></div>`).join("");
  $("bench").innerHTML = M.bench.map(([k, v]) => `<tr><td>${esc(k)}</td><td>${esc(v)}</td></tr>`).join("");
  const gmax = Math.max(...M.gates.map(g => g.weight));
  $("gates").innerHTML = M.gates.map(g => `<div class="bar"><span class="t">${esc(g.relation)}${g.trained ? "" : " (no training edges)"}</span><span class="v">${g.weight.toFixed(3)}</span>
    <div class="track"><div class="fill" style="width:${(g.weight / gmax * 100).toFixed(1)}%;background:#a5abb6"></div></div></div>`).join("");
  $("transfer").textContent = M.transfer;

  const C = S.checks;
  const nPass = C.groups.flatMap(g => g.items).filter(i => i.status === "PASS").length;
  const nAll = C.groups.flatMap(g => g.items).length;
  $("checksub").textContent = `${nPass} of ${nAll} checks pass. Run by checks.py against the database and every published file, ${C.generated}.`;
  $("checklist").innerHTML = C.groups.map(g => `<div class="card"><h3>${esc(g.name)}<span class="note">${g.items.filter(i => i.status === "PASS").length}/${g.items.length}</span></h3>
    ${g.items.map(i => `<div class="chk"><span class="s ${i.status}">${i.status}</span><div><b>${esc(i.name)}</b><span>${esc(i.detail)}</span></div></div>`).join("")}</div>`).join("");

  /* ---------- graph store ---------- */
  const nodeById = new Map(G.nodes.map(n => [n.id, n]));
  const relsOf = new Map();
  for (const r of G.rels) for (const k of [r.start, r.end]) (relsOf.get(k) || relsOf.set(k, []).get(k)).push(r);

  const STYLE = {   // Neo4j Browser default palette
    Flagged:   { color: "#F16667", text: "#fff", r: 24 },
    Account:   { color: "#4C8EDA", text: "#fff", r: 22 },
    Video:     { color: "#FFC454", text: "#4a3b0b", r: 32 },
    Indicator: { color: "#8DCC93", text: "#163a1b", r: 34 },
    Verdict:   { color: "#C990C0", text: "#fff", r: 46 },
  };
  const styleOf = n => STYLE[n.labels.includes("Flagged") ? "Flagged" : n.labels[0]] || STYLE.Account;
  const caption = n => {
    const p = n.props;
    if (n.labels[0] === "Account") return p.account_id.slice(-6);
    if (n.labels[0] === "Video") return p.title;
    return p.name;
  };

  const cy = q => esc(q)
    .replace(/\b(MATCH|OPTIONAL MATCH|WHERE|RETURN|LIMIT|ORDER BY|DESC|AND|OR|WITH)\b/g, '<span class="kw">$1</span>')
    .replace(/(:[A-Z][A-Za-z_|]*)/g, '<span class="lb">$1</span>')
    .replace(/(&#39;[^&]*&#39;)/g, '<span class="st">$1</span>');

  const accounts = G.nodes.filter(n => n.labels[0] === "Account");
  const sub = (nodeIds, relFilter, cap = 160) => {
    const ids = new Set([...nodeIds].slice(0, cap));
    const rels = G.rels.filter(r => ids.has(r.start) && ids.has(r.end) && relFilter(r));
    return { nodes: [...ids].map(i => nodeById.get(i)), rels };
  };
  const neighboursVia = (types, seed) => {
    const out = new Set(seed);
    for (const id of seed) for (const r of relsOf.get(id) || []) if (types.includes(r.type)) { out.add(r.start); out.add(r.end); }
    return out;
  };
  const COORD = ["COPY", "SYNC", "CO_THREAD"];

  const QUERIES = [
    { name: "What the model weighs", cypher: "MATCH (i:Indicator)-[w:WEIGHT]->(v:Verdict {name:'BOT'}) RETURN i, w, v",
      run: () => sub(G.nodes.filter(n => n.labels[0] === "Indicator" || n.labels[0] === "Verdict").map(n => n.id), r => r.type === "WEIGHT") },
    { name: "Largest coordinated cluster", cypher: "MATCH (a:Account {cluster: 1})-[r:COPY|SYNC|CO_THREAD|POSTED_ON]-(x) RETURN a, r, x",
      run: () => { const seed = accounts.filter(n => n.props.cluster === 1).map(n => n.id);
        return sub(neighboursVia(["POSTED_ON"], seed), r => COORD.includes(r.type) || r.type === "POSTED_ON"); } },
    { name: "Accounts that copied text", cypher: "MATCH p=(a:Account)-[:COPY]-(b:Account) RETURN p LIMIT 400",
      run: () => { const ids = new Set(); G.rels.filter(r => r.type === "COPY").forEach(r => { ids.add(r.start); ids.add(r.end); });
        return sub(ids, r => r.type === "COPY"); } },
    { name: "Active together across videos", cypher: "MATCH p=(a:Account)-[:CO_THREAD|SYNC]-(b:Account) RETURN p LIMIT 400",
      run: () => { const ids = new Set(); G.rels.filter(r => r.type === "CO_THREAD" || r.type === "SYNC").forEach(r => { ids.add(r.start); ids.add(r.end); });
        return sub(neighboursVia(["POSTED_ON"], ids), r => ["CO_THREAD", "SYNC", "POSTED_ON"].includes(r.type)); } },
    { name: "Medium-risk accounts and their videos", cypher: "MATCH (a:Account:Flagged)-[r:POSTED_ON]->(v:Video) RETURN a, r, v",
      run: () => { const seed = accounts.filter(n => n.labels.includes("Flagged")).map(n => n.id);
        return sub(neighboursVia(["POSTED_ON"], seed), r => r.type === "POSTED_ON" || COORD.includes(r.type)); } },
    { name: "Videos on alert", cypher: "MATCH (v:Video) WHERE v.level <= 3 OPTIONAL MATCH (a:Account)-[r:POSTED_ON]->(v) WHERE (a)-[:COPY|SYNC|CO_THREAD]-() RETURN v, r, a",
      run: () => { const vids = G.nodes.filter(n => n.labels[0] === "Video" && n.props.level <= 3).map(n => n.id);
        const ids = new Set(vids);
        for (const v of vids) for (const r of relsOf.get(v) || []) if (r.type === "POSTED_ON" && (relsOf.get(r.start) || []).some(x => COORD.includes(x.type))) ids.add(r.start);
        return sub(ids, r => r.type === "POSTED_ON" || COORD.includes(r.type)); } },
  ];

  /* ---------- renderer ---------- */
  const svg = d3.select("#svg");
  const root = svg.append("g");
  const defs = svg.append("defs");
  defs.append("marker").attr("id", "arrow").attr("viewBox", "0 -5 10 10").attr("refX", 10).attr("refY", 0)
    .attr("markerWidth", 7).attr("markerHeight", 7).attr("orient", "auto").append("path").attr("d", "M0,-5L10,0L0,5").attr("fill", "#a5abb6");
  const zoom = d3.zoom().scaleExtent([0.1, 4]).on("zoom", e => root.attr("transform", e.transform));
  svg.call(zoom).on("dblclick.zoom", null);
  svg.on("click", e => { if (e.target === svg.node()) select(null); });

  let sim, current, hidden = new Set(), selected = null;
  const relW = r => r.type === "WEIGHT" ? 1 + r.props.importance_pct / 3 : 1.6;

  function draw() {
    const vis = n => !n.labels.some(l => hidden.has(l));
    const nodes = current.nodes.filter(vis).map(n => ({ ...n }));
    const ids = new Set(nodes.map(n => n.id));
    const rels = current.rels.filter(r => ids.has(r.start) && ids.has(r.end) && !hidden.has(r.type)).map(r => ({ ...r, source: r.start, target: r.end }));
    root.selectAll("*").remove();
    if (sim) sim.stop();

    const link = root.append("g").selectAll("g").data(rels).join("g").attr("class", "rel").style("cursor", "pointer")
      .on("click", (e, d) => { e.stopPropagation(); select(d, "rel"); });
    link.append("path").attr("stroke", "#a5abb6").attr("stroke-width", relW).attr("fill", "none").attr("marker-end", "url(#arrow)");
    link.append("path").attr("class", "hit").attr("stroke", "transparent").attr("stroke-width", 10).attr("fill", "none");
    const lbl = link.append("g");
    lbl.append("rect").attr("class", "rel-label-bg");
    lbl.append("text").attr("class", "rel-label").attr("text-anchor", "middle").attr("dy", "0.32em")
      .text(d => d.type === "WEIGHT" ? `WEIGHT ${d.props.importance_pct.toFixed(1)}%` : d.type);
    lbl.each(function () { const t = this.querySelector("text").getBBox(); d3.select(this).select("rect").attr("x", -t.width / 2 - 2).attr("y", -6).attr("width", t.width + 4).attr("height", 12); });

    const node = root.append("g").selectAll("g").data(nodes).join("g").attr("class", "node").style("cursor", "pointer")
      .on("click", (e, d) => { e.stopPropagation(); select(d, "node"); })
      .call(d3.drag().on("start", (e, d) => { if (!e.active) sim.alphaTarget(0.2).restart(); d.fx = d.x; d.fy = d.y; })
        .on("drag", (e, d) => { d.fx = e.x; d.fy = e.y; }).on("end", (e) => { if (!e.active) sim.alphaTarget(0); }));
    node.append("circle").attr("class", "ring").attr("r", d => styleOf(d).r + 4).attr("fill", "none").attr("stroke", "none");
    node.append("circle").attr("r", d => styleOf(d).r).attr("fill", d => styleOf(d).color).attr("stroke", d => d3.color(styleOf(d).color).darker(0.6));
    node.each(function (d) {
      const st = styleOf(d), words = wrap(caption(d), Math.max(6, Math.floor(st.r / 3.4)));
      const fs = d.labels[0] === "Account" ? 10 : 10.5;
      const t = d3.select(this).append("text").attr("text-anchor", "middle").attr("fill", st.text).style("font-size", fs + "px");
      words.forEach((w, i) => t.append("tspan").attr("x", 0).attr("y", (i - (words.length - 1) / 2) * (fs + 1.5)).attr("dy", "0.35em").text(w));
    });

    sim = d3.forceSimulation(nodes)
      .force("link", d3.forceLink(rels).id(d => d.id).distance(d => d.type === "WEIGHT" ? 150 : d.type === "POSTED_ON" ? 110 : 95).strength(0.6))
      .force("charge", d3.forceManyBody().strength(d => d.labels[0] === "Verdict" ? -1400 : -320))
      .force("collide", d3.forceCollide(d => styleOf(d).r + 8))
      .force("x", d3.forceX().strength(0.04)).force("y", d3.forceY().strength(0.04))
      .on("tick", tick);
    let fitted = false;
    sim.on("end", () => { if (!fitted) { fit(); fitted = true; } });
    setTimeout(() => { if (!fitted) { fit(); fitted = true; } }, 1800);

    function tick() {
      link.each(function (d) {
        const dx = d.target.x - d.source.x, dy = d.target.y - d.source.y, len = Math.hypot(dx, dy) || 1;
        const rs = styleOf(d.source).r, rt = styleOf(d.target).r + 3;
        const sx = d.source.x + dx / len * rs, sy = d.source.y + dy / len * rs, tx = d.target.x - dx / len * rt, ty = d.target.y - dy / len * rt;
        const p = `M${sx},${sy}L${tx},${ty}`;
        d3.select(this).selectAll("path").attr("d", p);
        let a = Math.atan2(dy, dx) * 180 / Math.PI; if (a > 90 || a < -90) a += 180;
        d3.select(this).select("g").attr("transform", `translate(${(sx + tx) / 2},${(sy + ty) / 2}) rotate(${a})`);
      });
      node.attr("transform", d => `translate(${d.x},${d.y})`);
    }
    $("foot").textContent = `Displaying ${nodes.length} nodes, ${rels.length} relationships.`;
    overview(nodes, rels);
    renderTable(nodes, rels);
  }

  function wrap(s, n) {
    const words = String(s).split(/\s+/), out = [];
    let line = "";
    for (const w of words) { if ((line + " " + w).trim().length > n && line) { out.push(line); line = w; } else line = (line + " " + w).trim(); }
    if (line) out.push(line);
    const lines = out.slice(0, 3);
    if (out.length > 3) lines[2] = lines[2].slice(0, n - 1) + "…";
    return lines.map(l => l.length > n + 3 ? l.slice(0, n + 2) + "…" : l);
  }

  function fit() {
    const b = root.node().getBBox(), w = svg.node().clientWidth, h = svg.node().clientHeight;
    if (!b.width) return;
    const k = Math.min(1.6, 0.9 / Math.max(b.width / w, b.height / h));
    svg.transition().duration(500).call(zoom.transform, d3.zoomIdentity.translate(w / 2 - k * (b.x + b.width / 2), h / 2 - k * (b.y + b.height / 2)).scale(k));
  }

  function countBy(arr, f) { const m = new Map(); arr.forEach(x => f(x).forEach(k => m.set(k, (m.get(k) || 0) + 1))); return m; }
  function overview(nodes, rels) {
    const all = current;
    const lab = countBy(all.nodes, n => n.labels), typ = countBy(all.rels, r => [r.type]);
    $("overview").innerHTML = `<div class="chips"><span class="chip all">*(${all.nodes.length})</span>${[...lab].map(([l, c]) =>
      `<button class="chip ${hidden.has(l) ? "off" : ""}" data-k="${l}" style="background:${(STYLE[l] || STYLE.Account).color};color:${(STYLE[l] || STYLE.Account).text}">${l} (${fmt(c)})</button>`).join("")}</div>
      <div class="chips"><span class="chip rel" style="background:#7b8494">*(${all.rels.length})</span>${[...typ].map(([t, c]) =>
      `<button class="chip rel ${hidden.has(t) ? "off" : ""}" data-k="${t}">${t} (${fmt(c)})</button>`).join("")}</div>`;
    $("overview").querySelectorAll("button").forEach(b => b.onclick = () => { const k = b.dataset.k; hidden.has(k) ? hidden.delete(k) : hidden.add(k); draw(); });
    if (!selected) showOverviewProps();
  }

  function showOverviewProps() {
    $("props").innerHTML = `<h4>Overview</h4><p class="empty">${esc(current.q.name)}. Click a node or relationship to see its properties.</p>
      <table>${current.q.notes.map(([k, v]) => `<tr><td>${esc(k)}</td><td>${esc(v)}</td></tr>`).join("")}</table>`;
  }

  function select(d, kind) {
    selected = d;
    root.selectAll(".node").classed("sel", n => d && kind === "node" && n.id === d.id);
    root.selectAll(".rel").classed("sel", r => d && kind === "rel" && r.id === d.id);
    if (!d) return showOverviewProps();
    if (kind === "node") {
      const chips = d.labels.map(l => `<span class="chip" style="background:${(STYLE[l] || STYLE.Account).color};color:${(STYLE[l] || STYLE.Account).text}">${l}</span>`).join("");
      $("props").innerHTML = `<h4>Node properties ${chips}</h4><table><tr><td>&lt;id&gt;</td><td>${esc(d.id)}</td></tr>${Object.entries(d.props).map(([k, v]) =>
        `<tr><td>${esc(k)}</td><td>${esc(Array.isArray(v) ? v.join(" · ") : v)}</td></tr>`).join("")}</table>`;
    } else {
      $("props").innerHTML = `<h4>Relationship properties <span class="chip rel">${esc(d.type)}</span></h4><table>
        <tr><td>&lt;id&gt;</td><td>${esc(d.id)}</td></tr><tr><td>from</td><td>${esc(d.start)}</td></tr><tr><td>to</td><td>${esc(d.end)}</td></tr>
        ${Object.entries(d.props).map(([k, v]) => `<tr><td>${esc(k)}</td><td>${esc(v)}</td></tr>`).join("")}</table>`;
    }
  }

  function renderTable(nodes, rels) {
    const keys = [...new Set(nodes.flatMap(n => Object.keys(n.props)))].slice(0, 9);
    $("tableview").innerHTML = `<table><thead><tr><th>labels</th>${keys.map(k => `<th>${esc(k)}</th>`).join("")}</tr></thead><tbody>${
      nodes.slice(0, 500).map(n => `<tr><td>${n.labels.join(":")}</td>${keys.map(k => `<td>${esc(n.props[k] ?? "")}</td>`).join("")}</tr>`).join("")}</tbody></table>`;
    $("jsonview").textContent = JSON.stringify({ nodes: nodes.slice(0, 60).map(({ id, labels, props }) => ({ id, labels, props })),
      relationships: rels.slice(0, 60).map(({ id, type, start, end, props }) => ({ id, type, start, end, props })) }, null, 2);
  }

  document.querySelectorAll(".vt").forEach(b => b.onclick = () => {
    document.querySelectorAll(".vt").forEach(x => x.classList.toggle("on", x === b));
    const v = b.dataset.view;
    $("svg").style.visibility = v === "graph" ? "visible" : "hidden";
    $("overview").hidden = $("props").hidden = v !== "graph";
    $("tableview").hidden = v !== "table"; $("jsonview").hidden = v !== "json";
  });

  function run(i) {
    const q = QUERIES[i], t0 = performance.now();
    const res = q.run();
    current = { ...res, q: { name: q.name, notes: [["nodes", fmt(res.nodes.length)], ["relationships", fmt(res.rels.length)]] } };
    hidden = new Set(); selected = null;
    $("cypher").innerHTML = cy(q.cypher);
    document.querySelectorAll(".qbtn").forEach((b, j) => b.classList.toggle("on", j === i));
    draw();
    $("took").textContent = `${Math.max(1, Math.round(performance.now() - t0))} ms`;
  }
  $("qlist").innerHTML = QUERIES.map((q, i) => `<button class="qbtn" data-i="${i}">${esc(q.name)}<small>${esc(q.cypher)}</small></button>`).join("");
  $("qlist").querySelectorAll(".qbtn").forEach(b => b.onclick = () => run(+b.dataset.i));
  run(0);
})();
