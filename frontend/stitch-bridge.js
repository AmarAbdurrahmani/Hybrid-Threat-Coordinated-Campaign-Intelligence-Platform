/* Binds the original Stitch pages to the analyst workspace API. */
(() => {
  const data = window.workspaceData;
  const page = document.body.dataset.stitchPage;
  const params = new URLSearchParams(location.search);
  const $ = (selector, parent = document) => parent.querySelector(selector);
  const all = (selector, parent = document) => [...parent.querySelectorAll(selector)];
  const region = name => $('[data-stitch-region="' + name + '"]');
  const cls = {
    card:'bg-surface-container-low p-space-md flex flex-col gap-space-sm',
    button:'inline-flex items-center gap-1 px-3 py-1.5 bg-surface-container-high hover:bg-surface-variant text-primary font-label-md text-label-md uppercase',
    subtle:'text-on-surface-variant font-body-sm text-body-sm',
    cell:'py-2 px-3',
    value:'font-headline-md text-headline-md text-primary',
  };
  const severity = {1:'L1 - Critical',2:'L2 - High',3:'L3 - Moderate',4:'L4 - Baseline'};
  const severityColor = {1:'text-error',2:'text-secondary',3:'text-secondary',4:'text-on-surface-variant'};
  const names = {temporal:'Temporal',linguistic:'Linguistic',narrative:'Narrative',network:'Network'};
  const reasons = {temporal:'tReasons',linguistic:'lReasons',narrative:'nReasons',network:'gReasons'};
  const choices = {likely_coordinated:'Likely coordinated',organic:'Organic',inconclusive:'Inconclusive'};
  const BUCKET_MS = 5 * 60_000;
  const COMMENTS_PER_PAGE = 50;
  const ranges = [[72,'6H'],[144,'12H'],[0,'All']];
  const enc = encodeURIComponent;
  const state = {
    mode: ['preview','exercise','live'].includes(params.get('mode')) ? params.get('mode') : data.hasKey() ? 'live' : 'preview',
    threads: [], selected: params.get('thread'), filter:'', platform:'', level:'', flagged:false, limit:25, tab:'overview',
    analysis:null, campaign:null, cases:[], suggestions:[],
    chartWindow:72, bucket:null, commentPage:0, commentQuery:'', commentTier:'',
  };
  let refreshTimer, toastTimer, requestVersion = 0;
  const demo = () => state.mode === 'exercise' && !data.hasKey();
  const modeQuery = () => '?simulated=' + (state.mode === 'exercise');
  const api = (path, options) => demo() ? Promise.resolve().then(() => data.exercise(path, options)) : data.api(path,options);
  const number = value => Number.isFinite(value) ? value.toLocaleString() : '—';
  const percent = value => Number.isFinite(value) ? (100 * value).toFixed(1) + '%' : '—';
  const date = value => Number.isFinite(value) && value > 0 ? new Date(value).toLocaleString() : '—';
  const threadPath = (id, method) => 'threads/' + enc(id) + '/' + method;
  const topFilter = $('header input');
  const notices = node('div','stitch-notice');
  notices.hidden = true;
  notices.setAttribute('role','status');
  document.body.append(notices);
  const dialog = node('dialog','stitch-dialog');
  dialog.setAttribute('aria-labelledby','stitch-dialog-title');
  document.body.append(dialog);

  function node(tag, className = '', value) {
    const result = document.createElement(tag);
    if (className) result.className = className;
    if (value != null) result.textContent = String(value);
    return result;
  }
  function add(parent, tag, className, value) {
    const child = node(tag,className,value);
    parent.append(child);
    return child;
  }
  function replace(parent, ...children) { if (parent) parent.replaceChildren(...children); }
  function button(label, handler) {
    const element = node('button',cls.button,label);
    element.type = 'button';
    element.addEventListener('click', () => work(element,handler));
    return element;
  }
  function link(label, href, className = cls.button) {
    const element = node('a',className,label);
    element.href = href;
    return element;
  }
  function cleanUrl(url) {
    try { const target = new URL(url); return /^https?:$/.test(target.protocol) ? target.href : ''; }
    catch { return ''; }
  }
  function sourceLink(label, url) {
    const address = cleanUrl(url);
    if (!address) return node('span',cls.subtle,'Original source link unavailable');
    const anchor = link(label,address);
    anchor.target = '_blank';
    anchor.rel = 'noopener noreferrer';
    return anchor;
  }
  function destination(file, query = {}) {
    const target = new URL('./' + file, location.href);
    for (const [key,value] of Object.entries({api:params.get('api'),mode:state.mode,...query})) {
      if (value != null && value !== '') target.searchParams.set(key,value);
    }
    return target.href;
  }
  function jump(file, query) { location.href = destination(file,query); }
  function supplemental(hash) { return destination('workspace.html') + '#' + hash; }
  function status(text, error = false) {
    clearTimeout(toastTimer);
    notices.hidden = false;
    notices.textContent = text;
    notices.setAttribute('role',error ? 'alert' : 'status');
    toastTimer = setTimeout(() => { notices.hidden = true; },6000);
  }
  async function work(control, handler) {
    if (control?.disabled) return;
    if (control) control.disabled = true;
    try { await handler(); } catch (error) { status(error.message,true); }
    finally { if (control) control.disabled = false; }
  }
  function modal(title, builder) {
    const header = node('div','flex items-center justify-between gap-space-md mb-3');
    const heading = add(header,'h2','font-headline-md text-headline-md text-primary',title);
    heading.id = 'stitch-dialog-title';
    header.append(button('Close',() => dialog.close()));
    dialog.replaceChildren(header);
    builder(dialog);
    if (!dialog.open) dialog.showModal();
  }
  function form(parent, label, handler) {
    const element = add(parent,'form','flex flex-col gap-3');
    const submit = node('button',cls.button,label);
    submit.type = 'submit';
    element.addEventListener('submit',event => {
      event.preventDefault();
      work(submit, () => handler(new FormData(element)));
    });
    return {element, submit};
  }
  function field(parent,label,name,type = 'text',value = '') {
    const labelElement = add(parent,'label','flex flex-col gap-1');
    add(labelElement,'span','font-label-md text-label-md uppercase',label);
    const input = add(labelElement, type === 'textarea' ? 'textarea' : 'input');
    if (type !== 'textarea') input.type = type;
    input.name = name;
    input.value = value;
    input.required = true;
    return input;
  }
  function select(parent,label,name,options) {
    const labelElement = add(parent,'label','flex flex-col gap-1');
    add(labelElement,'span','font-label-md text-label-md uppercase',label);
    const input = add(labelElement,'select','stitch-field');
    input.name = name;
    input.required = true;
    options.forEach(([value,text]) => { add(input,'option','',text).value = value; });
    return input;
  }
  function section(title) {
    const element = node('section',cls.card);
    add(element,'h2','font-headline-sm text-headline-sm text-primary uppercase',title);
    return element;
  }
  function labelValue(parent,label,value) {
    add(parent,'strong','font-label-sm text-label-sm text-outline uppercase',label);
    add(parent,'p','font-body-sm text-body-sm text-on-surface',value);
  }
  function download(filename, content, type = 'text/plain') {
    const url = URL.createObjectURL(new Blob([content],{type}));
    const anchor = link('',url,'');
    anchor.download = filename;
    document.body.append(anchor);
    anchor.click();
    anchor.remove();
    setTimeout(() => URL.revokeObjectURL(url),1000);
  }
  function csv(rows,filename) {
    const value = input => {
      const cell = String(input ?? '');
      return '"' + (/^\s*[=+\-@]/.test(cell) ? "'" : '') + cell.replaceAll('"','""') + '"';
    };
    download(filename,rows.map(row=>row.map(value).join(',')).join('\r\n'),'text/csv;charset=utf-8');
  }

  function header() {
    const label = state.mode === 'preview' ? 'STITCH PREVIEW · EXERCISE' : state.mode === 'exercise' ? 'EXERCISE DATA' : 'LIVE · FASTAPI';
    const nodes = all('header span');
    const text = nodes.find(el => el.children.length === 0 && /ONLINE.*INGEST ACTIVE/.test(el.textContent));
    if (text) text.textContent = label;
    const headerStatus = $('header [data-stitch-status]');
    if (headerStatus) headerStatus.textContent = label;
    else if (!text) {
      const badge = add($('header'),'span','text-secondary font-label-sm text-label-sm',label);
      badge.dataset.stitchStatus = '';
    }
    if (state.mode !== 'preview') {
      all('aside nav a').forEach(anchor => {
        all('span',anchor).filter(span => !span.children.length && /^\d+\s/.test(span.textContent.trim())).forEach(span => span.hidden = true);
      });
    }
    const operator = nodes.find(el => el.children.length === 0 && el.textContent.trim() === 'Analyst J. Vance');
    if (operator) operator.textContent = state.mode === 'preview' ? 'Example analyst' : 'Analyst workspace';
    const rank = nodes.find(el => el.children.length === 0 && el.textContent.trim() === 'G-SOC LEVEL 3');
    if (rank) rank.textContent = state.mode === 'preview' ? 'STITCH SAMPLE' : state.mode.toUpperCase();
    for (const anchor of all('aside a')) {
      const target = new URL(anchor.href, location.href);
      if (target.origin !== location.origin) continue;
      if (params.has('api')) target.searchParams.set('api',params.get('api'));
      target.searchParams.set('mode',state.mode);
      anchor.href = target.href;
    }
  }

  function setMode(mode) {
    if (mode === 'live' && !data.hasKey()) return connect();
    jump(page === 'monitor' ? 'index.html' : page + '.html',{mode});
  }
  function connect() {
    modal('Connect to FastAPI', box => {
      add(box,'p',cls.subtle,data.origin);
      const {element,submit} = form(box,'Connect',async values => {
        data.setKey(String(values.get('key') || ''));
        try { await data.api('threads'); } catch (error) { data.setKey(''); throw error; }
        jump('index.html',{mode:'live'});
      });
      field(element,'Analyst API key','key','password').autocomplete = 'off';
      element.append(submit);
    });
  }
  function filteredThreads() {
    const query = state.filter.trim().toLowerCase();
    return state.threads.filter(row => Boolean(row.simulated) === (state.mode === 'exercise') &&
      (!state.platform || state.platform === row.platform) && (!state.flagged || row.pct > 0) &&
      (!state.level || String(row.level || '') === state.level) &&
      (!query || [row.id,row.title,row.platform,row.url].some(value => String(value || '').toLowerCase().includes(query))));
  }
  function renderMonitor() {
    const body = $('#triage-table-body');
    const rows = filteredThreads();
    const current = rows.find(row => row.id === state.selected) || rows[0];
    state.selected = current?.id || '';
    replace(body,...rows.slice(0,state.limit).map((row,index) => {
      const element = node('tr',row.id === state.selected ? 'bg-primary/5' : 'hover:bg-surface-container-highest/20');
      add(element,'td',cls.cell,index + 1);
      add(element,'td',cls.cell + ' font-bold uppercase ' + (severityColor[row.level] || cls.subtle),severity[row.level] || 'Analysis pending');
      const identity = add(element,'td',cls.cell);
      identity.append(link(row.title || row.id,destination('investigation.html',{thread:row.id}),'text-on-surface font-bold hover:text-primary'));
      add(identity,'p',cls.subtle,(row.platform || 'Source') + ' · ' + row.id);
      add(element,'td',cls.cell,number(row.n));
      add(element,'td',cls.cell + ' text-primary',percent(row.pct));
      add(element,'td',cls.cell,date(row.last_ts));
      add(element,'td',cls.cell,'—');
      const campaign = state.cases.find(item => item.thread_ids.includes(row.id));
      const caseCell = add(element,'td',cls.cell);
      if (campaign) caseCell.append(link('Case ' + campaign.id,destination('campaign.html',{case:campaign.id})));
      else caseCell.textContent = 'Unassigned';
      add(element,'td',cls.cell).append(button('Focus',() => { state.selected = row.id; renderMonitor(); }));
      return element;
    }));
    if (!rows.length) {
      const emptyRow = add(body,'tr');
      const cell = add(emptyRow,'td','p-space-md ' + cls.subtle,'No threads match these filters.');
      cell.colSpan = 9;
    }
    const more = $('[data-stitch-action=more]');
    more.hidden = rows.length <= state.limit;
    more.textContent = 'Load more (' + Math.max(0,rows.length-state.limit) + ' remaining)';
    const metrics = [...region('monitor-metrics').children];
    const groups = rows.filter(row => row.level === 1 || row.level === 2).length;
    const scored = rows.filter(row => Number.isFinite(row.pct));
    const average = scored.length ? scored.reduce((total,row) => total+row.pct,0)/scored.length : null;
    const outputs = [
      ['Monitored threads',number(rows.length),'In the current filter'],
      ['Escalated threads',number(groups),'Level 1 or Level 2'],
      ['Campaign cases',number(state.cases.length),'In the selected mode'],
      ['Average flagged share',percent(average),'Across scored threads'],
    ];
    metrics.forEach((card,index) => {
      const [title,value,context] = outputs[index];
      const iconSlot = $('.material-symbols-outlined',card);
      const metricHeader = node('div','flex justify-between items-center');
      add(metricHeader,'span','font-label-sm text-label-sm uppercase font-bold',title);
      if (iconSlot) metricHeader.append(iconSlot);
      replace(card,metricHeader,node('strong','font-headline-lg text-headline-lg text-primary',value),node('span',cls.subtle,context));
    });
    const inspector = section('Triage focus · Selected thread');
    if (!current) add(inspector,'p',cls.subtle,'Select a thread to inspect its evidence.');
    else {
      add(inspector,'strong','font-headline-sm text-headline-sm',current.title || current.id);
      labelValue(inspector,'Source',current.platform || 'Unavailable');
      labelValue(inspector,'Severity',severity[current.level] || 'Analysis pending');
      labelValue(inspector,'Latest activity',date(current.last_ts));
      labelValue(inspector,'Flagged share',percent(current.pct));
      inspector.append(link('Open full workspace',destination('investigation.html',{thread:current.id})));
    }
    replace(region('inspector'),inspector);
  }
  function previewRows() { return all('[data-stitch-preview-row]'); }
  function filterPreview() {
    for (const row of previewRows()) {
      const text = row.textContent.toLowerCase();
      row.hidden = Boolean((state.filter && !text.includes(state.filter.toLowerCase())) ||
        (state.platform === 'telegram' && !text.includes('@')) ||
        (state.platform === 'youtube' && !text.includes('vid:')) ||
        (state.level && !text.includes('l'+state.level+' -')) ||
        (state.flagged && !/l[123] -/.test(text)));
    }
  }
  function applyFilters() { if (state.mode === 'preview') filterPreview(); else renderMonitor(); }
  function selectPreview(row) {
    const label = $('td:nth-child(3) a',row)?.textContent.trim();
    const body = region('inspector');
    const title = $('.font-headline-sm.font-bold',body);
    if (title && label) title.textContent = label;
    const id = row.textContent.match(/th_[0-9a-f]+/i)?.[0];
    if (id) $('h2',body).textContent = id;
    previewRows().forEach(candidate => candidate.style.outline = candidate === row ? '1px solid #2dd4bf' : '');
  }
  function timelineBuckets(items, windowSize) {
    const counts = new Map();
    let last = -Infinity;
    for (const item of items) {
      const time = item.t ?? item.c.ts;
      if (!Number.isFinite(time)) continue;
      const start = Math.floor(time / BUCKET_MS) * BUCKET_MS;
      counts.set(start,(counts.get(start) || 0) + 1);
      last = Math.max(last,start);
    }
    if (!counts.size) return [];
    if (!windowSize) return [...counts].sort(([left],[right])=>left-right);
    return Array.from({length:windowSize},(_,index) => {
      const start = last - (windowSize - 1 - index) * BUCKET_MS;
      return [start, counts.get(start) || 0];
    });
  }
  function updateTimelineSelection() {
    all('[data-stitch-bucket]').forEach(bar =>
      bar.setAttribute('aria-pressed',String(Number(bar.dataset.stitchBucket) === state.bucket)));
  }
  function chooseBucket(start) {
    state.bucket = state.bucket === start ? null : start;
    state.commentPage = 0;
    updateTimelineSelection();
    renderDetail('comments');
    replace(region('thread-evidence'),renderEvidence(commentPage(state.analysis.items,state).rows[0]));
    region('thread-narratives').scrollIntoView({block:'nearest',behavior:'smooth'});
  }
  function renderTimeline() {
    const chartPanel = region('thread-chart').firstElementChild;
    const content = section('5-minute comment burst velocity');
    const windows = add(content,'div','flex flex-wrap gap-2');
    windows.setAttribute('aria-label','Timeline range');
    for (const [size,label] of ranges) {
      const control = button(label,() => {
        state.chartWindow = size;
        renderTimeline();
        $('[data-stitch-window="'+size+'"]')?.focus({preventScroll:true});
      });
      control.dataset.stitchWindow = size;
      control.setAttribute('aria-pressed',String(size === state.chartWindow));
      windows.append(control);
    }
    const buckets = timelineBuckets(state.analysis.items,state.chartWindow);
    if (!buckets.length) {
      add(content,'p',cls.subtle,'No comment timestamps are available.');
      replace(chartPanel,content);
      return;
    }
    add(content,'p',cls.subtle,state.chartWindow ? 'Select a bar to filter comments to that five-minute interval.' : 'All intervals with comments, in chronological order.');
    const viewport = add(content,'div','overflow-x-auto');
    const plot = add(viewport,'div','stitch-live-chart');
    plot.setAttribute('role','group');
    plot.setAttribute('aria-label','Comment counts in five-minute intervals');
    const peak = buckets.reduce((maximum,[,count])=>Math.max(maximum,count),1);
    for (const [start,count] of buckets) {
      if (!count) { add(plot,'span','stitch-empty-bucket'); continue; }
      const bar = node('button','stitch-bucket');
      bar.type = 'button';
      bar.dataset.stitchBucket = start;
      bar.setAttribute('aria-label','Filter comments from '+date(start)+' to '+date(start+BUCKET_MS)+' ('+count+' comments)');
      bar.title = date(start) + ' · ' + count + ' comments';
      bar.style.height = Math.max(6,100*count/peak)+'px';
      bar.addEventListener('click',()=>chooseBucket(start));
      plot.append(bar);
    }
    replace(chartPanel,content);
    updateTimelineSelection();
  }
  function commentPage(items,{bucket,commentTier,commentQuery,commentPage}) {
    const query = commentQuery.trim().toLowerCase();
    const time = item => item.t ?? item.c.ts;
    const order = item => Number.isFinite(time(item)) ? time(item) : Infinity;
    const ordered = items.filter(item =>
      (bucket === null || (Number.isFinite(time(item)) && time(item) >= bucket && time(item) < bucket + BUCKET_MS)) &&
      (!commentTier || item.tier === commentTier) &&
      (!query || String(item.c.text || '').toLowerCase().includes(query) || String(item.c.user || '').toLowerCase().includes(query.replace(/^@/,''))))
      .sort((a,b)=>order(a)-order(b));
    const last = Math.max(0,Math.ceil(ordered.length/COMMENTS_PER_PAGE)-1);
    const page = Math.min(Math.max(0,commentPage),last);
    const from = page * COMMENTS_PER_PAGE;
    return {rows:ordered.slice(from,from+COMMENTS_PER_PAGE),total:ordered.length,page,from,last};
  }
  function commentInspector(analysis) {
    const content = section('Comments');
    const filter = add(content,'div','flex flex-wrap items-center gap-2');
    const search = add(filter,'input','stitch-field');
    search.type = 'search';
    search.placeholder = 'Filter by text or account…';
    search.value = state.commentQuery;
    search.setAttribute('aria-label','Filter comments');
    const tiers = add(filter,'select','stitch-field');
    tiers.setAttribute('aria-label','Coordination tier');
    for (const [value,label] of [['','All tiers'],['high','High'],['medium','Medium'],['organic','Organic']]) add(tiers,'option','',label).value = value;
    tiers.value = state.commentTier;
    if (state.bucket !== null) filter.append(button('Clear interval · '+date(state.bucket),()=> {
      chooseBucket(state.bucket);
      $('[aria-label="Filter comments"]',region('thread-narratives'))?.focus({preventScroll:true});
    }));
    const list = add(content,'div','flex flex-col gap-2 max-h-[26rem] overflow-y-auto');
    const nav = add(content,'nav','flex items-center justify-between gap-2');
    nav.setAttribute('aria-label','Comment pages');
    const previous = pageButton('Previous',-1);
    const pageLabel = node('span',cls.subtle);
    pageLabel.setAttribute('aria-live','polite');
    const next = pageButton('Next',1);
    nav.append(previous,pageLabel,next);
    function updateRows() {
      const {rows,total,page,from,last} = commentPage(analysis.items,state);
      state.commentPage = page;
      list.replaceChildren();
      if (!rows.length) add(list,'p',cls.subtle,'No comments match the selected filters.');
      for (const item of rows) {
        list.append(button(item.c.user + ' · ' + number(item.score) + '/100',() => replace(region('thread-evidence'),renderEvidence(item))));
        add(list,'p',cls.subtle,item.c.text);
      }
      pageLabel.textContent = total ? (from+1) + '–' + (from+rows.length) + ' of ' + total : '0 comments';
      previous.disabled = page === 0;
      next.disabled = page === last;
      list.scrollTop = 0;
    }
    function pageButton(label,delta) {
      const control = node('button',cls.button,label);
      control.type = 'button';
      control.addEventListener('click',()=> {
        state.commentPage += delta;
        updateRows();
        replace(region('thread-evidence'),renderEvidence(commentPage(analysis.items,state).rows[0]));
      });
      return control;
    }
    search.addEventListener('input',()=> { state.commentQuery = search.value; state.commentPage = 0; updateRows(); });
    tiers.addEventListener('change',()=> { state.commentTier = tiers.value; state.commentPage = 0; updateRows(); });
    updateRows();
    return content;
  }
  function renderEvidence(item) {
    const view = section('Evidence inspector');
    if (!item) { add(view,'p',cls.subtle,'Select a comment to inspect its signals.'); return view; }
    labelValue(view,'Pseudonymous account',item.c.user);
    labelValue(view,'Coordination score',number(item.score) + '/100');
    labelValue(view,'Comment',item.c.text);
    for (const [key,label] of Object.entries(names)) {
      labelValue(view,label,Number.isFinite(item[key]) ? item[key]+'/100' : 'Unavailable');
      for (const reason of item[reasons[key]] || []) add(view,'p',cls.subtle,reason);
    }
    view.append(sourceLink('Open original comment',item.c.url));
    return view;
  }
  function renderDetail(tab) {
    state.tab = tab;
    const analysis = state.analysis;
    all('[id^=tab-]').forEach(button => {
      const active = button.id === 'tab-'+tab;
      button.setAttribute('aria-pressed',String(active));
      button.classList.toggle('text-primary',active);
    });
    if (!analysis) {
      for (const name of ['signals','narratives','network']) region('thread-'+name).hidden = tab !== 'overview' && tab !== name;
      if (tab === 'comments' || tab === 'infra') {
        const block = region('thread-narratives');
        block.hidden = false;
      }
      return;
    }
    const signals = region('thread-signals'), narratives = region('thread-narratives'), network = region('thread-network');
    signals.hidden = tab !== 'overview';
    narratives.hidden = false;
    network.hidden = true;
    if (tab === 'overview') {
      const cards = Object.entries(names).map(([id,label]) => {
        const card = section(label);
        const value = analysis.thread.signalAvg?.[id]?.flagged;
        add(card,'strong',cls.value,Number.isFinite(value) ? value + '/100' : 'Unavailable');
        add(card,'p',cls.subtle,'Average among flagged comments');
        return card;
      });
      replace(signals,...cards);
      const content = section('Recurring narratives');
      for (const [label,count] of analysis.thread.narratives || []) labelValue(content,label,number(count) + ' matches');
      if (!analysis.thread.narratives?.length) add(content,'p',cls.subtle,'No recurring narratives were identified.');
      replace(narratives,content);
    } else if (tab === 'comments') {
      replace(narratives,commentInspector(analysis));
    } else if (tab === 'network') {
      const content = section('Network & clusters');
      for (const cluster of analysis.clusters) labelValue(content,cluster.id,number(cluster.size) + ' accounts · ' + cluster.edgeTypes.join(', '));
      if (!analysis.clusters.length) add(content,'p',cls.subtle,'No coordinated clusters identified.');
      replace(narratives,content);
    } else {
      const content = section('Observed infrastructure');
      for (const row of analysis.infra) labelValue(content,row.node,[row.asn,row.city,row.cc].filter(Boolean).join(' · ') || 'Location unavailable');
      if (!analysis.infra.length) add(content,'p',cls.subtle,'This source supplies no infrastructure telemetry.');
      replace(narratives,content);
    }
  }
  function renderInvestigation() {
    const analysis = state.analysis, {thread,meta} = analysis;
    const label = meta.thread;
    $('h1').textContent = label.title || label.id;
    const subtitle = $('h1').previousElementSibling;
    if (subtitle) subtitle.textContent = [label.platform,label.id,label.simulated ? 'EXERCISE' : 'LIVE'].filter(Boolean).join(' // ');
    const top = region('thread-toolbar');
    const badge = $('.flex.items-center.gap-2',top);
    if (badge) badge.textContent = severity[thread.level] || 'Analysis pending';
    const header = region('thread-header');
    const oldSource = $('a', header);
    if (oldSource) oldSource.replaceWith(sourceLink('Open source thread',label.url));
    const cards = [...region('thread-metrics').children];
    ['Total comments','Flagged share','Largest cluster','Peak burst'].forEach((title,index) => {
      const value = [number(thread.n),percent(thread.pct),number(thread.largestCluster),number(thread.peak?.count)][index];
      const iconSlot = $('.material-symbols-outlined',cards[index]);
      const heading = node('div','flex justify-between items-center');
      add(heading,'span','font-label-sm text-label-sm uppercase',title);
      if (iconSlot) heading.append(iconSlot);
      replace(cards[index],heading,node('strong','font-headline-lg text-headline-lg text-primary',value));
    });
    renderTimeline();
    replace(region('thread-evidence'),renderEvidence([...analysis.items].sort((a,b)=>b.score-a.score)[0]));
    region('thread-footer').textContent = 'Synapse Intel · Scoring version ' + (meta.scoring_version || '1');
    renderDetail(state.tab);
  }
  function newCase(ids, {dismissed = false} = {}) {
    if (!ids.length) throw new Error('Choose a thread before creating a case.');
    const initial = ids.length === 1 ? (state.analysis?.meta.thread.title || state.threads.find(thread => thread.id === ids[0])?.title || 'Campaign review') :
      'Coordination across ' + ids.length + ' threads';
    if (dismissed) return createCase(initial,ids,'dismissed');
    modal('Create new case',box => {
      const {element,submit} = form(box,'Open case',async values => {
        const campaign = await createCase(String(values.get('title')),ids,'open',String(values.get('owner') || ''));
        jump('campaign.html',{case:campaign.id});
      });
      field(element,'Title','title','text',initial).maxLength = 200;
      field(element,'Owner','owner').required = false;
      element.append(submit);
    });
  }
  function createCase(title,ids,status,owner = '') {
    return api('cases',{method:'POST',body:{title,owner:owner || null,simulated:state.mode === 'exercise',status,thread_ids:ids}});
  }
  async function assignCase(threadId) {
    if (!threadId) return useExampleThread();
    const campaigns = await api('cases' + modeQuery());
    const options = campaigns.filter(item => !item.thread_ids.includes(threadId) && item.status !== 'dismissed');
    modal('Assign thread to case',box => {
      if (options.length) {
        const {element,submit} = form(box,'Link thread',async fields => {
          const id = String(fields.get('case'));
          await api('cases/'+enc(id),{method:'PATCH',body:{link:[threadId]}});
          jump('campaign.html',{case:id});
        });
        select(element,'Campaign case','case',options.map(item=>[item.id,item.title]));
        element.append(submit);
      } else add(box,'p',cls.subtle,'No existing case can accept this thread.');
      box.append(button('Create a new case',() => newCase([threadId])));
    });
  }
  function casePicker() {
    const card = section('Campaign case queue');
    for (const campaign of state.cases) {
      const row = add(card,'p','flex items-center gap-2');
      row.append(link(campaign.title,destination('campaign.html',{case:campaign.id})));
      add(row,'span',cls.subtle,campaign.status + ' · ' + campaign.thread_ids.length + ' linked threads');
    }
    if (!state.cases.length) add(card,'p',cls.subtle,'No campaign cases in this mode.');
    if (state.threads.length) {
      const {element,submit} = form(card,'Create case',fields=>newCase([String(fields.get('thread'))]));
      select(element,'Thread','thread',state.threads.map(thread=>[thread.id,thread.title || thread.id]));
      element.append(submit);
    }
    return card;
  }
  function caseSuggestions() {
    const card = section('Suggested cases');
    for (const suggestion of state.suggestions) {
      const text = suggestion.thread_ids.map(id=>state.threads.find(row=>row.id===id)?.title || id).join(' · ');
      add(card,'p','font-body-sm text-body-sm',text);
      const actions = add(card,'div','flex flex-wrap gap-2');
      actions.append(button('Open case',()=>newCase(suggestion.thread_ids)),button('Dismiss',async () => {
        await newCase(suggestion.thread_ids,{dismissed:true});
        await load();
      }));
    }
    if (!state.suggestions.length) add(card,'p',cls.subtle,'No new case suggestions.');
    return card;
  }
  function snapshotDetails(value) {
    const box = node('div','flex flex-col gap-2');
    labelValue(box,'Archived analysis',value.scoring_version || 'Version unavailable');
    labelValue(box,'Captured',date(value.captured_at));
    for (const comment of value.comments || []) {
      labelValue(box,comment.user || comment.id,comment.text);
      if (comment.url) box.append(sourceLink('Original comment',comment.url));
    }
    return box;
  }
  function renderCampaign() {
    const campaign = state.campaign;
    replace(region('case-suggestions'),caseSuggestions());
    replace(region('case-summary'),casePicker());
    if (!campaign) {
      $('h1').textContent = 'Campaign cases & SitRep review';
      for (const name of ['evidence','chart','review','history','report']) region('case-'+name).hidden = true;
      return;
    }
    $('h1').textContent = campaign.title;
    const header = region('case-header');
    const title = $('h1',header);
    const sampleBadges = title.parentElement.previousElementSibling;
    if (sampleBadges) sampleBadges.hidden = true;
    const metadata = title.nextElementSibling;
    metadata.textContent =
      'Case ' + campaign.id + ' · ' + campaign.status + ' · ' + (campaign.owner || 'Unassigned') +
      (campaign.simulated ? ' · EXERCISE' : '') + ' · ' + date(campaign.created_at*1000);
    for (const name of ['evidence','review','history','report']) region('case-'+name).hidden = false;
    region('case-chart').hidden = true;
    const evidence = section('Linked threads & preserved evidence');
    const active = new Set(campaign.thread_ids);
    for (const [id,value] of Object.entries(campaign.evidence || {})) {
      const row = add(evidence,'div','bg-surface-container p-space-sm flex flex-col gap-2');
      row.append(link(value.thread.title || id,destination('investigation.html',{thread:id})));
      add(row,'strong','font-label-sm text-label-sm uppercase',active.has(id) ? 'Linked' : 'Unlinked · Snapshot retained');
      const controls = add(row,'div','flex flex-wrap gap-2');
      controls.append(button('Inspect snapshot',()=>modal('Preserved evidence',box=>box.append(snapshotDetails(value)))));
      if (active.has(id)) controls.append(button('Unlink',async () => {
        await api('cases/'+enc(campaign.id),{method:'PATCH',body:{unlink:[id]}});
        await load();
      }));
    }
    if (!Object.keys(campaign.evidence || {}).length) add(evidence,'p',cls.subtle,'No evidence snapshots have been captured.');
    replace(region('case-evidence'),evidence);
    const review = section('Human analyst review');
    const {element,submit} = form(review,'Record review',async values => {
      await api('cases/'+enc(campaign.id)+'/reviews',{method:'POST',body:Object.fromEntries(values)});
      await load();
      status('Review recorded.');
    });
    field(element,'Reviewer','reviewer').maxLength = 200;
    select(element,'Decision','decision',Object.entries(choices));
    field(element,'Rationale','reason','textarea').maxLength = 2000;
    element.append(submit);
    replace(region('case-review'),review);
    const history = section('Official case review history');
    for (const entry of campaign.reviews) labelValue(history,entry.reviewer+' · '+(choices[entry.decision] || entry.decision),entry.reason);
    if (!campaign.reviews.length) add(history,'p',cls.subtle,'No analyst reviews recorded.');
    replace(region('case-history'),history);
    const report = section('Formatted SitRep preview');
    report.append(button('Preview SitRep',()=>previewReport('cases/'+enc(campaign.id)+'/sitrep','case-'+campaign.id+'-sitrep.txt')));
    replace(region('case-report'),report);
  }
  function linkThread() {
    if (!state.campaign) throw new Error('Open a case to link a thread.');
    const available = state.threads.filter(thread => !state.campaign.thread_ids.includes(thread.id));
    if (!available.length) throw new Error('No additional threads are available in this mode.');
    modal('Link thread & capture snapshot',box => {
      const {element,submit} = form(box,'Link & capture',async fields => {
        await api('cases/'+enc(state.campaign.id),{method:'PATCH',body:{link:[String(fields.get('thread'))]}});
        dialog.close();
        await load();
      });
      select(element,'Thread','thread',available.map(thread=>[thread.id,thread.title || thread.id]));
      element.append(submit);
    });
  }
  async function previewReport(path,filename) {
    const report = path ? await api(path) : 'EXERCISE · STITCH SAMPLE\n\n'+(region('case-report') || region('thread-evidence')).innerText;
    modal('Situation report',box => {
      const actions = add(box,'div','flex flex-wrap gap-2');
      actions.append(button('Download text',()=>download(filename,report)),button('Print / Save PDF',()=>window.print()));
      add(box,'pre','stitch-report mt-3 bg-surface-container-lowest p-space-sm',report);
    });
  }
  function useExampleThread() {
    const [thread] = data.exercise('threads');
    jump('investigation.html',{mode:'exercise',thread:thread.id});
  }
  function setFilter({platform = state.platform,level = state.level,flagged = state.flagged} = {}) {
    state.platform = platform;
    state.level = level;
    state.flagged = flagged;
    state.limit = 25;
    applyFilters();
  }
  function openThread() {
    if (state.mode !== 'preview') {
      if (state.selected) jump('investigation.html',{thread:state.selected});
      return;
    }
    jump('investigation.html',{mode:'preview'});
  }
  const actions = {
    'live-mode':()=>setMode('live'),
    'exercise-mode':()=>setMode('exercise'),
    'all-modes':()=>jump('index.html',{mode:'preview'}),
    refresh:()=>state.mode === 'preview' ? location.reload() : load(),
    'exercise-tools':()=>{ location.href = supplemental('exercise'); },
    'open-thread':openThread,
    'assign-case':()=>assignCase(state.selected),
    'new-case':()=>state.selected ? newCase([state.selected]) : useExampleThread(),
    'thread-report':()=>previewReport(state.selected ? threadPath(state.selected,'sitrep') : null,'thread-sitrep.txt'),
    'case-report':()=>previewReport(state.campaign ? 'cases/'+enc(state.campaign.id)+'/sitrep' : null,'case-sitrep.txt'),
    'export-case':()=>previewReport(state.campaign ? 'cases/'+enc(state.campaign.id)+'/sitrep' : null,'case-sitrep.txt'),
    'link-thread':linkThread,
    'close-case':async () => {
      if (!state.campaign) throw new Error('Select an exercise or live case to save changes.');
      await api('cases/'+enc(state.campaign.id),{method:'PATCH',body:{status:'closed'}});
      await load();
    },
    'tab-overview':()=>renderDetail('overview'),
    'tab-comments':()=>renderDetail('comments'),
    'tab-network':()=>renderDetail('network'),
    'tab-infra':()=>renderDetail('infra'),
    'platform-all':()=>setFilter({platform:''}),
    'platform-telegram':()=>setFilter({platform:'telegram'}),
    'platform-youtube':()=>setFilter({platform:'youtube'}),
    critical:()=>setFilter({level:'1',flagged:false}),
    flagged:()=>setFilter({level:'',flagged:true}),
    more:()=>{ if (state.mode === 'preview') return useExampleThread(); state.limit += 25; renderMonitor(); },
    'clear-stream':()=>{ const logs = region('monitor-telemetry')?.lastElementChild; if (logs) logs.textContent = 'Display cleared.'; },
    'comment-csv':()=> {
      if (!state.analysis) return useExampleThread();
      csv([['Account','Score','Comment'],...state.analysis.items.map(item=>[item.c.user,item.score,item.c.text])],'comments.csv');
    },
    csv:()=> {
      if (state.mode === 'preview') {
        csv([['Severity','Thread','Comments','Flags'],...previewRows().filter(row=>!row.hidden).map(row=>
          [1,2,3,4].map(index=>row.children[index].innerText.trim()))],'preview-queue.csv');
      } else csv([['ID','Title','Source','Comments','Flagged share'],...filteredThreads().map(row=>
        [row.id,row.title,row.platform,row.n,row.pct])],'thread-queue.csv');
    },
    'review-threads':()=>{ location.href = supplemental('cases'); },
    'dismiss-thread':()=>{ location.href = supplemental('cases'); },
    'submit-review':()=>{ if (!state.campaign) throw new Error('Switch to Exercise or Live to record a review.'); },
    unsupported:()=>status('This controls a sample workflow in the Stitch preview.'),
    'suggestion':()=>setMode('exercise'),
    'snapshot-preview':()=>modal('Preserved example snapshot',box=>add(box,'p',cls.subtle,'This is example evidence from the Stitch design. Switch to Exercise to create a case and inspect an actual snapshot.')),
    'dismiss-preview':()=>setMode('exercise'),
    'unlink-preview':()=>setMode('exercise'),
  };

  function bind() {
    document.addEventListener('click',event => {
      const control = event.target.closest('[data-stitch-action]');
      if (control) {
        const invoke = actions[control.dataset.stitchAction];
        if (invoke) { event.preventDefault(); work(control,invoke); }
        return;
      }
      const row = event.target.closest('[data-stitch-preview-row]');
      if (row) {
        event.preventDefault();
        selectPreview(row);
        if (event.target.closest('a')) openThread();
      }
      if (event.target.closest('[data-stitch-preview-link]')) {
        event.preventDefault();
        status('Source links are available for connected data.');
      }
    });
    const localFilter = $('#filter-input');
    for (const input of [localFilter,topFilter].filter(Boolean)) input.addEventListener('input',() => {
      if (page !== 'monitor') return;
      state.filter = input.value;
      if (input !== localFilter) localFilter.value = input.value;
      if (input !== topFilter) topFilter.value = input.value;
      applyFilters();
    });
    if (topFilter && page !== 'monitor') topFilter.addEventListener('keydown',event=>{
      if (event.key === 'Enter') jump('index.html',{q:topFilter.value});
    });
    region('severity-filter')?.addEventListener('change',event => {
      state.level = event.target.selectedOptions[0].textContent.match(/level\s*(\d)/i)?.[1] || '';
      applyFilters();
    });
    document.addEventListener('keydown',event=>{
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'k') {
        event.preventDefault(); topFilter?.focus();
      }
    });
  }
  function updateMonitorTelemetry() {
    const ticker = region('monitor-ticker');
    ticker.textContent = demo() ? 'EXERCISE · GENERATED LOCALLY' : 'FASTAPI · THREAD TRIAGE';
    const logs = region('monitor-telemetry')?.lastElementChild;
    if (logs) logs.textContent = 'Ingestion telemetry is available from the Sources workspace.';
    const description = region('monitor-controls')?.querySelector('p');
    if (description) description.textContent = 'Review coordinated activity across monitored threads.';
    all('[data-stitch-action^=platform-]').forEach(control=>{
      const kind = control.dataset.stitchAction.slice('platform-'.length);
      const matching = state.threads.filter(row=>Boolean(row.simulated)===(state.mode==='exercise') && (kind==='all' || row.platform===kind));
      const label = {all:'All',telegram:'Telegram',youtube:'YouTube'}[kind];
      control.textContent = label + ' (' + matching.length + ')';
    });
  }
  async function load() {
    const version = ++requestVersion;
    if (state.mode === 'preview') return;
    try {
      if (page === 'monitor') {
        const [threads,cases] = await Promise.all([api('threads'),api('cases'+modeQuery())]);
        if (version !== requestVersion) return;
        state.threads = threads;
        state.cases = cases;
        updateMonitorTelemetry();
        renderMonitor();
      } else if (page === 'investigation') {
        const threads = await api('threads');
        if (version !== requestVersion) return;
        state.threads = threads;
        state.selected ||= filteredThreads()[0]?.id;
        if (!state.selected) throw new Error('There are no threads in this mode.');
        const analysis = await api(threadPath(state.selected,'analysis'));
        if (version !== requestVersion) return;
        if (Boolean(analysis.meta.thread.simulated) !== (state.mode === 'exercise')) throw new Error('This thread belongs to another mode.');
        state.analysis = analysis;
        renderInvestigation();
      } else {
        const id = params.get('case');
        const [threads,cases,suggestions,campaign] = await Promise.all([
          api('threads'),api('cases'+modeQuery()),api('cases/suggestions'+modeQuery()),id ? api('cases/'+enc(id)) : null,
        ]);
        if (version !== requestVersion) return;
        const simulated = state.mode === 'exercise';
        if (campaign && Boolean(campaign.simulated) !== simulated) throw new Error('This case belongs to another mode.');
        state.threads = threads.filter(thread=>Boolean(thread.simulated) === simulated);
        state.cases = cases;
        state.suggestions = suggestions;
        state.campaign = campaign;
        renderCampaign();
      }
    } catch (error) {
      if (version !== requestVersion) return;
      status(error.message,true);
      const warning = section('Workspace connection');
      add(warning,'p',cls.subtle,error.message);
      warning.append(button('Connect to FastAPI',connect),
        link('Open Stitch preview',destination('index.html',{mode:'preview'})));
      document.querySelector('main').replaceChildren(warning);
    }
  }
  async function subscribe() {
    if (state.mode === 'preview' || demo() || !data.hasKey()) return;
    try {
      const {ticket} = await data.api('ws-ticket',{method:'POST'});
      const socket = new WebSocket(data.origin.replace(/^http/,'ws')+'/ws/live?ticket='+enc(ticket));
      socket.addEventListener('message',event=>{
        let message;
        try { message = JSON.parse(event.data); } catch { return; }
        if (message.type !== 'analysis' || dialog.open || page === 'campaign') return;
        if (page === 'investigation' && state.selected !== message.thread_id) return;
        clearTimeout(refreshTimer);
        refreshTimer = setTimeout(load,500);
      });
    } catch {}
  }
  header();
  bind();
  if (params.has('q') && page === 'monitor') {
    state.filter = params.get('q');
    $('#filter-input').value = state.filter;
    topFilter.value = state.filter;
  }
  if (state.mode === 'preview') {
    if (page === 'monitor') {
      filterPreview();
      for (const element of all('main span')) {
        if (element.children.length) continue;
        if (element.textContent.trim() === 'LIVE TELEMETRY') element.textContent = 'PREVIEW TELEMETRY';
        if (element.textContent.trim() === 'LIVE RAW LOGS') element.textContent = 'EXAMPLE RAW LOGS';
      }
    }
  } else {
    load();
    subscribe();
  }
})();
