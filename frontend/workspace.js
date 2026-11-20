


/* Stitch analyst workspace. */
(() => {
  const data = window.workspaceData;
  const byId = id => document.getElementById(id);
  const view = byId('view'), modal = byId('modal'), connection = byId('connection'), notice = byId('notice');
  const search = byId('quick-search');
  const displayLevel = {1:'Critical',2:'High',3:'Moderate',4:'Baseline'};
  const signals = [['temporal','Temporal','tReasons'],['linguistic','Linguistic','lReasons'],
                   ['narrative','Narrative','nReasons'],['network','Network','gReasons']];
  const decisionLabel = {likely_coordinated:'Likely coordinated',organic:'Organic',inconclusive:'Inconclusive'};
  const numberFormat = new Intl.NumberFormat();
  const timestampFormat = new Intl.DateTimeFormat(undefined,{dateStyle:'medium',timeStyle:'short'});
  const clockFormat = new Intl.DateTimeFormat(undefined,{hour:'2-digit',minute:'2-digit'});
  const state = {mode:data.hasKey() ? 'live' : 'exercise', health:null, authError:'', threads:[],
    analysis:null, activeComment:'', activeTab:'overview', filters:{query:'',platform:'',level:'',limit:25}};
  let requestGeneration = 0, socketGeneration = 0;
  let socket, reconnect, refreshTimer, toastTimer, map;

  const esc = value => String(value ?? '').replace(/[&<>"']/g, char => ({'&':'&amp;', '<':'&lt;', '>':'&gt;', '"':'&quot;', "'":'&#39;'}[char]));
  const enc = encodeURIComponent;
  const int = value => Number.isFinite(value) ? numberFormat.format(value) : '—';
  const pct = value => Number.isFinite(value) ? (value * 100).toFixed(1) + '%' : '—';
  const date = value => Number.isFinite(value) && value > 0 && value <= 8.64e15 ? timestampFormat.format(value) : '—';
  const seconds = value => date(value * 1000);
  const icon = name => ' <span class="material-symbols-outlined" aria-hidden="true">' + esc(name) + '</span>';
  const isExercise = () => state.mode === 'exercise';
  const isDemo = () => isExercise() && !data.hasKey() && state.health?.auth_required !== false;
  const queryMode = () => '?simulated=' + isExercise();
  const pathFor = (id, action) => 'threads/' + enc(id) + '/' + action;
  const exerciseBadge = () => tag('Exercise','exercise');
  const filteredThreads = () => state.threads.filter(row => Boolean(row.simulated) === isExercise());
  const titleFor = id => state.threads.find(row => row.id === id)?.title || (state.analysis?.meta.thread.id === id ? state.analysis.meta.thread.title : '') || id;
  const highestScore = items => items.reduce((best, item) => !best || item.score > best.score ? item : best, null);
  const api = (path, options) => isDemo() ? Promise.resolve().then(() => data.exercise(path,options)) : data.api(path,options);

  function route() {
    const [name,value] = location.hash.slice(1).split('/');
    const page = ['monitor','thread','cases','case','sources','alerts','exercise'].includes(name) ? name : 'monitor';
    let id = '';
    try { id = decodeURIComponent(value || ''); } catch {}
    return {page,id};
  }
  function go(page,id) {
    const path = '#' + page + (id ? '/' + enc(id) : '');
    if (location.hash === path) render();
    else location.hash = path;
  }
  function announce(message,error = false) {
    clearTimeout(toastTimer);
    notice.hidden = false;
    notice.className = 'notice' + (error ? ' error' : '');
    notice.textContent = message;
    toastTimer = setTimeout(() => { notice.hidden = true; },5500);
  }
  function tag(label,color = '') {
    return `<span class="tag ${esc(color)}">${esc(label)}</span>`;
  }
  function severity(level) {
    return displayLevel[level] ? tag('L'+level+' · '+displayLevel[level],'l'+level) : tag('Analysis pending');
  }
  function button(label,action,options = {}) {
    const value = options.value == null ? '' : ` data-value="${esc(options.value)}"`;
    return `<button type="button" class="button${options.primary ? ' primary' : ''}" data-action="${esc(action)}"${value}>${options.icon ? icon(options.icon) : ''}${esc(label)}</button>`;
  }
  function link(label,page,id,options = {}) {
    const className = options.record ? 'record-title' : 'button' + (options.primary ? ' primary' : '');
    return `<a class="${className}" href="#${page}${id ? '/'+enc(id) : ''}">${options.icon ? icon(options.icon) : ''}${esc(label)}</a>`;
  }
  function external(label,value) {
    try {
      const url = new URL(value);
      if (['https:','http:'].includes(url.protocol)) return `<a class="button" href="${esc(url.href)}" target="_blank" rel="noopener noreferrer">${esc(label)}${icon('open_in_new')}</a>`;
    } catch {}
    return '';
  }
  function heading(title,subtitle,actions = '',eyebrow = '') {
    return `<div class="page-heading"><div>${eyebrow ? '<span class="eyebrow">'+esc(eyebrow)+'</span>' : ''}
      <h1>${esc(title)}</h1>${subtitle ? '<p class="muted">'+esc(subtitle)+'</p>' : ''}</div>
      ${actions ? '<div class="actions">'+actions+'</div>' : ''}</div>`;
  }
  function panel(title,content) {
    return `<section class="panel"><h2>${esc(title)}</h2>${content}</section>`;
  }
  function empty(text) { return `<p class="empty">${esc(text)}</p>`; }
  function metrics(entries) {
    return `<div class="metric-grid">${entries.map(([label,value,note]) =>
      `<div class="metric"><span>${esc(label)}</span><strong>${esc(value)}</strong><small>${esc(note)}</small></div>`).join('')}</div>`;
  }
  function table(labels,rows,fallback) {
    return rows.length ? `<div class="scroll-table" tabindex="0" aria-label="Scrollable results"><table><thead><tr>
      ${labels.map(label => '<th scope="col">'+esc(label)+'</th>').join('')}</tr></thead><tbody>${rows.join('')}</tbody></table></div>` : empty(fallback);
  }
  function options(values,selected = '') {
    return values.map(([value,label]) => `<option value="${esc(value)}"${String(value) === String(selected) ? ' selected' : ''}>${esc(label)}</option>`).join('');
  }
  function updateChrome() {
    const {page} = route();
    const nav = page === 'thread' ? 'monitor' : page === 'case' ? 'cases' : page;
    document.querySelectorAll('[data-nav]').forEach(node => {
      if (node.dataset.nav === nav) node.setAttribute('aria-current','page');
      else node.removeAttribute('aria-current');
    });
    document.querySelectorAll('[data-mode]').forEach(node => node.setAttribute('aria-pressed',String(node.dataset.mode === state.mode)));
    let text = state.health?.ok ? 'Ready to connect' : 'Connecting';
    let status = '';
    if (isDemo()) { text = 'Local exercise'; status = 'exercise'; }
    else if (state.authError) { text = state.authError; status = 'error'; }
    else if (socket?.readyState === WebSocket.OPEN) {
      text = isExercise() ? 'Exercise connected' : 'Live connected';
      status = isExercise() ? 'exercise' : 'ready';
    } else if (state.health?.analyst_key_configured === false && state.health.auth_required) text = 'Analyst key required on server';
    connection.className = 'connection '+status;
    connection.replaceChildren(document.createElement('span'),document.createTextNode(text));
  }
  function modeBanner() {
    if (isDemo()) return '<div class="banner exercise"><strong>EXERCISE · LOCAL DEMO</strong> · Fictional data. Changes stay in this browser tab.</div>';
    return isExercise() ? '<div class="banner exercise"><strong>EXERCISE</strong> · Simulated records and alerts.</div>' : '';
  }
  function modalOpen(title,body) {
    modal.innerHTML = `<div class="panel-heading"><h2 id="modal-title">${esc(title)}</h2>${button('Close','close-modal')}</div>${body}`;
    if (!modal.open) modal.showModal();
  }
  function renderError(error) {
    const keyNeeded = error.status === 401;
    const reason = keyNeeded && state.health?.analyst_key_configured === false
      ? 'Set an analyst API key on the FastAPI server to enable Live mode.' : error.message;
    view.innerHTML = heading('Analyst workspace','Connect to monitor threads and review campaign evidence.') +
      `<section class="banner ${keyNeeded ? '' : 'error'}"><h2>${keyNeeded ? 'Analyst connection required' : 'Workspace unavailable'}</h2>
      <p>${esc(reason)}</p><div class="actions">${button('Connect','connect',{primary:true})}
      ${button('Open local exercise','local-exercise')}${button('Retry','refresh')}</div></section>`;
  }
  function monitorRows() {
    const q = state.filters.query.trim().toLowerCase();
    const {platform,level} = state.filters;
    return filteredThreads().filter(row => (!platform || row.platform === platform) &&
      (!level || (level === 'pending' ? !row.level : String(row.level) === level)) &&
      (!q || [row.title,row.id,row.platform,row.url].some(value => String(value || '').toLowerCase().includes(q))));
  }
  function queueHtml(matches) {
    const selected = state.monitorSelection || matches[0]?.id;
    return table(['Severity','Thread and source','Comments','Flagged','Latest activity','Action'],
      matches.slice(0,state.filters.limit).map(row => `<tr${row.id === selected ? ' class="is-selected"' : ''}>
        <td>${severity(row.level)}</td><td>${link(row.title || row.id,'thread',row.id,{record:true})}
        <small>${esc(row.platform || 'Source')} · ${esc(row.id)}</small></td><td>${int(row.n)}</td>
        <td><span class="threshold">${pct(row.pct)}</span></td><td>${esc(date(row.last_ts))}</td>
        <td>${button('Focus','focus-thread',{value:row.id})}</td></tr>`),'No threads match these filters.');
  }
  function queueFooter(total) {
    return `<small>${int(Math.min(state.filters.limit,total))} of ${int(total)} threads</small>
      ${total > state.filters.limit ? button('Load more','more-threads') : ''}`;
  }
  function monitorPage() {
    const rows = filteredThreads();
    const scored = rows.filter(row => Number.isFinite(row.pct));
    const average = scored.length ? scored.reduce((sum,row) => sum + row.pct,0)/scored.length : null;
    const platforms = [...new Set(rows.map(row => row.platform).filter(Boolean))].sort();
    const matches = monitorRows();
    return heading('Thread Monitoring & Triage Queue','Review incoming discussions for evidence of possible coordination.',
      button('Refresh queue','refresh',{icon:'refresh'}) + button('Export CSV','thread-csv',{icon:'download'}) + link('Exercise tools','exercise','',{icon:'science'})) + modeBanner() +
      metrics([['Monitored threads',int(rows.length),'In the selected mode'],
        ['Escalated threads',int(rows.filter(row => row.level === 1 || row.level === 2).length),'Level 1 or Level 2'],
        ['Comments',int(rows.reduce((sum,row) => sum + (row.n || 0),0)),'In monitored threads'],
        ['Average flagged share',pct(average),'Across scored threads']]) +
      `<div class="split">${panel('Triage queue', `<div class="controls">
      <input id="monitor-filter" type="search" placeholder="Filter by thread, source, or ID…" value="${esc(state.filters.query)}" aria-label="Filter threads">
      <select id="filter-platform" aria-label="Platform">${options([['','All platforms'],...platforms.map(value=>[value,value])],state.filters.platform)}</select>
      <select id="filter-level" aria-label="Severity">${options([['','All severity levels'],...Object.entries(displayLevel).map(([n,label])=>[n,'Level '+n+' · '+label]),['pending','Analysis pending']],state.filters.level)}</select>
      </div><div id="queue-rows">${queueHtml(matches)}</div><div id="queue-footer" class="controls">${queueFooter(matches.length)}</div>`)}
      <div id="triage-focus"></div></div>`;
  }
  async function focusThread(id,generation = requestGeneration) {
    const target = byId('triage-focus');
    if (!target) return;
    const row = filteredThreads().find(entry => entry.id === id);
    if (!row) { target.innerHTML = panel('Triage focus',empty('Select a thread to inspect its latest signals.')); return; }
    state.monitorSelection = id;
    let analysis;
    target.innerHTML = panel('Triage focus','<p class="muted">Loading thread signals…</p>');
    try { if (row.level) analysis = await api(pathFor(id,'analysis')); } catch {}
    if (generation !== requestGeneration || state.monitorSelection !== id || byId('triage-focus') !== target) return;
    const item = analysis && highestScore(analysis.items);
    target.innerHTML = panel('Triage focus', `${severity(row.level)}<h3 style="margin-top:.55rem">${esc(row.title || row.id)}</h3>
      <dl class="data-list"><dt>Latest activity</dt><dd>${esc(date(row.last_ts))}</dd><dt>Dominant narrative</dt>
      <dd>${esc(analysis?.thread.narratives?.[0]?.[0] || 'Unavailable')}</dd>
      <dt>Highest scoring comment</dt><dd>${item ? esc(item.c.text.slice(0,180)) : 'Unavailable'}</dd></dl>
      ${link('Open workspace','thread',id,{primary:true,icon:'open_in_new'})}`);
  }
  function refreshFilters({focus = false} = {}) {
    const matches = monitorRows();
    byId('queue-rows').innerHTML = queueHtml(matches);
    byId('queue-footer').innerHTML = queueFooter(matches.length);
    if (focus) focusThread(state.monitorSelection || matches[0]?.id);
  }
  function burstChart(items) {
    const interval = 300_000;
    const newest = items.reduce((last,item) => Math.max(last,item.t ?? item.c.ts ?? 0),0);
    if (!newest) return empty('No timestamps are available for the burst chart.');
    const start = Math.floor(newest / interval) * interval - 23 * interval;
    const buckets = Array.from({length:24}, () => ({all:0,flagged:0}));
    for (const item of items) {
      const stamp = item.t ?? item.c.ts;
      const bucket = Number.isFinite(stamp) && buckets[Math.floor((stamp-start)/interval)];
      if (!bucket) continue;
      ++bucket.all;
      if (item.tier !== 'organic') ++bucket.flagged;
    }
    const max = Math.max(1,...buckets.map(bucket=>bucket.all));
    const time = index => clockFormat.format(start+index*interval);
    return `<p class="muted">Two hours through the latest comment · Peak ${max} comments per 5 minutes</p>
      <div class="barplot" role="img" aria-label="Five-minute comment counts">
      ${buckets.map((bucket,index)=>`<span title="${esc(time(index))} · ${bucket.all} comments · ${bucket.flagged} flagged">
      <i style="height:${100*bucket.all/max}%;${bucket.flagged ? 'border-top:2px solid var(--amber)' : ''}"></i></span>`).join('')}</div>
      <div class="plot-labels"><span>${esc(time(0))}</span><span>${esc(time(23))}</span></div>
      <details><summary>View chart data</summary>${table(['Time','Comments','Flagged'],buckets.map((bucket,index)=>
      `<tr><td>${esc(time(index))}</td><td>${bucket.all}</td><td>${bucket.flagged}</td></tr>`),'')}</details>`;
  }
  function highlights(item) {
    const text = item.c.text || '';
    let offset = 0;
    let html = '';
    for (const span of [...(item.spans || [])].sort((a,b)=>a.s-b.s)) {
      if (!Number.isInteger(span.s) || !Number.isInteger(span.e) || span.s < offset || span.e > text.length || span.s >= span.e) continue;
      html += esc(text.slice(offset,span.s))+'<mark>'+esc(text.slice(span.s,span.e))+'</mark>';
      offset = span.e;
    }
    return html+esc(text.slice(offset));
  }
  function evidence(item) {
    if (!item) return empty('Select a comment to inspect its evidence.');
    return `<div class="actions">${tag('Score '+item.score,item.tier === 'high' ? 'l1' : '')}${tag(item.tier)}${item.c.simulated ? exerciseBadge() : ''}</div>
      <dl class="data-list"><dt>Account</dt><dd>${esc(item.c.user)}</dd><dt>Published</dt><dd>${esc(date(item.t ?? item.c.ts))}</dd></dl>
      <p class="comment-body">${highlights(item)}</p>${signals.map(([key,label,reasons])=>`<h3>${label} · ${Number.isFinite(item[key]) ? int(item[key])+'/100' : 'Unavailable'}</h3>
      <ul>${(item[reasons] || []).map(reason=>'<li>'+esc(reason)+'</li>').join('') || '<li>No supporting details</li>'}</ul>`).join('')}
      <h3>Similar comments</h3>
      ${item.dups?.length ? `<ul>${item.dups.slice(0,8).map(dup=>`<li>${button(dup.user+' · '+pct(dup.sim),'inspect',{value:dup.id})}</li>`).join('')}</ul>` :
        '<p class="muted">No similar comments in this analysis.</p>'}${external('Open original comment',item.c.url)}`;
  }
  function overview(analysis) {
    const summary = analysis.thread;
    const signalCards = signals.map(([key,label])=>{
      const value = summary.signalAvg?.[key];
      return [label,Number.isFinite(value?.flagged) ? int(value.flagged)+'/100' : 'Unavailable',
        Number.isFinite(value?.organic) ? 'Organic average '+int(value.organic)+'/100' : 'Organic average unavailable'];
    });
    const narratives = (summary.narratives || []).map(row=>`<tr><td>${esc(row[0])}</td><td>${int(row[1])}</td><td>${esc(row[3])}</td></tr>`);
    const sample = highestScore(analysis.items);
    return metrics(signalCards)+`<div class="split">
      ${panel('Recurring narratives',table(['Narrative','Matches','Severity'],narratives,'No recurring narratives were identified.'))}
      ${panel('Evidence inspector',sample ? `${tag('Score '+sample.score)}<p class="comment-body">${highlights(sample)}</p>
        ${button('Inspect comment','inspect',{value:sample.c.id,primary:true})}` : empty('No comments are available.'))}</div>`;
  }
  function commentRows(sorted) {
    const q = (state.commentQuery || '').trim().toLowerCase();
    const items = sorted.filter(item=>!q || [item.c.user,item.c.text,item.c.id].some(value=>String(value || '').toLowerCase().includes(q)));
    return table(['Account and comment','Time','Score','Tier'],items.slice(0,state.commentLimit || 30).map(item=>
      `<tr${item.c.id === state.activeComment ? ' class="is-selected"' : ''}><td>${button(item.c.user,'inspect',{value:item.c.id})}
      <small>${esc(item.c.text.slice(0,135))}</small></td><td>${esc(date(item.t ?? item.c.ts))}</td><td>${int(item.score)}</td>
      <td>${tag(item.tier,item.tier === 'high' ? 'l1' : '')}</td></tr>`),'No comments match the filter.') +
      (items.length > (state.commentLimit || 30) ? button('Load more comments','more-comments') : '');
  }
  function comments(analysis) {
    const sorted = [...analysis.items].sort((a,b)=>b.score-a.score);
    const active = sorted.find(item=>item.c.id === state.activeComment) || sorted[0];
    state.activeComment = active?.c.id || '';
    return `<div class="split">${panel('Comments',`<div class="controls">
      <input type="search" id="comment-filter" aria-label="Filter comments" placeholder="Filter by text or account…" value="${esc(state.commentQuery || '')}">
      </div><div id="comment-rows">${commentRows(sorted)}</div>`)}
      <div id="evidence-panel">${panel('Evidence inspector',evidence(active))}</div></div>`;
  }
  function network(analysis) {
    const clusters = analysis.clusters.map(cluster=>`<tr><td>${esc(cluster.id)}</td><td>${int(cluster.size)}</td>
      <td>${int(cluster.avgScore)}</td><td>${esc(cluster.edgeTypes.join(', '))}</td>
      <td><details><summary>Accounts</summary>${cluster.members.slice(0,50).map(user=>
        `<div>${button(user,'account',{value:user})} <small>Score ${int(analysis.scoreByUser[user])}</small></div>`).join('')}</details></td></tr>`);
    const edges = analysis.edges.slice(0,80).map(edge=>`<tr><td>${button(edge.a,'account',{value:edge.a})}</td>
      <td>${button(edge.b,'account',{value:edge.b})}</td><td>${esc(edge.type)}</td><td>${int(edge.w)}</td></tr>`);
    const chart = edges.length ? '<canvas id="network-graph" class="network-graph" width="900" height="260" role="img" aria-label="Selected account links. See the connection table for details."></canvas>' :
      empty('No account links are available.');
    return `<div class="stack">${panel('Account network',chart)}
      ${panel('Connected clusters',table(['Cluster','Accounts','Mean score','Connection types','Members'],clusters,'No multi-account clusters identified.'))}
      ${panel('Account connections',table(['Account','Connected account','Evidence type','Weight'],edges,'No connections identified.'))}</div>`;
  }
  function plotNetwork() {
    const canvas = byId('network-graph');
    if (!canvas) return;
    const context = canvas.getContext('2d');
    if (!context) return;
    const edges = state.analysis.edges.slice(0,80);
    const accounts = [...new Set(edges.flatMap(edge=>[edge.a,edge.b]))].slice(0,70);
    const points = new Map(accounts.map((id,index)=>{
      const theta = index*Math.PI*(3-Math.sqrt(5));
      const radius = Math.sqrt((index+1)/(accounts.length+1));
      return [id,[450+405*Math.cos(theta)*radius,130+110*Math.sin(theta)*radius]];
    }));
    for (const edge of edges) {
      if (!points.has(edge.a) || !points.has(edge.b)) continue;
      context.beginPath();
      context.strokeStyle = edge.type === 'coactivity' ? '#28766a' : '#4b5966';
      context.moveTo(...points.get(edge.a));
      context.lineTo(...points.get(edge.b));
      context.stroke();
    }
    for (const [id,xy] of points) {
      context.beginPath();
      context.fillStyle = state.analysis.scoreByUser[id] >= 60 ? '#ff9f98' : '#57f1db';
      context.arc(...xy,4,0,2*Math.PI);
      context.fill();
    }
  }
  const mapped = row => Number.isFinite(row.lat) && Number.isFinite(row.lon) && Math.abs(row.lat) <= 90 && Math.abs(row.lon) <= 180;
  function infrastructure(analysis) {
    const rows = analysis.infra.map(row=>`<tr><td>${esc(row.node)}</td><td>${esc(row.asn || 'Unavailable')}</td>
      <td>${esc([row.city,row.cc].filter(Boolean).join(', ') || 'Unavailable')}</td><td>${esc(row.type || 'Unavailable')}</td><td>${int(row.comments)}</td></tr>`);
    const mapHtml = analysis.infra.some(mapped) ? '<div id="infra-map" class="map-shell" aria-label="Map of observed infrastructure"></div>' :
      empty('This source supplies no coordinates.');
    return panel('Observed infrastructure','<p class="muted">Locations represent observed egress infrastructure.</p>'+mapHtml+
      table(['Node','ASN','Observed location','Type','Comments'],rows,'No infrastructure telemetry is available.'));
  }
  function plotInfrastructure() {
    const host = byId('infra-map');
    if (!host) return;
    if (!window.L) { host.textContent = 'Map unavailable. Locations are shown in the table.'; return; }
    const points = state.analysis.infra.filter(mapped);
    map = L.map(host,{scrollWheelZoom:false});
    L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png',{attribution:'© <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors',maxZoom:18}).addTo(map);
    for (const point of points) {
      const label = document.createElement('span');
      label.textContent = [point.node,point.city,point.cc].filter(Boolean).join(' · ');
      L.circleMarker([point.lat,point.lon],{color:'#2dd4bf',radius:7}).bindPopup(label).addTo(map);
    }
    map.fitBounds(points.map(point=>[point.lat,point.lon]),{padding:[24,24],maxZoom:8});
  }
  function threadPage(analysis) {
    const {thread:summary,meta,items} = analysis;
    const thread = meta.thread;
    const tab = state.activeTab;
    const tabs = [['overview','Overview'],['comments','Comments'],['network','Network'],['infra','Infrastructure']];
    const tabContent = {overview,comments,network,infra:infrastructure};
    return `<div class="controls">${link('Back to monitor','monitor','',{icon:'arrow_back'})}${severity(summary.level)}${thread.simulated ? exerciseBadge() : ''}</div>` +
      heading(thread.title || thread.id,(thread.platform || 'Source')+' · '+thread.id,
        external('Open source thread',thread.url)+button('New case','new-case',{value:thread.id,icon:'add'})+
        button('Link to case','link-case',{value:thread.id,icon:'link'})+button('Preview SitRep','thread-report',{value:thread.id,icon:'description'}),'Thread investigation') +
      metrics([['Total comments',int(summary.n),'Analyzed in this thread'],['Flagged share',pct(summary.pct),'Medium or high score'],
        ['Largest cluster',int(summary.largestCluster),'Accounts'],['Scoring engine',meta.embedder || 'Unavailable',meta.scoring_version ? 'Version '+meta.scoring_version : 'Analysis contract '+analysis.contract]]) +
      panel('Five-minute comment bursts',burstChart(items))+
      `<div class="tabs" aria-label="Investigation views">${tabs.map(([key,label])=>
      `<button type="button" aria-pressed="${key === tab}" data-action="tab" data-value="${key}">${label}</button>`).join('')}</div>` +
      tabContent[tab](analysis)+'<p class="muted" style="margin-top:.8rem">Scores indicate possible coordination. Attribution requires independent evidence.</p>';
  }
  function paintThread() {
    if (map) { map.remove(); map = null; }
    view.innerHTML = modeBanner()+threadPage(state.analysis);
    plotNetwork();
    plotInfrastructure();
  }

  function caseList(cases, suggestions) {
    const rows = cases.map(record => `<tr><td>${link(record.title,'case',record.id,{record:true})}<small>Case ${record.id}</small></td>
      <td>${tag(record.status)}</td><td>${esc(record.owner || 'Unassigned')}</td>
      <td>${int(record.thread_ids.length)}</td><td>${esc(seconds(record.created_at))}</td></tr>`);
    const evidence = suggestions.map((suggestion, index) => `<article class="panel"><h3>${suggestion.thread_ids.length} related threads</h3>
      <ul>${suggestion.thread_ids.map(id=>`<li>${link(titleFor(id),'thread',id,{record:true})}</li>`).join('')}</ul>
      <p class="muted">${suggestion.links.length} thread links with recurring account pairs</p><div class="row-actions">
      ${button('Open case','accept-case',{value:index,primary:true})}${button('Dismiss','dismiss-case',{value:index})}</div></article>`).join('');
    return heading('Campaign Cases & SitRep Review', 'Link threads, preserve evidence and record analyst decisions.',
      link('Open monitor','monitor','',{icon:'arrow_back'})) +
      modeBanner() + `<div class="split">${panel('Case queue', table(['Case','Status','Owner','Threads','Created'], rows,'No campaign cases in this mode.'))}
      <div class="stack">${panel('Suggested cases', evidence || empty('No new case suggestions.'))}</div></div>`;
  }

  function casePage(campaign) {
    const active = new Set(campaign.thread_ids);
    const snapshots = Object.entries(campaign.evidence);
    const rows = snapshots.map(([tid, value]) => {
      const sample = value.comments || [];
      return `<tr><td>${link(value.thread.title || tid,'thread',tid,{record:true})}<small>${esc(tid)}</small></td>
        <td>${active.has(tid) ? tag('Linked','ready') : tag('Unlinked')}</td>
        <td>${severity(value.score.level)}<small>${int(sample.length)} captured comments</small></td>
        <td><details><summary>Read snapshot</summary><p class="muted">Scoring ${esc(value.scoring_version)} · Saved ${esc(date(value.captured_at))}</p>
          ${sample.map(comment => `<p class="comment-body"><strong>${esc(comment.user || comment.id)} · ${int(comment.score)}</strong><br>${esc(comment.text)} ${external('Original comment',comment.url)}</p>`).join('')}
        </details></td>
        <td>${active.has(tid) ? button('Unlink','unlink',{value:tid}) : ''}</td></tr>`;
    });
    const reviews = campaign.reviews.map(entry => `<article class="panel"><h3>${esc(decisionLabel[entry.decision] || entry.decision)}</h3>
      <small>${esc(entry.reviewer)} · ${esc(seconds(entry.created_at))}</small><p class="comment-body">${esc(entry.reason)}</p></article>`).join('');
    const choices = filteredThreads().filter(thread => !active.has(thread.id));
    return `<div class="controls">${link('Back to cases','cases','',{icon:'arrow_back'})}${tag(campaign.status)}${campaign.simulated ? exerciseBadge() : ''}</div>` +
      heading(campaign.title, 'Case ' + campaign.id + ' · ' + (campaign.owner || 'Unassigned') + ' · ' + seconds(campaign.created_at),
        button('Preview SitRep','case-report',{value:campaign.id,icon:'description'}), 'Campaign case') +
      `<div class="split"><div class="stack">
        ${panel('Linked threads and preserved evidence',
          (choices.length ? `<form class="controls" data-form="case-add"><select name="thread" required aria-label="Thread to link">
            ${options([['','Select a thread to link'], ...choices.map(row => [row.id,row.title || row.id])])}</select>
            <button class="button primary" type="submit">Link thread</button></form>` : '') +
          table(['Thread','Link','Score','Snapshot','Action'], rows, 'No evidence has been captured.'))}
        ${panel('Review history', reviews || empty('No analyst decisions recorded.'))}
      </div><div class="stack">
        ${panel('Case details', `<form class="form-grid" data-form="case-update">
          <label>Title <input name="title" value="${esc(campaign.title)}" maxlength="200" required></label>
          <label>Owner <input name="owner" value="${esc(campaign.owner || '')}" maxlength="200"></label>
          <label>Status <select name="status">${options(['open','reviewing','closed','dismissed'].map(value => [value,value]),campaign.status)}</select></label>
          <button class="button" type="submit">Save case</button></form>`)}
        ${panel('Analyst review', `<form class="form-grid" data-form="review">
          <label>Reviewer <input name="reviewer" maxlength="200" required></label>
          <label>Decision <select name="decision">${options(Object.entries(decisionLabel))}</select></label>
          <label>Rationale <textarea name="reason" rows="4" maxlength="2000" required></textarea></label>
          <button class="button primary" type="submit">Record review</button></form>`)}
      </div></div>`;
  }

  function sourcePage(sources) {
    const rows = sources.map(row => `<tr><td><strong>${esc(row.ref)}</strong><small>${esc(row.kind)}</small></td>
      <td>${row.active ? (row.last_error ? tag('Error','failed') : tag('Active','ready')) : tag('Paused')}</td>
      <td>${esc(seconds(row.last_attempt))}</td><td>${esc(seconds(row.last_success))}</td>
      <td>${esc(row.last_error || '—')}${row.last_backfill ? '<small>Backfill ' + esc(seconds(row.last_backfill)) + '</small>' : ''}</td>
      <td><div class="row-actions">${button(row.active ? 'Pause' : 'Resume', row.active ? 'source-pause' : 'source-resume',{value:row.id})}
      ${button('Retry','source-retry',{value:row.id})}${button('Backfill','source-backfill',{value:row.id})}</div></td></tr>`);
    const actions = isDemo() ? button('Switch to Live','live-mode',{primary:true}) : button('Add source','add-source',{primary:true,icon:'add'});
    return heading('Sources & Ingestion Management', 'Monitor watched YouTube and public Telegram sources.', actions + button('Refresh','refresh',{icon:'refresh'})) +
      modeBanner() + (isDemo() ? panel('Source management', empty('Source polling is available when connected to FastAPI.')) :
      metrics([['Configured',int(sources.length),'Watched sources'],['Active',int(sources.filter(row=>row.active).length),'Enabled for polling'],
        ['Reporting errors',int(sources.filter(row=>row.last_error).length),'Last recorded poll error'],
        ['Successful polls',int(sources.filter(row=>row.last_success).length),'Sources with a successful poll']]) +
        panel('Source activity', table(['Source','Health','Last attempt','Last success','Latest error','Actions'],rows,'No sources configured.')));
  }

  function alertPage(alerts) {
    const rows = alerts.map(alert => `<tr><td>${severity(alert.level)}</td>
      <td>${link(titleFor(alert.thread_id),'thread',alert.thread_id,{record:true})}${alert.exercise ? '<br>' + exerciseBadge() : ''}</td>
      <td>${esc(seconds(alert.sent_at))}</td><td>${tag((alert.status || 'Recorded').replaceAll('_',' '),alert.status)}</td>
      <td>${int(alert.attempts)}</td><td>${alert.acknowledged_at ? 'Acknowledged · ' + esc(seconds(alert.acknowledged_at)) :
      button('Acknowledge','ack-alert',{value:alert.id})}</td></tr>`);
    return heading('Alerts & Delivery', 'Review delivery attempts and acknowledge alerts independently.',button('Refresh','refresh',{icon:'refresh'})) +
      modeBanner() + panel('Alert queue', table(['Severity','Thread','Recorded','Delivery','Attempts','Acknowledgment'],rows,'No alerts recorded in this mode.'));
  }

  function exercisesPage() {
    return heading('Exercise tools', 'Run a fictional scenario or analyze a pasted comment batch.') + modeBanner() +
      `<div class="split">${panel('Scenario simulator', `<form class="form-grid" data-form="simulate">
        <label>Scenario <select name="scenario"><option value="energy">Energy infrastructure rumor</option><option value="kfor">NATO / KFOR disinformation</option></select></label>
        <label>Accounts <input type="number" name="accounts" min="3" max="60" value="12" required></label>
        <button class="button primary" type="submit">Run exercise scenario</button></form>`)}
      ${panel('Analyze a batch', '<p class="muted">Paste text or JSON into the browser-based analyzer.</p><a class="button" href="./classic.html#batch">Open batch analyzer</a>')}</div>`;
  }

  async function loadThreads() {
    return api('threads');
  }

  async function render({silent = false} = {}) {
    const generation = ++requestGeneration;
    updateChrome();
    const {page,id} = route();
    const current = () => generation === requestGeneration;
    if (!silent) {
      if (map) { map.remove(); map = null; }
      view.innerHTML = '<p class="empty" role="status">Loading workspace…</p>';
      view.setAttribute('aria-busy', 'true');
    }
    try {
      if (page === 'exercise') {
        view.innerHTML = exercisesPage();
      } else if (page === 'thread' && id) {
        const analysis = await api(pathFor(id,'analysis'));
        if (!current()) return;
        if (Boolean(analysis.meta.thread.simulated) !== isExercise()) throw new Error('This thread belongs to a different workspace mode.');
        if (state.analysis?.meta.thread.id !== id) {
          state.activeComment = '';
          state.activeTab = 'overview';
          state.commentQuery = '';
          state.commentLimit = 30;
        }
        state.analysis = analysis;
        paintThread();
      } else if (page === 'cases') {
        const [threads, campaigns, suggestions] = await Promise.all([loadThreads(), api('cases' + queryMode()), api('cases/suggestions' + queryMode())]);
        if (!current()) return;
        state.threads = threads;
        state.suggestions = suggestions;
        view.innerHTML = caseList(campaigns, suggestions);
      } else if (page === 'case' && id) {
        const [threads, campaign] = await Promise.all([loadThreads(), api('cases/' + enc(id))]);
        if (!current()) return;
        if (Boolean(campaign.simulated) !== isExercise()) throw new Error('This case belongs to a different workspace mode.');
        state.threads = threads;
        view.innerHTML = modeBanner() + casePage(campaign);
      } else if (page === 'sources') {
        const sources = await api('sources');
        if (!current()) return;
        view.innerHTML = sourcePage(sources);
      } else if (page === 'alerts') {
        const [threads, alerts] = await Promise.all([loadThreads(), api('alerts')]);
        if (!current()) return;
        state.threads = threads;
        view.innerHTML = alertPage(alerts.filter(alert => Boolean(alert.exercise) === isExercise()));
      } else {
        const threads = await loadThreads();
        if (!current()) return;
        state.threads = threads;
        view.innerHTML = monitorPage();
        const first = filteredThreads().find(row => row.id === state.monitorSelection) || filteredThreads()[0];
        focusThread(first?.id, generation);
      }
      state.authError = '';
    } catch (error) {
      if (!current()) return;
      if (error.status === 401) state.authError = 'Analyst connection required';
      renderError(error);
    } finally {
      if (current()) { view.setAttribute('aria-busy', 'false'); updateChrome(); }
    }
  }

  function setMode(mode) {
    if (state.mode === mode) return;
    state.mode = mode;
    state.filters = {query:'',platform:'',level:'',limit:25};
    search.value = '';
    state.monitorSelection = '';
    const {page} = route();
    if (page === 'thread' || page === 'case') go('monitor');
    else render();
    connectSocket();
  }

  async function connectSocket() {
    const generation = ++socketGeneration;
    clearTimeout(reconnect);
    if (socket) { socket.close(); socket = null; }
    if (isDemo() || (!data.hasKey() && state.health?.auth_required !== false)) { updateChrome(); return; }
    try {
      const {ticket} = await data.api('ws-ticket', {method:'POST'});
      if (generation !== socketGeneration) return;
      socket = new WebSocket(data.origin.replace(/^http/, 'ws') + '/ws/live?ticket=' + enc(ticket));
      socket.onmessage = event => {
        if (generation !== socketGeneration) return;
        let message;
        try { message = JSON.parse(event.data); } catch { return; }
        if (message.type === 'hello') {
          state.authError = '';
          updateChrome();
        }
        const {page,id} = route();
        const relevant = message.type === 'analysis' && (page === 'monitor' || (page === 'thread' && id === message.thread_id))
          || message.type === 'alert_delivery' && page === 'alerts';
        if (!relevant) return;
        clearTimeout(refreshTimer);
        refreshTimer = setTimeout(() => {
          if (generation !== socketGeneration || route().page !== page || route().id !== id || modal.open) return;
          if (view.contains(document.activeElement) && document.activeElement.matches('input, select, textarea, button, a')) return;
          render({silent:true});
        }, 500);
      };
      socket.onclose = () => {
        if (generation !== socketGeneration) return;
        state.authError = 'Live updates disconnected';
        updateChrome();
        reconnect = setTimeout(connectSocket, 3500);
      };
    } catch (error) {
      if (generation !== socketGeneration) return;
      state.authError = error.status === 401 ? 'Analyst connection required' : 'Live updates unavailable';
      updateChrome();
      if (error.status !== 401) reconnect = setTimeout(connectSocket, 5000);
    }
  }

  function download(file, content, type = 'text/plain') {
    const url = URL.createObjectURL(new Blob([content], {type}));
    const anchor = document.createElement('a');
    anchor.href = url;
    anchor.download = file;
    document.body.append(anchor);
    anchor.click();
    anchor.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  }

  function exportCsv() {
    const cell = input => {
      let value = String(input ?? '');
      if (/^\s*[=+\-@\t\r]/.test(value)) value = "'" + value;
      return '"' + value.replaceAll('"','""') + '"';
    };
    const rows = filteredThreads().map(({id,title,platform,n,pct,level,last_ts}) => [id,title,platform,level,n,pct,last_ts]);
    download('thread-queue.csv', [['Thread ID','Title','Platform','Severity','Comments','Flagged fraction','Latest activity in milliseconds'], ...rows]
      .map(row => row.map(cell).join(',')).join('\r\n'), 'text/csv;charset=utf-8');
  }

  function connectDialog() {
    modalOpen('Connect to FastAPI', `<p class="muted">${esc(data.origin)}</p>
      <form class="form-grid" data-form="connect">
        <label>Analyst API key <input type="password" name="key" autocomplete="off" ${state.health?.auth_required !== false ? 'required' : ''}></label>
        <div class="actions"><button class="button primary" type="submit">Connect</button>
        ${data.hasKey() ? button('Clear key','clear-key') : ''}</div>
      </form>`);
  }

  async function caseDialog(id, suggestion = null) {
    const ids = suggestion ? suggestion.thread_ids : [id];
    modalOpen('Open campaign case', `<form class="form-grid" data-form="case-new">
      ${ids.map(tid => '<input type="hidden" name="thread" value="' + esc(tid) + '">').join('')}
      <label>Title <input name="title" value="${esc(suggestion ? 'Coordination across ' + ids.length + ' threads' : titleFor(id).slice(0,190))}" maxlength="200" required></label>
      <label>Owner <input name="owner" maxlength="200"></label>
      <button type="submit" class="button primary">Open case</button></form>`);
  }

  async function linkDialog(id) {
    const campaigns = await api('cases' + queryMode());
    const choices = campaigns.filter(campaign => !campaign.thread_ids.includes(id) && campaign.status !== 'dismissed');
    modalOpen('Link thread to campaign case', choices.length ? `<form class="form-grid" data-form="link-existing">
      <input type="hidden" name="thread" value="${esc(id)}">
      <label>Campaign case <select name="case" required>${options(choices.map(campaign => [campaign.id, campaign.title]))}</select></label>
      <button class="button primary" type="submit">Link thread</button></form>` :
      empty('No available campaign cases.') + button('Open new case','new-case',{value:id,primary:true}));
  }

  async function reportDialog(path, file) {
    const report = await api(path);
    modalOpen('Situation report', `<div class="actions">
      ${button('Download text','download-report',{value:file,icon:'download'})}
      ${button('Print / save PDF','print-report',{icon:'print'})}</div><pre class="report-text" id="report-content">${esc(report)}</pre>`);
  }

  function inspectComment(id) {
    const item = state.analysis?.items.find(item => item.c.id === id);
    if (!item) return;
    state.activeComment = id;
    if (state.activeTab !== 'comments') {
      state.activeTab = 'comments';
      paintThread();
    } else {
      document.getElementById('evidence-panel').innerHTML = panel('Evidence inspector', evidence(item));
      document.querySelectorAll('#comment-rows tr.is-selected').forEach(row => row.classList.remove('is-selected'));
      for (const action of document.querySelectorAll('#comment-rows [data-action=inspect]')) {
        if (action.dataset.value === id) action.closest('tr').classList.add('is-selected');
      }
    }
    const inspector = document.getElementById('evidence-panel');
    if (window.matchMedia('(max-width:1000px)').matches) inspector?.scrollIntoView({block:'nearest'});
  }

  const actions = {
    connect: connectDialog,
    'close-modal': () => modal.close(),
    'clear-key': () => { data.setKey(''); modal.close(); setMode('exercise'); connectSocket(); render(); },
    'local-exercise': () => { data.setKey(''); setMode('exercise'); go('monitor'); },
    'live-mode': () => setMode('live'),
    refresh: () => render(),
    'thread-csv': exportCsv,
    'focus-thread': id => { state.monitorSelection = id; refreshFilters({focus:true}); },
    'more-threads': () => { state.filters.limit += 25; refreshFilters(); },
    tab: value => {
      state.activeTab = value;
      paintThread();
      view.querySelector('[data-action="tab"][aria-pressed="true"]')?.focus();
    },
    inspect: inspectComment,
    account: user => {
      state.commentQuery = user;
      const item = state.analysis.items.find(item => item.c.user === user);
      if (item) inspectComment(item.c.id);
    },
    'more-comments': () => {
      state.commentLimit += 30;
      document.getElementById('comment-rows').innerHTML = commentRows([...state.analysis.items].sort((a,b) => b.score - a.score));
    },
    'new-case': caseDialog,
    'link-case': linkDialog,
    'accept-case': index => caseDialog('', state.suggestions[Number(index)]),
    'dismiss-case': async index => {
      const suggestion = state.suggestions[Number(index)];
      if (!suggestion) return;
      await api('cases', {method:'POST',body:{title:'Dismissed coordination suggestion',simulated:isExercise(),status:'dismissed',thread_ids:suggestion.thread_ids}});
      await render();
      announce('Suggestion dismissed.');
    },
    unlink: async threadId => {
      await api('cases/' + enc(route().id), {method:'PATCH',body:{unlink:[threadId]}});
      await render();
      announce('Thread unlinked. Evidence snapshot preserved.');
    },
    'thread-report': id => reportDialog(pathFor(id,'sitrep'), 'thread-sitrep.txt'),
    'case-report': id => reportDialog('cases/' + enc(id) + '/sitrep', 'case-' + id + '-sitrep.txt'),
    'download-report': file => download(file, document.getElementById('report-content').textContent),
    'print-report': () => window.print(),
    'add-source': () => modalOpen('Add source', `<form class="form-grid" data-form="add-source">
      <label>Source type <select name="kind"><option value="youtube">YouTube</option><option value="telegram">Telegram</option></select></label>
      <label>Video ID or public channel username <input name="ref" maxlength="200" required></label>
      <button class="button primary" type="submit">Add source</button></form>`),
    'ack-alert': async id => {
      await api('alerts/' + enc(id) + '/acknowledge', {method:'POST'});
      await render();
      announce('Alert acknowledged.');
    },
  };

  for (const action of ['pause','resume','retry','backfill']) {
    actions['source-' + action] = async id => {
      const result = await api('sources/' + enc(id) + '/' + action, {method:'POST'});
      await render();
      announce(result.errors ? 'Poll failed. See the latest source error.' : 'Source updated.', Boolean(result.errors));
    };
  }

  const forms = {
    connect: async fields => {
      const key = String(fields.get('key') || '');
      data.setKey(key);
      try {
        const threads = await data.api('threads');
        state.threads = threads;
      } catch (error) {
        data.setKey('');
        throw error;
      }
      modal.close();
      if (state.mode !== 'live') setMode('live');
      else { connectSocket(); await render(); }
      announce('Connected to the analyst workspace.');
    },
    'case-new': async fields => {
      const result = await api('cases', {method:'POST',body:{
        title:fields.get('title'), owner:fields.get('owner') || null, simulated:isExercise(), thread_ids:fields.getAll('thread'),
      }});
      modal.close();
      go('case', result.id);
      announce('Campaign case opened.');
    },
    'link-existing': async fields => {
      const id = fields.get('case');
      await api('cases/' + enc(id), {method:'PATCH',body:{link:[fields.get('thread')]}});
      modal.close();
      go('case', id);
      announce('Thread linked and evidence captured.');
    },
    'case-update': async fields => {
      await api('cases/' + enc(route().id), {method:'PATCH',body:{
        title:fields.get('title'), owner:fields.get('owner') || null, status:fields.get('status'),
      }});
      await render();
      announce('Case saved.');
    },
    'case-add': async fields => {
      await api('cases/' + enc(route().id), {method:'PATCH',body:{link:[fields.get('thread')]}});
      await render();
      announce('Thread linked and evidence captured.');
    },
    review: async fields => {
      await api('cases/' + enc(route().id) + '/reviews', {method:'POST',body:Object.fromEntries(fields)});
      await render();
      announce('Analyst review recorded.');
    },
    'add-source': async fields => {
      await api('sources', {method:'POST',body:Object.fromEntries(fields)});
      modal.close();
      await render();
      announce('Source added.');
    },
    simulate: async fields => {
      if (!isExercise()) setMode('exercise');
      const result = await api('simulate-attack', {method:'POST',body:{
        scenario:fields.get('scenario'), accounts:Number(fields.get('accounts')), spread_threads:2,
      }});
      go('thread', result.primary);
      announce('Exercise added ' + result.new + ' comments across ' + result.thread_ids.length + ' threads.');
    },
  };

  document.addEventListener('click', async event => {
    const target = event.target.closest('[data-action],[data-mode]');
    if (!target) return;
    if (target.dataset.mode) { setMode(target.dataset.mode); return; }
    const invoke = actions[target.dataset.action];
    if (!invoke || target.disabled) return;
    target.disabled = true;
    try { await invoke(target.dataset.value); }
    catch (error) { announce(error.message, true); }
    finally { target.disabled = false; }
  });

  document.addEventListener('submit', async event => {
    const handler = forms[event.target.dataset.form];
    if (!handler) return;
    event.preventDefault();
    const submitter = event.submitter || event.target.querySelector('button[type=submit]');
    if (submitter?.disabled) return;
    if (submitter) submitter.disabled = true;
    try { await handler(new FormData(event.target)); }
    catch (error) { announce(error.message, true); }
    finally { if (submitter) submitter.disabled = false; }
  });

  search.addEventListener('input', () => {
    state.filters.query = search.value;
    state.filters.limit = 25;
    if (route().page !== 'monitor') go('monitor');
    else if (document.getElementById('queue-rows')) {
      document.getElementById('monitor-filter').value = search.value;
      refreshFilters();
    }
  });
  view.addEventListener('input', event => {
    if (event.target.id === 'monitor-filter') {
      state.filters.query = event.target.value;
      state.filters.limit = 25;
      search.value = event.target.value;
      refreshFilters();
    }
    if (event.target.id === 'comment-filter') {
      state.commentQuery = event.target.value;
      state.commentLimit = 30;
      document.getElementById('comment-rows').innerHTML = commentRows([...state.analysis.items].sort((a,b) => b.score - a.score));
    }
  });
  view.addEventListener('change', event => {
    if (!['filter-platform','filter-level'].includes(event.target.id)) return;
    const field = event.target.id === 'filter-platform' ? 'platform' : 'level';
    state.filters[field] = event.target.value;
    state.filters.limit = 25;
    refreshFilters();
  });
  document.addEventListener('keydown', event => {
    if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'k' && !modal.open) {
      event.preventDefault();
      search.focus();
    }
  });
  window.addEventListener('hashchange', () => {
    if (location.hash === '#view') return;
    render();
  });
  render();
  data.api('health', {authenticated:false, signal:AbortSignal.timeout(20_000)}).then(health => {
    const wasDemo = isDemo();
    state.health = health;
    if (wasDemo && !isDemo()) render();
    updateChrome();
    connectSocket();
  }).catch(() => {
    if (!isDemo()) { state.authError = 'Backend unavailable'; updateChrome(); }
  });
})();
