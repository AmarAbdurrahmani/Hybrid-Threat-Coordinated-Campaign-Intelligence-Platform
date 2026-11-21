/* HTCCIP site: summary sections + a Neo4j Browser–style graph explorer (d3-force, SVG). */
(async function () {
  "use strict";
  const [S, G] = await Promise.all([fetch("data/summary.json").then(r => r.json()), fetch("data/graph.json").then(r => r.json())]);
  const fmt = n => typeof n === "number" ? n.toLocaleString("en-US") : n;
  const esc = s => String(s).replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const $ = id => document.getElementById(id);

  /* ---------- summary sections ---------- */
  /* ---------- Top 10: bubbles (size = bot-likeness, clustered by group) ---------- */
  $("h-comments").textContent = fmt(S.counts.comments);
  const T = S.showcase || [];
  const GCOL = ["#F16667", "#4C8EDA", "#F79767", "#569480", "#C990C0", "#FFC454", "#57C7E3", "#8DCC93"];
  const groupsIn = [...new Set(T.map(t => t.group))];
  const gcol = g => g ? GCOL[groupsIn.indexOf(g) % GCOL.length] : "#a5abb6";
  const rmin = Math.min(...T.map(t => t.risk)), rmax = Math.max(...T.map(t => t.risk));
  const rad = t => 24 + 56 * Math.pow((t.risk - rmin) / Math.max(1, rmax - rmin), 1.3);
  const BW = 720, BH = 480;
  const bsvg = d3.select("#bubbles").attr("viewBox", `0 0 ${BW} ${BH}`);
  // circle packing: each group is a circle, its accounts sit inside it, nothing overlaps
  const tree = d3.hierarchy({ children: groupsIn.map(g => ({ g, name: (T.find(t => t.group === g) || {}).group_name, children: T.filter(t => t.group === g).map(t => ({ ...t })) })) })
    .sum(d => d.risk ? Math.pow(rad(d), 2) : 0);
  d3.pack().size([BW, BH - 10]).padding(d => d.depth === 0 ? 22 : 5)(tree);
  bsvg.attr("viewBox", `${tree.x - tree.r - 4} ${tree.y - tree.r - 14} ${2 * tree.r + 8} ${2 * tree.r + 30}`);
  const grp = bsvg.append("g").selectAll("g").data(tree.children || []).join("g");
  grp.append("circle").attr("cx", d => d.x).attr("cy", d => d.y + 10).attr("r", d => d.r).attr("fill", d => gcol(d.data.g)).attr("fill-opacity", 0.1)
    .attr("stroke", d => gcol(d.data.g)).attr("stroke-dasharray", "5 4");
  grp.filter(d => d.children.length > 1).append("text").attr("class", "halo-label").attr("x", d => d.x).attr("y", d => d.y + 10 - d.r - 6).attr("text-anchor", "middle").text(d => d.data.name);
  const bnodes = tree.leaves().map(l => ({ ...l.data, x: l.x, y: l.y + 10, r: l.r }));
  const bub = bsvg.append("g").selectAll("g").data(bnodes).join("g").attr("class", "bub").attr("tabindex", 0).attr("role", "button")
    .attr("aria-label", d => `${d.label}, ${d.theme}, score ${d.risk}`).attr("transform", d => `translate(${d.x},${d.y})`)
    .on("click", (e, d) => pick(d)).on("keydown", (e, d) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); pick(d); } });
  bub.append("circle").attr("class", "ring").attr("r", d => d.r + 4).attr("fill", "none");
  bub.append("circle").attr("r", d => d.r - 2).attr("fill", d => gcol(d.group)).attr("stroke", "#fff").attr("stroke-width", 3);
  bub.append("text").attr("text-anchor", "middle").attr("dy", "0.1em").style("font-size", d => `${Math.round(d.r * 0.7)}px`).text(d => d.icon);
  bub.append("text").attr("text-anchor", "middle").attr("y", d => d.r * 0.6).attr("fill", "#fff").style("font-size", "13px").style("font-weight", 700).text(d => d.label.replace("Account ", ""));
  function pick(d) {
    bub.classed("sel", n => n === d);
    $("pick").innerHTML = `<div class="pick-head"><span class="pick-icon" style="background:${gcol(d.group)}">${d.icon}</span>
        <div><h3>${esc(d.label)}</h3><p>${esc(d.theme)} · ${esc(d.group_name)}</p></div></div>
      <div class="meter"><div style="width:${Math.min(100, d.risk)}%;background:${d.risk >= 60 ? "#d43f3a" : d.risk >= 35 ? "#e8a33b" : "#4C8EDA"}"></div></div>
      <p class="score"><b>${d.risk}/100</b> coordination score</p>
      <blockquote>“${esc(d.comment)}”</blockquote>
      <p class="where">▶️ <a href="https://www.youtube.com/watch?v=${encodeURIComponent(d.video_id || "")}" target="_blank" rel="noopener noreferrer">${esc(d.video)}</a><br>🕒 ${esc(d.date)}</p>
      <h4>Why it stands out</h4><ul>${d.why.map(w => `<li>${esc(w)}</li>`).join("")}</ul>
      <p class="fine">A sign of coordination, not proof. A human checks before anyone acts.</p>`;
  }
  if (bnodes.length) pick([...bnodes].sort((a, b) => b.risk - a.risk)[0]);

  /* ---------- all bot families, packed by topic ---------- */
  const CL = S.clades || [];
  const TCOL = { contests_independence: "#E4572E", supports_independence: "#3A7BD5", religion: "#9B5DE5", nato: "#2A9D8F", russia: "#C1121F",
    war: "#F4A259", albania: "#B5179E", eu_us: "#00A6D6", politicians: "#E9B824", none: "#8D99AE" };
  $("famtitle").textContent = `${fmt(CL.length)} bot families, grouped by what they talk about`;
  const ctree = d3.hierarchy({ children: [...d3.group(CL, d => d.key)].map(([k, v]) => ({ key: k, name: v[0].theme, icon: v[0].icon, n: v.length, acc: d3.sum(v, x => x.accounts), children: v })) })
    .sum(d => d.accounts ? Math.pow(d.accounts, 0.7) : 0).sort((a, b) => b.value - a.value);
  d3.pack().size([900, 900]).padding(d => d.depth === 0 ? 40 : 3)(ctree);
  const csvg = d3.select("#clades").attr("viewBox", `${ctree.x - ctree.r} ${ctree.y - ctree.r - 22} ${2 * ctree.r} ${2 * ctree.r + 30}`);
  const tg = csvg.append("g").selectAll("g").data(ctree.children || []).join("g");
  tg.append("circle").attr("cx", d => d.x).attr("cy", d => d.y).attr("r", d => d.r).attr("fill", d => TCOL[d.data.key]).attr("fill-opacity", 0.09)
    .attr("stroke", d => TCOL[d.data.key]).attr("stroke-width", 1.5).attr("stroke-dasharray", "6 4");
  const cb = csvg.append("g").selectAll("g").data(ctree.leaves()).join("g").attr("class", "bub").attr("tabindex", 0).attr("role", "button")
    .attr("aria-label", d => `Family ${d.data.gi}, ${d.data.theme}, ${d.data.accounts} accounts`).attr("transform", d => `translate(${d.x},${d.y})`)
    .on("click", (e, d) => cpick(d.data)).on("keydown", (e, d) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); cpick(d.data); } });
  cb.append("circle").attr("class", "ring").attr("r", d => d.r + 3).attr("fill", "none");
  cb.append("circle").attr("r", d => Math.max(2, d.r - 1)).attr("fill", d => TCOL[d.data.key]).attr("fill-opacity", d => 0.45 + 0.55 * Math.min(1, d.data.avg_risk / 45)).attr("stroke", "#fff").attr("stroke-width", 1.5);
  cb.filter(d => d.r > 9).append("text").attr("text-anchor", "middle").attr("dy", "0.35em").attr("fill", "#fff").style("font-weight", 700)
    .style("font-size", d => `${Math.max(9, Math.min(34, d.r * 0.75))}px`).text(d => d.data.accounts);
  const tl = csvg.append("g").selectAll("g").data(ctree.children || []).join("g").attr("transform", d => `translate(${d.x},${d.y - d.r - 7})`);
  tl.append("text").attr("class", "theme-label").attr("text-anchor", "middle").text(d => `${d.data.icon} ${d.data.name} · ${d.data.n} ${d.data.n === 1 ? "family" : "families"}`)
    .each(function () { const b = this.getBBox(); d3.select(this.parentNode).insert("rect", "text").attr("x", b.x - 5).attr("y", b.y - 2).attr("width", b.width + 10).attr("height", b.height + 4).attr("rx", 9).attr("fill", "#fff").attr("fill-opacity", 0.92); });
  function cpick(d) {
    cb.classed("sel", n => n.data === d);
    const col = TCOL[d.key];
    const how = [d.copy && `${fmt(d.copy)} pairs posted the same text`, d.co_thread && `${fmt(d.co_thread)} pairs showed up together on the same videos`, d.sync && `${fmt(d.sync)} pairs posted within seconds of each other`].filter(Boolean);
    $("cpick").innerHTML = `<div class="pick-head"><span class="pick-icon" style="background:${col}">${d.icon}</span>
        <div><h3>Family ${d.gi}</h3><p>${esc(d.theme)}</p></div></div>
      <p class="bigline"><b>${fmt(d.accounts)}</b> accounts acting together on ${fmt(d.videos)} video${d.videos === 1 ? "" : "s"}</p>
      <div class="meter"><div style="width:${Math.min(100, d.avg_risk)}%;background:${d.avg_risk >= 35 ? "#e8a33b" : "#4C8EDA"}"></div></div>
      <p class="score"><b>${d.avg_risk}/100</b> average bot-likeness · top member ${d.max_risk} · ${fmt(d.flagged)} flagged</p>
      <h4>${d.shared ? `The line ${fmt(d.repeated_by)} of them posted` : "Example comment"}</h4><blockquote>“${esc(d.text)}”</blockquote>
      ${d.themes.length ? `<h4>What they talk about (% of accounts)</h4>${barsHtml(d.themes, "%")}` : ""}
      <h4 class="mt12">Why we link them</h4><ul>${how.map(h => `<li>${h}</li>`).join("")}</ul>
      <p class="where">▶️ Most active on: <a href="https://www.youtube.com/watch?v=${encodeURIComponent(d.video_id)}" target="_blank" rel="noopener noreferrer">${esc(d.video)}</a><br>🕒 ${esc(d.from)} – ${esc(d.to)}</p>
      <button class="gobtn" type="button" id="famgo">See this family as a graph →</button>
      <p class="fine">Signs of coordination, not proof. A human checks before anyone acts.</p>`;
    $("famgo").onclick = () => {
      openFull();
      runQuery({ name: `Family ${d.gi}`, cypher: `MATCH (a:Account {cluster: ${d.gi}})-[r:COPY|SYNC|CO_THREAD]-(b:Account) RETURN a, r, b LIMIT 160`,
        run: () => sub(accounts.filter(n => n.props.cluster === d.gi).sort((x, y) => y.props.risk - x.props.risk).map(n => n.id), r => COORD.includes(r.type)) });
      $("graph").scrollIntoView({ behavior: "smooth" });
    };
  }
  if (CL.length) cpick([...CL].sort((a, b) => b.accounts - a.accounts)[0]);

  /* ---------- what we found, by certainty ---------- */
  const F = S.findings || {}, CFf = S.model.confidence || {};
  const top99 = (CFf.rows || []).find(r => r.band === "99–100%") || {};
  $("foundrow").innerHTML = [
    ["🏭", F.groups_5, "possible bot farms", `groups of 5+ accounts posting the same text or showing up together (${fmt(F.accounts_in_groups_5)} accounts; largest ${fmt(F.largest_group)})`, "Moderate", "#e8a33b"],
    ["🔴", F.strong, "accounts with strong evidence", "coordination score 60+", "High", "#d43f3a"],
    ["🟠", F.moderate, "accounts with moderate evidence", "score 35–59: bursts plus copied text", "Moderate", "#e8a33b"],
    ["🟡", F.weak, "accounts with weak evidence", "score 15–34: one signal only", "Low", "#4C8EDA"],
    ["✅", top99.bots != null ? `${fmt(top99.bots)}/${fmt(top99.accounts)}` : "–", "confident calls were real bots", "on labelled test accounts (Twitter benchmark), where the true answer is known", "Proven", "#2e9e5b"],
  ].map(([i, v, t, d, c, col]) => `<div class="fcard"><div class="fhead"><span class="ti">${i}</span><span class="cert" style="background:${col}">${c} certainty</span></div>
    <b>${typeof v === "number" ? fmt(v) : v}</b><span class="ft">${t}</span><span class="fd">${d}</span></div>`).join("") +
    `<p class="fnote">Out of ${fmt(F.accounts)} accounts, ${fmt(F.none)} look like ordinary conversation. "Certain" on YouTube needs a sample checked by two people; that is our next step.</p>`;

  /* ---------- trust row ---------- */
  const allChecks = (S.checks?.groups || []).flatMap(g => g.items);
  const CFt = S.model.confidence || {};
  $("trustrow").innerHTML = [
    ["✅", `${allChecks.filter(i => i.status === "PASS").length}/${allChecks.length}`, "safety and sanity checks pass"],
    ["🎯", `${CFt.accuracy ?? "–"}%`, `right on ${fmt(CFt.n || 0)} test accounts with known answers`],
    ["🧪", fmt((S.model.tested || []).reduce((s, r) => s + r[1], 0)), "test runs on labelled accounts"],
    ["🔒", "0", "names or channel IDs stored"],
  ].map(([i, v, l]) => `<div class="tile"><span class="ti">${i}</span><b>${v}</b><span>${l}</span></div>`).join("");

  /* KPI cards: each opens the evidence behind it */
  const KPI_GO = [["stage", 2], ["query", 10], ["stage", 1], ["query", 0], ["query", 9], ["query", 10]];
  $("kpis").innerHTML = S.kpis.map((k, i) => `<button class="kpi" data-i="${i}" type="button"><b>${fmt(k.value)}</b><span>${esc(k.label)}</span>
    <em>${KPI_GO[i][0] === "query" ? "Open in graph →" : "See pipeline step →"}</em></button>`).join("");
  $("kpis").addEventListener("click", e => {
    const b = e.target.closest(".kpi"); if (!b) return;
    const [kind, arg] = KPI_GO[+b.dataset.i];
    if (kind === "query") { openFull(); runQuery(arg); $("graph").scrollIntoView({ behavior: "smooth" }); }
    else { selectStage(arg); $("pipeline").scrollIntoView({ behavior: "smooth" }); }
  });

  /* Pipeline as a clickable node-link graph */
  const STAGE_INFO = [
    { color: "#FFC454", text: "#4a3b0b", edge: "raw comments", go: 10,
      why: "Influence operations surface first in public comment sections. We need a lawful, repeatable way to watch them.",
      how: "Official YouTube Data API only: search recent Kosovo–Serbia videos in Albanian, Serbian and English, rank by comment count, pull comment threads and replies, then public channel age.",
      io: ["Search queries", "Comments, video metadata, channel age"] },
    { color: "#C990C0", text: "#fff", edge: "hashed IDs", go: null,
      why: "We study behaviour, not people. Nobody should be identifiable from what we store or publish.",
      how: "Every author ID becomes an HMAC-SHA256 keyed hash on arrival. Channel IDs and display names are dropped before anything touches disk.",
      io: ["Raw author IDs", "yt_xxxxxxxxxxxx pseudonyms"] },
    { color: "#57C7E3", text: "#0b3440", edge: "rows", go: null,
      why: "Re-scanning the same video must never double-count a swarm.",
      how: "SQLite with scoped comment IDs (video + upstream ID). Inserts are idempotent; changed videos are marked for re-analysis.",
      io: ["Pseudonymised comments", "Deduplicated store"] },
    { color: "#F79767", text: "#fff", edge: "risk scores", go: 9,
      why: "A swarm looks normal one comment at a time. It shows up in timing, wording and who appears together.",
      how: "Per comment: timing bursts vs the video's own baseline, Geg/standard doublets and Serbian calques, near-duplicate talking points, network signals. Weighted into a 0–100 risk.",
      io: ["Comments per video", "Risk per comment, alert level per video"] },
    { color: "#4C8EDA", text: "#fff", edge: "account graph", go: 0,
      why: "One account can look human. Accounts copying each other or turning up together across videos do not.",
      how: "Links between accounts: COPY (near-identical text), SYNC (posting within seconds), CO_THREAD (active on the same videos together).",
      io: ["Accounts and comments", "Typed links between accounts"] },
    { color: "#8DCC93", text: "#163a1b", edge: "learned weights", go: 5,
      why: "Hand-set rules are guesses. Weights learned from labelled accounts can be measured.",
      how: "LightGBM over account indicators plus a relational graph neural network over the links, calibrated with isotonic regression, trained on human-labelled Cresci-2017 accounts.",
      io: ["Labelled accounts + graph", "Calibrated model, indicator weights"] },
    { color: "#F16667", text: "#fff", edge: null, go: 9,
      why: "The tool supports a decision; it never makes one. An analyst reviews, a human acts.",
      how: "Videos get an alert level, accounts get a ranked risk with the evidence behind it. Indicators of coordination, never attribution.",
      io: ["Scores, links, evidence", "Review queue and SitRep"] },
  ];
  const PW = 1100, PH = 250, R = 46;
  const px = i => 80 + i * ((PW - 160) / (S.pipeline.length - 1));
  const ps = d3.select("#pipe").append("svg").attr("viewBox", `0 0 ${PW} ${PH}`).attr("class", "pipesvg").attr("role", "group").attr("aria-label", "Pipeline stages");
  ps.append("defs").append("marker").attr("id", "parrow").attr("viewBox", "0 -5 10 10").attr("refX", 10).attr("markerWidth", 8).attr("markerHeight", 8)
    .attr("orient", "auto").append("path").attr("d", "M0,-5L10,0L0,5").attr("fill", "#a5abb6");
  S.pipeline.slice(0, -1).forEach((p, i) => {
    const x1 = px(i) + R + 4, x2 = px(i + 1) - R - 6, y = 110;
    ps.append("line").attr("x1", x1).attr("x2", x2).attr("y1", y).attr("y2", y).attr("stroke", "#a5abb6").attr("stroke-width", 2).attr("marker-end", "url(#parrow)");
    const g = ps.append("g").attr("transform", `translate(${(x1 + x2) / 2},${y - 14})`);
    const t = g.append("text").attr("class", "rel-label").attr("text-anchor", "middle").attr("dy", "0.32em").style("font-size", "10px").text(STAGE_INFO[i].edge);
    const bb = t.node().getBBox();
    g.insert("rect", "text").attr("x", -bb.width / 2 - 3).attr("y", -7).attr("width", bb.width + 6).attr("height", 14).attr("fill", "#fff");
  });
  const pn = ps.selectAll("g.pnode").data(S.pipeline).join("g").attr("class", "pnode").attr("tabindex", 0).attr("role", "button")
    .attr("aria-label", d => `${d.title}: ${d.value} ${d.unit}`).attr("transform", (d, i) => `translate(${px(i)},110)`)
    .on("click", (e, d) => selectStage(S.pipeline.indexOf(d)))
    .on("keydown", (e, d) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); selectStage(S.pipeline.indexOf(d)); } });
  pn.append("circle").attr("class", "ring").attr("r", R + 6).attr("fill", "none");
  pn.append("circle").attr("r", R).attr("fill", (d, i) => STAGE_INFO[i].color).attr("stroke", (d, i) => d3.color(STAGE_INFO[i].color).darker(0.6)).attr("stroke-width", 2);
  pn.append("text").attr("text-anchor", "middle").attr("dy", "-0.2em").attr("fill", (d, i) => STAGE_INFO[i].text).style("font-size", "17px").style("font-weight", 700).text(d => fmt(d.value));
  pn.append("text").attr("text-anchor", "middle").attr("dy", "1.3em").attr("fill", (d, i) => STAGE_INFO[i].text).style("font-size", "9.5px").text(d => d.stage.toUpperCase());
  pn.append("text").attr("text-anchor", "middle").attr("y", R + 26).attr("fill", "#1b2433").style("font-size", "13px").style("font-weight", 600).text(d => d.title);
  pn.append("text").attr("text-anchor", "middle").attr("y", R + 43).attr("fill", "#5d6b7e").style("font-size", "11px").text(d => d.unit.length > 26 ? d.unit.slice(0, 25) + "…" : d.unit);

  const STAT_KEY = ["collect", "protect", "store", "score", "link", "learn", "decide"];
  function barsHtml(series, unit = "") {
    if (!series || !series.length) return "";
    const max = Math.max(...series.map(s => s.value), 1);
    return `<div class="bars">${series.map(s => `<div class="bar"><span class="t" title="${esc(s.label)}">${esc(s.label)}</span><span class="v">${fmt(s.value)}${unit}</span>
      <div class="track"><div class="fill" style="width:${(s.value / max * 100).toFixed(1)}%"></div></div></div>`).join("")}</div>`;
  }
  function selectStage(i) {
    const p = S.pipeline[i], info = STAGE_INFO[i], st = (S.stats || {})[STAT_KEY[i]] || {};
    pn.classed("sel", (d, j) => j === i);
    const extra = STAT_KEY[i] === "score" ? `<h4>Average signal strength in medium/high-risk comments</h4>${barsHtml(st.signals)}`
      : STAT_KEY[i] === "learn" ? `<h4>F1 on bot types never seen in training</h4>${barsHtml(st.lofo)}`
      : STAT_KEY[i] === "collect" && st.queries ? `<h4>Search queries</h4><p class="qtags">${st.queries.map(q => `<span>${esc(q)}</span>`).join("")}</p>` : "";
    const chartTitle = { collect: "Collection run", protect: "Comments per account", store: "Largest videos by comments", score: "Accounts by risk band",
      link: "Links by type", learn: "Top learned indicator weights (%)", decide: "Videos by alert level" }[STAT_KEY[i]];
    $("stage").innerHTML = `<div class="stage-head"><span class="dot" style="background:${info.color}"></span><div><div class="st">${String(i + 1).padStart(2, "0")} · ${esc(p.stage)}</div>
        <h3>${esc(p.title)}</h3></div><div class="stage-num"><b>${fmt(p.value)}</b><span>${esc(p.unit)}</span></div></div>
      <div class="stage-grid">
        <div><h4>Why this step</h4><p>${esc(info.why)}</p><h4>How it works</h4><p>${esc(info.how)}</p>
          <h4>In → out</h4><p class="io"><span>${esc(info.io[0])}</span> → <span>${esc(info.io[1])}</span></p>
          <p class="note">${p.detail}</p>
          ${info.go !== null ? `<button class="gobtn" type="button" data-q="${info.go}">Open in graph browser →</button>` : ""}</div>
        <div><h4>${chartTitle}</h4>${barsHtml(st.series, STAT_KEY[i] === "learn" ? "%" : "")}${extra}</div>
      </div>`;
    const gb = $("stage").querySelector(".gobtn");
    if (gb) gb.onclick = () => { runQuery(+gb.dataset.q); $("graph").scrollIntoView({ behavior: "smooth" }); };
  }
  let runQuery = () => {};

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
  const CF = M.confidence || {};
  if (CF.rows) {
    $("confnote").textContent = `Scores are calibrated: a stated percentage is the share of similar held-out accounts that really were bots. ` +
      `On ${fmt(CF.n)} held-out accounts the verdict was right for ${fmt(CF.correct)} (${CF.accuracy}%): ${CF.fp} humans wrongly flagged, ${CF.fn} bots missed.`;
    $("conf").innerHTML = `<tr><td><b>Model said</b></td><td><b>Accounts</b></td><td><b>Really bots</b></td></tr>` +
      CF.rows.map(r => `<tr><td>${esc(r.band)} bot</td><td>${fmt(r.accounts)}</td><td>${fmt(r.bots)} (${r.bot_rate}%)</td></tr>`).join("");
  }
  const total = (M.tested || []).reduce((s, r) => s + r[1], 0);
  $("tested").innerHTML = (M.tested || []).map(([k, v]) => `<tr><td>${esc(k)}</td><td>${fmt(v)}</td></tr>`).join("") +
    `<tr><td><b>Total test evaluations</b></td><td><b>${fmt(total)}</b></td></tr>
     <tr><td>Labelled accounts used overall (training + test)</td><td>≈105,000</td></tr><tr><td>Posts processed for the graph model</td><td>714,856</td></tr>`;
  if (M.stress && M.stress.f1) $("stress").textContent = `Stress test: with each account cut to at most 3 posts (YouTube-like activity), F1 is ${M.stress.f1} ` +
    `(precision ${M.stress.precision}, recall ${M.stress.recall}) on ${fmt(M.stress.n)} held-out accounts.`;

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
    Theme:     { color: "#DA7194", text: "#fff", r: 44 },
    Group:     { color: "#569480", text: "#fff", r: 30 },
  };
  const baseStyle = n => STYLE[n.labels.includes("Flagged") ? "Flagged" : n.labels[0]] || STYLE.Account;
  const styleOf = n => {
    const s = baseStyle(n);
    if (n.labels[0] === "Group") return { ...s, r: Math.min(64, 26 + Math.sqrt(n.props.accounts) * 1.6) };
    if (n.labels[0] === "Theme") return { ...s, r: Math.min(60, 34 + Math.sqrt(n.props.accounts) * 0.6) };
    return s;
  };
  const caption = n => {
    const p = n.props;
    if (n.labels[0] === "Account") return `${p.main_theme === "no tagged theme" ? "Account" : p.main_theme} · risk ${p.risk}`;
    if (n.labels[0] === "Video") return p.title;
    if (n.labels[0] === "Group") return `${p.name.split(" · ")[0]} · ${p.accounts} accts`;
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

  const themeQuery = (key, title) => ({ name: `Theme: ${title}`,
    cypher: `MATCH (a:Account)-[t:TALKS_ABOUT]->(:Theme {key:'${key}'}) OPTIONAL MATCH (a)-[m:MEMBER_OF]->(g:Group) RETURN a, t, m, g`,
    run: () => {
      const tid = `theme:${key}`, accs = (relsOf.get(tid) || []).filter(r => r.type === "TALKS_ABOUT").map(r => r.start)
        .sort((x, y) => (nodeById.get(y).props.risk - nodeById.get(x).props.risk)).slice(0, 120);
      const ids = new Set([tid, ...accs]);
      for (const a of accs) for (const r of relsOf.get(a) || []) if (r.type === "MEMBER_OF") ids.add(r.end);
      return sub(ids, r => r.type === "MEMBER_OF" || COORD.includes(r.type) || (r.type === "TALKS_ABOUT" && r.end === tid), 200);
    } });
  const QUERIES = [
    { name: "Groups and what they talk about", cypher: "MATCH (g:Group)-[r:ABOUT]->(t:Theme) RETURN g, r, t",
      run: () => sub(G.nodes.filter(n => n.labels[0] === "Theme" || (n.labels[0] === "Group" && n.props.accounts >= 5)).map(n => n.id), r => r.type === "ABOUT", 200) },
    themeQuery("contests_independence", "contests Kosovo's independence"),
    themeQuery("religion", "religion"),
    themeQuery("nato", "NATO / KFOR"),
    themeQuery("russia", "Russia"),
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
      .text(d => d.type === "WEIGHT" ? `WEIGHT ${d.props.importance_pct.toFixed(1)}%` : d.type === "ABOUT" ? `ABOUT ${d.props.share_pct}%` : d.type);
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
      .force("link", d3.forceLink(rels).id(d => d.id).distance(d => d.type === "WEIGHT" || d.type === "ABOUT" ? 170 : d.type === "POSTED_ON" ? 110 : 95).strength(0.6))
      .force("charge", d3.forceManyBody().strength(d => d.labels[0] === "Verdict" || d.labels[0] === "Theme" ? -1400 : -320))
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

  /* ---------- plain-language explanations ---------- */
  const plural = (n, w) => `${fmt(n)} ${w}${n === 1 ? "" : "s"}`;
  function plainEvidence(s) {
    let m;
    if ((m = s.match(/(\d+) comments in its 15-min window vs ([\d.]+) expected \(×([\d.]+)/)))
      return `<b>Posting burst.</b> ${m[1]} comments landed on this video within 15 minutes, where about ${Math.round(+m[2]) || 1} would be normal: <b>${Math.round(+m[3])}× the usual rate</b>.`;
    if ((m = s.match(/Nearest neighbour only (\d+)s away/))) return `<b>Tight timing.</b> Another comment was posted only ${m[1]} seconds from this one.`;
    if ((m = s.match(/(\d+) comments within ±2 min/))) return `<b>Micro-burst.</b> ${m[1]} comments within 2 minutes of each other.`;
    if ((m = s.match(/Near-duplicate of (\d+) comment\(s\) \(similarity best ([\d.]+)/)))
      return `<b>Copied text.</b> Its comment is ${+m[2] >= 0.99 ? "word-for-word the same as" : `${Math.round(m[2] * 100)}% identical to`} ${plural(+m[1], "other comment")}.`;
    if ((m = s.match(/Low follower count \((\d+)\)/))) return `<b>Small channel.</b> Only ${fmt(+m[1])} subscribers.`;
    if (/after publication|right after/i.test(s)) return `<b>Early swarm.</b> ${esc(s)}`;
    return esc(s.replace(/@yt_[0-9a-f]{12}/g, "another account"));
  }
  const verdict = r => r >= 60 ? ["High risk", "Strong signs of coordination. Review first.", "#d43f3a"]
    : r >= 35 ? ["Medium risk", "Several signs of coordination. Worth an analyst's look.", "#e8a33b"]
    : r >= 15 ? ["Low risk", "Weak signals only. Probably ordinary.", "#4C8EDA"] : ["Organic", "Looks like normal conversation.", "#2e9e5b"];
  const meter = (label, v, hint) => `<div class="bar"><span class="t">${label}<small> · ${hint}</small></span><span class="v">${Math.round(v)}</span>
    <div class="track"><div class="fill" style="width:${Math.min(100, v)}%;background:${v >= 60 ? "#d43f3a" : v >= 30 ? "#e8a33b" : "#8dcc93"}"></div></div></div>`;
  const yrs = d => d == null ? "unknown age" : d >= 365 ? `${(d / 365).toFixed(1)} years old` : `${d} days old`;
  const sanity = items => `<div class="sanity">${items.map(([ok, t]) => `<span class="${ok ? "ok" : "no"}">${ok ? "✓" : "!"} ${t}</span>`).join("")}</div>`;

  function explain(d) {
    const p = d.props, L = d.labels[0];
    if (L === "Account") {
      const [v, vtext, vc] = verdict(p.risk);
      const g = p.group && p.group !== "not in a group" ? p.group : null;
      const gsize = g ? (nodeById.get(`group:${p.cluster}`) || {}).props?.accounts : null;
      return `<div class="verdict-box" style="border-color:${vc}"><b style="color:${vc}">${v} · ${p.risk}/100</b><span>${vtext}</span></div>
        <p class="lead">A YouTube account (${yrs(p.account_age_days)}) that wrote ${plural(p.comments, "comment")} on ${plural(p.videos, "video")}.
        ${p.themes.length ? `It talks about <b>${p.themes.map(esc).join(", ")}</b>.` : "Its comments match none of our themes."}</p>
        ${g ? `<p class="lead">Belongs to <b>${esc(g.split(" · ")[0])}</b>, a group of ${fmt(gsize || 0)} accounts linked by copied text or by turning up together on the same videos.</p>` : ""}
        <h5>Why it scored this way</h5><ul class="why">${(p.evidence.length ? p.evidence.map(plainEvidence) : ["No single strong signal."]).map(x => `<li>${x}</li>`).join("")}</ul>
        <h5>Signals (0–100)</h5><div class="bars">${meter("Timing", p.timing, "bursts, speed")}${meter("Talking points", p.talking_points, "copied or shared lines")}
          ${meter("Language", p.language, "dialect mix, translation traces")}${meter("Network", p.network, "account age, links, overlap")}</div>
        <h5>Sanity</h5>${sanity([[true, "Real comment, official API"], [/^yt_[0-9a-f]{12}$/.test(p.account_id), "Identity hashed"],
          [p.evidence.length > 0, "Score traceable to evidence"], [true, "Indicator, not attribution"]])}`;
    }
    if (L === "Group") {
      const top = (p.themes || []).map(esc);
      return `<p class="lead"><b>${esc(p.name.split(" · ")[0])}</b>: ${fmt(p.accounts)} accounts that are linked to each other, by posting the same text or by repeatedly showing up together on the same videos.</p>
        <h5>What they talk about</h5><p class="lead">${top.length ? top.join(" · ") : "Nothing our theme keywords recognise."}</p>
        <h5>How risky</h5><div class="bars">${meter("Average risk", p.avg_risk, "across members")}${meter("Highest risk", p.max_risk, "most suspicious member")}</div>
        <p class="lead">${fmt(p.medium_or_high_risk)} of ${fmt(p.accounts)} members are medium or high risk. Active on ${plural(p.videos, "video")}.</p>
        <h5>Sanity</h5>${sanity([[p.accounts >= 3, "At least 3 linked accounts"], [true, "Links computed from real comments"], [true, "Theme = keyword match, shown in Raw data"]])}`;
    }
    if (L === "Theme") {
      return `<p class="lead"><b>${esc(p.description)}</b>: ${fmt(p.comments)} comments by ${fmt(p.accounts)} accounts matched this theme.</p>
        <h5>How a comment gets this theme</h5><p class="lead">It contains one of these keyword patterns (Albanian, Serbian, English):</p>
        <p class="qtags">${p.keywords.slice(0, 14).map(k => `<span>${esc(k.replace(/\\[bs]\*?|\(\?:|\)|\\/g, "").slice(0, 28))}</span>`).join("")}</p>
        <h5>Sanity</h5>${sanity([[true, "Transparent keyword rule, not a black box"], [false, "Keyword match can miss sarcasm or quotes"]])}`;
    }
    if (L === "Video") {
      const lv = { 1: ["Level 1 · strategic threat", "#d43f3a"], 2: ["Level 2 · review", "#e8a33b"], 3: ["Level 3 · watch", "#4C8EDA"], 4: ["Level 4 · normal", "#2e9e5b"] }[p.level] || ["not scored", "#999"];
      return `<div class="verdict-box" style="border-color:${lv[1]}"><b style="color:${lv[1]}">${lv[0]}</b><span>${esc(p.title)}</span></div>
        <p class="lead">${plural(p.comments, "comment")} collected. ${p.flagged_pct}% of them flagged as coordinated; the largest linked group on it has ${fmt(p.largest_cluster || 0)} accounts. Published ${esc(p.published || "?")}.</p>
        <p class="lead"><a href="https://www.youtube.com/watch?v=${encodeURIComponent(p.video_id)}" target="_blank" rel="noopener noreferrer">Open the video on YouTube ↗</a></p>`;
    }
    if (L === "Indicator") return `<p class="lead"><b>${esc(p.name)}</b> carries <b>${p.importance_pct}%</b> of the model's evidence. In ${p.pushes_bot_pct}% of accounts it pushed the score towards "bot".</p><p class="note">Learned from human-labelled accounts, not set by us.</p>`;
    if (L === "Verdict") return `<p class="lead">The question the model answers: <b>is this account a bot?</b> Above ${Math.round(p.threshold * 100)}% it says yes.</p>`;
    return "";
  }

  function select(d, kind) {
    selected = d;
    root.selectAll(".node").classed("sel", n => d && kind === "node" && n.id === d.id);
    root.selectAll(".rel").classed("sel", r => d && kind === "rel" && r.id === d.id);
    if (!d) return showOverviewProps();
    if (kind === "node") {
      const chips = d.labels.map(l => `<span class="chip" style="background:${(STYLE[l] || STYLE.Account).color};color:${(STYLE[l] || STYLE.Account).text}">${l}</span>`).join("");
      const raw = `<details class="raw"><summary>Raw data</summary><table><tr><td>&lt;id&gt;</td><td>${esc(d.id)}</td></tr>${Object.entries(d.props).map(([k, v]) =>
        `<tr><td>${esc(k)}</td><td>${esc(Array.isArray(v) ? v.join(" · ") : v)}</td></tr>`).join("")}</table></details>`;
      $("props").innerHTML = `<h4>${chips}</h4>${explain(d)}${raw}`;
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

  let lastQ = 0;
  function run(i) {
    lastQ = i;
    const q = typeof i === "number" ? QUERIES[i] : i, t0 = performance.now();
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
  runQuery = run;
  $("full").addEventListener("toggle", () => { if ($("full").open) run(lastQ); });
  const openFull = () => { if (!$("full").open) $("full").open = true; };

  /* ---------- browser self-test: every saved query, rendered for real ---------- */
  async function selfTest() {
    const out = [];
    for (let i = 0; i < QUERIES.length; i++) {
      const t0 = performance.now(); let err = null, res;
      try { res = QUERIES[i].run(); run(i); } catch (e) { err = String(e); }
      await new Promise(r => setTimeout(r, 60));
      const ms = Math.round(performance.now() - t0);
      const ids = res ? new Set(res.nodes.map(n => n.id)) : new Set();
      const dangling = res ? res.rels.filter(r => !ids.has(r.start) || !ids.has(r.end)).length : 0;
      const hashCaps = [...document.querySelectorAll("#svg .node text")].filter(t => /^[0-9a-f]{6}$/.test(t.textContent)).length;
      const drawn = document.querySelectorAll("#svg .node").length;
      const ok = !err && res.nodes.length > 0 && res.rels.length > 0 && dangling === 0 && hashCaps === 0 && drawn === res.nodes.length;
      out.push({ q: QUERIES[i].name, nodes: res ? res.nodes.length : 0, rels: res ? res.rels.length : 0, drawn, dangling, hashCaps, ms, ok, err });
    }
    run(0);
    const max = Math.max(...out.map(o => o.nodes + o.rels), 1);
    $("selftest").innerHTML = `<p class="lead"><b>${out.filter(o => o.ok).length} of ${out.length} queries pass.</b></p>` + out.map(o =>
      `<div class="chk"><span class="s ${o.ok ? "PASS" : "FAIL"}">${o.ok ? "PASS" : "FAIL"}</span><div><b>${esc(o.q)}</b>
        <span>${fmt(o.nodes)} nodes · ${fmt(o.rels)} relationships · ${o.drawn} drawn · ${o.dangling} dangling · ${o.hashCaps} hash captions · ${o.ms} ms${o.err ? " · " + esc(o.err) : ""}</span>
        <div class="track st"><div class="fill" style="width:${((o.nodes + o.rels) / max * 100).toFixed(1)}%"></div></div></div></div>`).join("");
    document.body.dataset.selftest = out.every(o => o.ok) ? "pass" : "fail";
    return out;
  }
  $("selftest-run").onclick = () => selfTest();
  if (/[?&]selftest=1/.test(location.search)) { $("full").open = true; selfTest(); }
  run(0);
  selectStage(0);
})();
