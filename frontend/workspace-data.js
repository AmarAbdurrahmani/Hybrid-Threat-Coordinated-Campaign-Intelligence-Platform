/* FastAPI access and the browser-only exercise dataset. */
(() => {
  const renderApi = 'https://hybrid-threat-coordinated-campaign.onrender.com';
  const localHost = /^(localhost|127\.0\.0\.1|\[::1\])$/;
  const configured = new URLSearchParams(location.search).get('api');

  function apiOrigin() {
    if (configured === 'same') return location.origin;
    if (configured) {
      try {
        const url = new URL(configured);
        if (url.protocol === 'https:' || (url.protocol === 'http:' && localHost.test(url.hostname))) return url.origin;
      } catch {}
    }
    if (localHost.test(location.hostname) || location.hostname.endsWith('.onrender.com')) return location.origin;
    return renderApi;
  }

  const origin = apiOrigin();
  const keySlot = 'htccip_key:' + origin;
  let key = '';
  try { key = sessionStorage.getItem(keySlot) || ''; } catch {}
  async function api(path, options = {}) {
    const { method = 'GET', body, signal, authenticated = true } = options;
    const response = await fetch(origin + '/api/' + path, {
      method, signal, cache: 'no-store',
      headers: {
        ...(body === undefined ? {} : { 'Content-Type': 'application/json' }),
        ...(key && authenticated ? { 'X-API-Key': key } : {}),
      },
      body: body === undefined ? undefined : JSON.stringify(body),
    });
    if (!response.ok) {
      const payload = await response.json().catch(() => ({}));
      const detail = payload.detail;
      const message = Array.isArray(detail) ? detail.map(issue => issue.msg).join('. ') : detail;
      const error = new Error(message || 'Request failed (' + response.status + ')');
      error.status = response.status;
      throw error;
    }
    return response.headers.get('content-type')?.includes('text/plain') ? response.text() : response.json();
  }

  function setKey(value) {
    key = value.trim();
    try {
      if (key) sessionStorage.setItem(keySlot, key);
      else sessionStorage.removeItem(keySlot);
    } catch {}
  }

  const signalNames = ['temporal', 'linguistic', 'narrative', 'network'];
  const threads = new Map();
  const cases = new Map();
  const alerts = [];
  let caseSequence = 0;
  let runSequence = 0;
  const demoSlot = 'htccip_exercise_v1';

  function saveExercise() {
    try {
      sessionStorage.setItem(demoSlot, JSON.stringify({
        threads: [...threads], cases: [...cases], alerts, caseSequence, runSequence,
      }));
    } catch {}
  }

  function restoreExercise() {
    try {
      const saved = JSON.parse(sessionStorage.getItem(demoSlot) || 'null');
      if (!saved || !saved.threads?.length) return false;
      for (const [id, entry] of saved.threads) threads.set(id, entry);
      for (const [id, entry] of saved.cases) cases.set(id, entry);
      alerts.push(...saved.alerts);
      caseSequence = saved.caseSequence;
      runSequence = saved.runSequence;
      return true;
    } catch { return false; }
  }

  const exercises = {
    kfor: {
      name: 'KFOR statement and coordinated reposts',
      narratives: ['Regional security claims', 'Reposted statement'],
      phrases: [
        'A short clip is circulating without the rest of the press briefing.',
        'Several accounts are quoting the same sentence from an unverified transcript.',
        'Can anyone link to the full press briefing?',
        'The local report links to the original statement.',
      ],
    },
    energy: {
      name: 'Regional power outage rumor',
      narratives: ['Unverified outage claim', 'Reposted warning'],
      phrases: [
        'The same unverified outage notice is being reposted across both discussions.',
        'People are forwarding a screenshot without a direct source.',
        'The regional operator has published a service update.',
        'Our neighborhood is reporting normal service.',
      ],
    },
  };

  function simulate(options = {}) {
    const scenario = exercises[options.scenario] || exercises.energy;
    const now = Date.now();
    const run = ++runSequence;
    const ids = [];
    const titles = [scenario.name, 'Mirror discussion: ' + scenario.name];
    const total = Math.max(3, Math.min(60, Number(options.accounts) || 12));
    for (let mirror = 0; mirror < 2; mirror++) {
      const id = 'demo:' + run + ':' + (mirror + 1);
      const last = now - (mirror + 1) * 360_000;
      const items = Array.from({ length: total + 6 }, (_, index) => {
        const flagged = index < total;
        const text = scenario.phrases[flagged ? index % 2 : 2 + index % 2];
        const score = flagged ? 76 + index % 21 : 8 + index % 14;
        const ts = last - (flagged ? index * 4200 : (index - total + 1) * 420_000);
        return {
          c: {id: id + ':' + index, text, ts, user: 'exercise_' + String(index + 1).padStart(3, '0'), simulated: true},
          t: ts, score, tier: flagged ? 'high' : 'organic',
          temporal: flagged ? 93 : 7, linguistic: flagged ? 81 : 0, narrative: flagged ? 74 : 9, network: flagged ? 86 : null,
          tReasons: flagged ? ['Several posts arrived within the same five-minute interval.'] : ['Activity follows the ordinary thread cadence.'],
          lReasons: flagged ? ['A repeated passage appears in several distinct accounts.'] : [],
          nReasons: flagged ? ['The comment repeats an unverified claim from this discussion.'] : [],
          gReasons: flagged ? ['This account pair appears in both exercise threads.'] : [],
          spans: flagged ? [{s: 0, e: Math.min(text.length, 24), type: 'ngram'}] : [],
          dups: flagged ? [{id: id + ':' + ((index + 2) % total), user: 'exercise_' + String((index + 2) % total + 1).padStart(3, '0'), sim: .91}] : [],
        };
      });
      const members = items.filter(item => item.tier === 'high').map(item => item.c.user);
      const n = items.length;
      const summary = {
        level: 1 + mirror, n, pct: total / n, largestCluster: total, high: total, medium: 0, organic: n - total,
        peak: {count: total, start: last - 90_000, end: last, coordinated: total},
        narratives: scenario.narratives.map((label, index) => [label, total - index * 3, 'exercise-' + index, 'Operational']),
        signalAvg: Object.fromEntries(signalNames.map(signal => [signal, {
          flagged: items[0][signal], organic: items.at(-1)[signal],
        }])),
      };
      const thread = {id, title: titles[mirror], platform: 'simulated', simulated: 1, n, last_ts: last, level: summary.level, pct: summary.pct};
      threads.set(id, {thread, analysis: {
        contract: '1', items, thread: summary, infra: [], users: Object.fromEntries(items.map(item => [item.c.user, null])),
        scoreByUser: Object.fromEntries(items.map(item => [item.c.user, item.score])),
        clusters: [{id: 'Exercise cluster', members, size: members.length, avgScore: 85, edgeTypes: ['coactivity']}],
        edges: members.slice(1).map(user => ({a: members[0], b: user, type: 'coactivity', w: 2})),
        meta: {thread, generated: now, embedder: 'exercise', scoring_version: 'Demo fixture'},
      }});
      ids.push(id);
    }
    alerts.unshift({id: run, thread_id: ids[0], level: 1, sent_at: now / 1000, status: 'dry_run', attempts: 0, exercise: 1, acknowledged_at: null});
    saveExercise();
    return {thread_ids: ids, primary: ids[0], new: (total + 6) * ids.length};
  }

  function snapshot(ids) {
    return Object.fromEntries(ids.map(id => {
      const analysis = threads.get(id)?.analysis;
      if (!analysis) throw new Error('Unknown exercise thread');
      return [id, {
        thread: analysis.meta.thread, score: analysis.thread, captured_at: analysis.meta.generated, scoring_version: analysis.meta.scoring_version,
        comments: analysis.items.slice(0, 12).map(item => ({
          ...item.c, score: item.score, signals: {tReasons: item.tReasons, lReasons: item.lReasons, nReasons: item.nReasons, gReasons: item.gReasons},
        })),
        coactivity_edges: analysis.edges,
      }];
    }));
  }

  function report(title, ids, review = []) {
    return ['EXERCISE — LOCAL DEMO', title, '', ...ids.flatMap(id => {
      const data = threads.get(id);
      return [data.thread.title, 'Level ' + data.thread.level + ' • ' + data.thread.n + ' comments', ''];
    }), ...review.map(record => record.reviewer + ' • ' + record.decision + ' — ' + record.reason)].join('\n');
  }

  function exercise(path, {method = 'GET', body} = {}) {
    const [route] = path.split('?');
    const [group, id, action] = route.split('/').map(decodeURIComponent);
    if (route === 'simulate-attack') return simulate(body);
    if (group === 'threads') {
      if (!id) return Array.from(threads.values(), entry => entry.thread).sort((a, b) => b.last_ts - a.last_ts);
      if (!threads.has(id)) throw new Error('Unknown exercise thread');
      if (action === 'sitrep') return report('Thread SitRep', [id]);
      return threads.get(id).analysis;
    }
    if (group === 'cases') {
      if (!id && method === 'POST') {
        const caseId = ++caseSequence;
        const campaign = {...body, id: caseId, status: body.status || 'open', created_at: Date.now() / 1000,
                          evidence: snapshot(body.thread_ids), reviews: []};
        cases.set(caseId, campaign);
        saveExercise();
        return structuredClone(campaign);
      }
      if (!id) return Array.from(cases.values()).reverse();
      if (id === 'suggestions') {
        const available = [...threads.keys()];
        const suggestions = [];
        for (let index = 0; index < available.length; index += 2) {
          const threadIds = available.slice(index, index + 2);
          if (![...cases.values()].some(c => threadIds.every(threadId => c.thread_ids.includes(threadId)))) {
            suggestions.push({thread_ids: threadIds, links: [{thread_ids: threadIds, account_pairs: threads.get(threadIds[0]).analysis.edges.length}]});
          }
        }
        return suggestions.reverse();
      }
      const campaign = cases.get(Number(id));
      if (!campaign) throw new Error('Unknown exercise case');
      if (action === 'sitrep') return report(campaign.title, campaign.thread_ids, campaign.reviews);
      if (action === 'reviews') campaign.reviews.push({...body, created_at: Date.now() / 1000});
      if (method === 'PATCH') {
        const {link = [], unlink = [], ...fields} = body;
        Object.assign(campaign, fields);
        campaign.thread_ids = [...new Set([...campaign.thread_ids, ...link])].filter(tid => !unlink.includes(tid));
        campaign.evidence = {...snapshot(link), ...campaign.evidence};
      }
      if (method !== 'GET') saveExercise();
      return structuredClone(campaign);
    }
    if (group === 'alerts') {
      if (action === 'acknowledge') {
        const alert = alerts.find(entry => entry.id === Number(id));
        if (alert) alert.acknowledged_at ??= Date.now() / 1000;
        saveExercise();
        return {acknowledged: Boolean(alert)};
      }
      return alerts;
    }
    if (group === 'sources') return [];
    throw new Error('This operation requires a live connection.');
  }

  if (!restoreExercise()) simulate();
  window.workspaceData = {api, exercise, origin, setKey, hasKey: () => Boolean(key)};
})();
