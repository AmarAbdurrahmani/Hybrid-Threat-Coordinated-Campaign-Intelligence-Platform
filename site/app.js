/* HTCCIP console: bot-family map, dossier with the family's real network, top accounts, model, checks. */
(async function () {
  "use strict";
  const S = await fetch("data/summary.json").then(r => r.json());
  const fmt = n => typeof n === "number" ? n.toLocaleString("en-US") : n;
  const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const $ = id => document.getElementById(id);
  const COL = { contests_independence: "#ff6b4a", supports_independence: "#4c9aff", religion: "#b07cff", nato: "#2ec4a6", russia: "#ff4d6d",
    war: "#ffa94d", albania: "#e05cc5", eu_us: "#38c6f4", politicians: "#f2c94c", none: "#7f8ba3" };
  const ICON = { contests_independence: "🗺️", supports_independence: "🏛️", religion: "⛪", nato: "🛡️", russia: "🐻", war: "⚔️", albania: "🦅", eu_us: "🌍", politicians: "👤", none: "💬" };
  const SHORT = { contests_independence: "“Kosovo is Serbia”", supports_independence: "Pro-independence", religion: "Religion", nato: "NATO / KFOR", russia: "Russia",
    war: "War & violence", albania: "Albania", eu_us: "EU / USA", politicians: "Leaders", none: "Other copied lines" };
  const barRows = (rows, unit = "", color) => { const max = Math.max(...rows.map(r => r.value), 1);
    return rows.map(r => `<div class="bar-row"><span class="t" title="${esc(r.label)}">${esc(r.label)}</span><span class="v">${fmt(r.value)}${unit}</span>
      <div class="track"><div class="fill" style="width:${(r.value / max * 100).toFixed(1)}%${color ? `;background:${color}` : ""}"></div></div></div>`).join(""); };
  const bars = (rows, unit, color) => `<div class="bars">${barRows(rows, unit, color)}</div>`;

  /* ---------- header, tabs, findings strip ---------- */
  $("live").innerHTML = `<b>${fmt(S.counts.comments)}</b> real comments · <b>${fmt(S.counts.accounts)}</b> accounts · <b>${S.counts.videos}</b> videos`;
  const VIEWS = ["families", "accounts", "model", "checks"];
  function show(v) {
    if (!VIEWS.includes(v)) v = "families";
    VIEWS.forEach(x => { $("v-" + x).classList.toggle("on", x === v); document.querySelector(`[data-v="${x}"]`).classList.toggle("on", x === v); });
    if (location.hash.slice(1) !== v) history.replaceState(null, "", "#" + v);
  }
  $("tabs").addEventListener("click", e => { const b = e.target.closest("button"); if (b) show(b.dataset.v); });
  window.addEventListener("hashchange", () => show(location.hash.slice(1)));

  const F = S.findings, CF = S.model.confidence || {}, allChecks = (S.checks?.groups || []).flatMap(g => g.items);
  const passed = allChecks.filter(i => i.status === "PASS").length;
  $("strip").innerHTML = [
    [F.groups_3, "bot families found", "3+ linked accounts each", "#4fd1c5"],
    [F.groups_5, "possible bot farms", "5+ accounts · moderate certainty", "#f5b942"],
    [F.moderate, "accounts, moderate evidence", "bursts plus copied text", "#f5b942"],
    [F.weak, "accounts, weak evidence", "one signal only · low certainty", "#4c9aff"],
    [`${CF.accuracy ?? "–"}%`, "model accuracy", `on ${fmt(CF.n || 0)} labelled test accounts`, "#3ddc97"],
    [`${passed}/${allChecks.length}`, "checks passed", "security, sanity, tests", "#3ddc97"],
  ].map(([v, t, d, c]) => `<div class="stat"><b>${fmt(v)}</b><span><i class="dot" style="background:${c}"></i>${t}</span><em>${d}</em></div>`).join("");

  /* ---------- family map ---------- */
  const CL = S.clades || [];
  $("famtitle").textContent = `${fmt(CL.length)} bot families, grouped by topic`;
  const tree = d3.hierarchy({ children: [...d3.group(CL, d => d.key)].map(([k, v]) => ({ key: k, n: v.length, acc: d3.sum(v, x => x.accounts), children: v })) })
    .sum(d => d.accounts ? Math.pow(d.accounts, 0.7) : 0).sort((a, b) => b.value - a.value);
  d3.pack().size([1000, 1000]).padding(d => d.depth === 0 ? 58 : 3)(tree);
  const FULL = [tree.x - tree.r - 10, tree.y - tree.r - 34, 2 * tree.r + 20, 2 * tree.r + 44];
  let vb = FULL;
  const svg = d3.select("#map").attr("viewBox", FULL.join(" ")).attr("preserveAspectRatio", "xMidYMid meet");
  const zoomTo = to => { const i = d3.interpolate(vb, to); vb = to; svg.transition().duration(650).attrTween("viewBox", () => t => i(t).join(" ")); };
  const topicBox = t => [t.x - t.r - 14, t.y - t.r - 40, 2 * t.r + 28, 2 * t.r + 54];
  let zoomed = null;
  function focusTopic(t) {
    zoomed = zoomed === t ? null : t;
    zoomTo(zoomed ? topicBox(zoomed) : FULL);
    document.querySelectorAll(".topic").forEach(el => el.classList.toggle("on", !!zoomed && el.dataset.k === zoomed.data.key));
  }
  svg.on("click", e => { if (e.target === svg.node() && zoomed) focusTopic(zoomed); });

  svg.append("g").selectAll("circle").data(tree.children).join("circle").attr("class", "tcircle")
    .attr("cx", d => d.x).attr("cy", d => d.y).attr("r", d => d.r).attr("fill", d => COL[d.data.key]).attr("fill-opacity", 0.07)
    .attr("stroke", d => COL[d.data.key]).attr("stroke-opacity", 0.6).attr("stroke-width", 1.5).attr("stroke-dasharray", "6 5")
    .on("click", (e, d) => focusTopic(d));
  const fam = svg.append("g").selectAll("g").data(tree.leaves()).join("g").attr("class", "fam").attr("tabindex", 0).attr("role", "button")
    .attr("aria-label", d => `Family ${d.data.gi}: ${SHORT[d.data.key]}, ${d.data.accounts} accounts`).attr("transform", d => `translate(${d.x},${d.y})`)
    .on("click", (e, d) => { e.stopPropagation(); pick(d.data); })
    .on("keydown", (e, d) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); pick(d.data); } });
  fam.append("circle").attr("class", "b").attr("r", d => Math.max(2, d.r - 0.5)).attr("fill", d => COL[d.data.key])
    .attr("fill-opacity", d => 0.4 + 0.6 * Math.min(1, d.data.avg_risk / 45));
  fam.filter(d => d.r > 9).append("text").attr("text-anchor", "middle").attr("dy", "0.35em")
    .style("font-size", d => `${Math.max(9, Math.min(40, d.r * 0.8))}px`).text(d => d.data.accounts);
  fam.append("title").text(d => `Family ${d.data.gi} · ${d.data.accounts} accounts · ${SHORT[d.data.key]}`);
  svg.append("g").selectAll("text").data(tree.children).join("text").attr("class", "tlabel").attr("text-anchor", "middle")
    .attr("x", d => d.x).attr("y", d => d.y - d.r - 12).text(d => `${ICON[d.data.key]} ${SHORT[d.data.key]}`);
  $("topics").innerHTML = tree.children.map(t => `<button class="topic" type="button" data-k="${t.data.key}"><i style="background:${COL[t.data.key]}"></i>${ICON[t.data.key]} ${SHORT[t.data.key]}
    <small>${t.data.n} ${t.data.n === 1 ? "family" : "families"} · ${fmt(t.data.acc)} accounts</small></button>`).join("");
  $("topics").addEventListener("click", e => { const b = e.target.closest(".topic"); if (b) focusTopic(tree.children.find(t => t.data.key === b.dataset.k)); });

  /* ---------- dossier ---------- */
  let G = null, current = null;
  const byCluster = new Map();
  const COORD = new Set(["COPY", "SYNC", "CO_THREAD"]);
  const LCOL = { COPY: "#ff6b6b", CO_THREAD: "#4c9aff", SYNC: "#ffd166" };
  fetch("data/graph.json").then(r => r.json()).then(g => {
    G = g;
    for (const n of g.nodes) if (n.labels[0] === "Account" && n.props.cluster) (byCluster.get(n.props.cluster) || byCluster.set(n.props.cluster, []).get(n.props.cluster)).push(n);
    if (current) network(current);
  });
  function plain(s) {
    let m;
    if ((m = s.match(/(\d+) comments in its 15-min window vs [\d.]+ expected \(×([\d.]+)/))) return `Posted in a burst: ${m[1]} comments in 15 minutes, ${Math.round(+m[2])}× the normal rate`;
    if ((m = s.match(/Near-duplicate of (\d+) comment/))) return `Same text as ${m[1]} other comment${m[1] === "1" ? "" : "s"}`;
    if ((m = s.match(/(\d+) comments within ±2 min/))) return `${m[1]} comments within 2 minutes`;
    if ((m = s.match(/Nearest neighbour only (\d+)s away/))) return `Another comment only ${m[1]} seconds away`;
    if ((m = s.match(/Low follower count \((\d+)\)/))) return `Channel has only ${m[1]} subscribers`;
    return null;
  }
  function pick(d) {
    current = d;
    fam.classed("sel", n => n.data === d);
    const c = COL[d.key];
    const how = [d.copy && `<b>${fmt(d.copy)}</b> pairs of accounts posted the same text`, d.co_thread && `<b>${fmt(d.co_thread)}</b> pairs keep showing up on the same videos together`,
      d.sync && `<b>${fmt(d.sync)}</b> pairs posted within seconds of each other`].filter(Boolean);
    $("dossier").innerHTML = `<div class="d-head"><span class="d-icon" style="background:${c}">${ICON[d.key]}</span><div><h2>Family ${d.gi}</h2><p>${esc(SHORT[d.key])}</p></div></div>
      <p class="d-big"><b>${fmt(d.accounts)}</b>accounts acting together</p>
      <div class="trio"><div><b>${d.avg_risk}<small>/100</small></b><span>avg bot-likeness</span></div><div><b>${fmt(d.flagged)}</b><span>flagged members</span></div><div><b>${fmt(d.videos)}</b><span>videos hit</span></div></div>
      <h3>${d.shared ? `The line ${fmt(d.repeated_by)} of them posted` : "Example comment"}</h3><blockquote class="quote" style="border-color:${c}">“${esc(d.text)}”</blockquote>
      <h3>The family as a network</h3>
      <div class="netbox"><svg id="net"></svg><div class="netkey"><span><i style="background:${LCOL.COPY}"></i>same text</span><span><i style="background:${LCOL.CO_THREAD}"></i>same videos</span><span><i style="background:${LCOL.SYNC}"></i>same seconds</span><span>bigger dot = more bot-like</span></div>
        <div class="nodeinfo" id="nodeinfo">Tap a dot to see why that account stands out.</div></div>
      <h3>Why we link them</h3><ul class="why">${how.map(h => `<li>${h}</li>`).join("")}</ul>
      ${d.themes.length ? `<h3>What they talk about · % of accounts</h3>${bars(d.themes, "%", c)}` : ""}
      <h3>Where and when</h3><p class="where">▶ <a href="https://www.youtube.com/watch?v=${encodeURIComponent(d.video_id)}" target="_blank" rel="noopener noreferrer">${esc(d.video)}</a><br>🕒 ${esc(d.from)} – ${esc(d.to)}</p>
      <p class="fine">Signs of coordination, not proof of who is behind it. A human analyst checks before anyone acts. Accounts are stored as keyed hashes, never names.</p>`;
    $("dossier").scrollTop = 0;
    network(d);
  }
  function network(d) {
    const el = d3.select("#net"); el.selectAll("*").remove();
    const W = 400, H = 250;
    el.attr("viewBox", `0 0 ${W} ${H}`);
    if (!G) { el.append("text").attr("x", W / 2).attr("y", H / 2).attr("text-anchor", "middle").attr("fill", "#8b98b0").style("font-size", "13px").text("Loading network…"); return; }
    const all = (byCluster.get(d.gi) || []).slice().sort((a, b) => b.props.risk - a.props.risk), mem = all.slice(0, 70);
    const ids = new Set(mem.map(n => n.id));
    const nodes = mem.map(n => ({ id: n.id, p: n.props, r: 3.5 + n.props.risk / 9 }));
    const links = G.rels.filter(r => COORD.has(r.type) && ids.has(r.start) && ids.has(r.end)).slice(0, 500).map(r => ({ source: r.start, target: r.end, type: r.type }));
    const sim = d3.forceSimulation(nodes).force("link", d3.forceLink(links).id(n => n.id).distance(26).strength(0.25))
      .force("charge", d3.forceManyBody().strength(-45)).force("x", d3.forceX(W / 2).strength(0.06)).force("y", d3.forceY(H / 2).strength(0.1))
      .force("collide", d3.forceCollide(n => n.r + 1.5)).stop();
    for (let i = 0; i < 220; i++) sim.tick();
    const xs = d3.extent(nodes, n => n.x), ys = d3.extent(nodes, n => n.y), pad = 16;
    el.attr("viewBox", `${xs[0] - pad} ${ys[0] - pad} ${Math.max(60, xs[1] - xs[0] + 2 * pad)} ${Math.max(40, ys[1] - ys[0] + 2 * pad)}`);
    el.append("g").selectAll("line").data(links).join("line").attr("x1", l => l.source.x).attr("y1", l => l.source.y).attr("x2", l => l.target.x).attr("y2", l => l.target.y)
      .attr("stroke", l => LCOL[l.type]).attr("stroke-opacity", 0.45).attr("stroke-width", 0.8);
    const dots = el.append("g").selectAll("circle").data(nodes).join("circle").attr("cx", n => n.x).attr("cy", n => n.y).attr("r", n => n.r)
      .attr("fill", COL[d.key]).attr("stroke", n => n.p.risk >= 35 ? "#fff" : "rgba(255,255,255,.25)").attr("stroke-width", n => n.p.risk >= 35 ? 1.4 : 0.6).style("cursor", "pointer")
      .on("click", (e, n) => {
        dots.attr("opacity", m => m === n ? 1 : 0.45);
        const why = (n.p.evidence || []).map(plain).filter(Boolean).slice(0, 3);
        $("nodeinfo").innerHTML = `<b>One account · bot-likeness ${n.p.risk}/100</b> · ${n.p.comments} comment${n.p.comments === 1 ? "" : "s"} on ${n.p.videos} video${n.p.videos === 1 ? "" : "s"}
          ${why.length ? `<ul>${why.map(w => `<li>${esc(w)}</li>`).join("")}</ul>` : ""}`;
      });
    $("nodeinfo").textContent = all.length > mem.length ? `Showing the ${mem.length} most bot-like of ${fmt(all.length)} accounts. Tap a dot to see why it stands out.` : "Tap a dot to see why that account stands out.";
  }
  if (CL.length) pick([...CL].sort((a, b) => b.accounts - a.accounts)[0]);

  /* ---------- top 10 accounts ---------- */
  const NAME2KEY = { "Kosovo is Serbia": "contests_independence", "Kosova independent": "supports_independence", "Religion": "religion", "NATO": "nato", "Russia": "russia",
    "War": "war", "Albania": "albania", "EU/US": "eu_us", "Leaders": "politicians" };
  $("cards").innerHTML = (S.showcase || []).map((t, i) => { const k = NAME2KEY[t.theme] || "none", c = COL[k];
    return `<article class="card"><div class="c-head"><span class="rank">#${i + 1}</span><span class="d-icon" style="background:${c}">${ICON[k]}</span>
        <div><h2>${esc(t.label)}</h2><p>${esc(SHORT[k])}${t.group ? ` · family ${t.group}` : ""}</p></div>
        <div class="score"><b>${t.risk}</b><span>bot-likeness /100</span></div></div>
      <div class="track"><div class="fill" style="width:${Math.min(100, t.risk)}%;background:${c}"></div></div>
      <blockquote class="quote" style="border-color:${c}">“${esc(t.comment)}”</blockquote>
      <div class="chips">${t.why.map(w => `<span>${esc(w)}</span>`).join("")}</div>
      <p class="where">▶ <a href="https://www.youtube.com/watch?v=${encodeURIComponent(t.video_id || "")}" target="_blank" rel="noopener noreferrer">${esc(t.video)}</a><br>🕒 ${esc(t.date)}</p></article>`; }).join("");

  /* ---------- model ---------- */
  const M = S.model;
  $("modellede").textContent = `One question: is this account a bot? LightGBM over ${M.n_features} indicators plus a graph neural network over the links between accounts, calibrated, trained on ${fmt(M.n_accounts)} human-labelled accounts (${M.dataset}).`;
  $("weights").innerHTML = barRows(M.weights.map(w => ({ label: w.name, value: w.pct })), "%");
  if (CF.rows) {
    $("confhint").textContent = `On ${fmt(CF.n)} held-out accounts with known answers it was right ${fmt(CF.correct)} times (${CF.accuracy}%): ${CF.fp} humans wrongly flagged, ${CF.fn} bots missed.`;
    $("conf").innerHTML = `<tr><th>Model said</th><th>Accounts</th><th>Really bots</th></tr>` + CF.rows.map(r => `<tr><td>${esc(r.band)} bot</td><td>${fmt(r.accounts)}</td><td>${fmt(r.bots)} · ${r.bot_rate}%</td></tr>`).join("");
  }
  $("bench").innerHTML = M.bench.map(([k, v]) => `<tr><td>${esc(k)}</td><td>${esc(v)}</td></tr>`).join("");
  const total = (M.tested || []).reduce((s, r) => s + r[1], 0);
  $("tested").innerHTML = (M.tested || []).map(([k, v]) => `<tr><td>${esc(k)}</td><td>${fmt(v)}</td></tr>`).join("") + `<tr><td><b>Total test evaluations</b></td><td><b>${fmt(total)}</b></td></tr>`;
  if (M.stress?.f1) $("stress").textContent = `Stress test: with every account cut to 3 posts, like a YouTube commenter, F1 is ${M.stress.f1} on ${fmt(M.stress.n)} held-out accounts.`;
  $("transfer").textContent = M.transfer;

  /* ---------- checks ---------- */
  $("checktitle").textContent = `${passed} of ${allChecks.length} checks pass`;
  $("checklede").textContent = `Run by site/checks.py against the database and every published file (${S.checks?.generated || ""}).`;
  $("checklist").innerHTML = (S.checks?.groups || []).map(g => `<div class="panel"><h2>${esc(g.name)} · ${g.items.filter(i => i.status === "PASS").length}/${g.items.length}</h2>
    ${g.items.map(i => `<div class="chk"><span class="s ${i.status}">${i.status}</span><div><b>${esc(i.name)}</b><span>${esc(i.detail)}</span></div></div>`).join("")}</div>`).join("");

  show(location.hash.slice(1));
})();
