/* Analyst operations for the live dashboard. */
window.setupOperations = function setupOperations(apiFetch, selectedThread) {
  const dialog = document.getElementById('operations');
  const body = document.getElementById('operationsBody');
  const status = document.getElementById('operationsStatus');
  const exercise = document.getElementById('exerciseCases');
  let tab = 'cases';
  let revision = 0;

  async function request(path, method = 'GET', data) {
    const response = await apiFetch('/api/' + path, {
      method,
      ...(data === undefined ? {} : { body: JSON.stringify(data) }),
    });
    if (!response.ok) {
      const { detail } = await response.json().catch(() => ({}));
      const message = Array.isArray(detail) ? detail.map(item => item.msg).join('; ') : detail;
      throw new Error(message || 'Request failed (' + response.status + ')');
    }
    return response.headers.get('Content-Type').includes('text/plain') ? response.text() : response.json();
  }

  function element(tag, value, parent = body) {
    const node = document.createElement(tag);
    if (value) node.textContent = value;
    parent.append(node);
    return node;
  }

  function button(label, action, parent = body) {
    const node = element('button', label, parent);
    node.type = 'button';
    node.className = 'mr-2 my-1 px-2 py-1 border border-ink-600 rounded';
    node.onclick = async () => {
      node.disabled = true;
      status.textContent = '';
      try {
        await action();
      } catch (error) {
        status.textContent = error.message;
      } finally {
        node.disabled = false;
      }
    };
    return node;
  }

  function field(parent, title, options, value) {
    const label = element('label', title + ' ', parent);
    label.className = 'block my-2';
    const input = element(options ? 'select' : 'input', '', label);
    input.className = 'ml-1 bg-ink-900 border border-ink-600 rounded px-2 py-1';
    if (options) {
      for (const option of options) element('option', option, input).value = option;
    } else {
      input.type = 'text';
      input.maxLength = 200;
    }
    if (value !== undefined && value !== null) input.value = value;
    return input;
  }

  function card() {
    const section = element('section');
    section.className = 'p-3 border border-ink-600 rounded';
    return section;
  }

  function downloadReport(id, report) {
    const url = URL.createObjectURL(new Blob([report], { type: 'text/plain;charset=utf-8' }));
    const link = document.createElement('a');
    link.href = url;
    link.download = 'campaign-case-' + id + '-sitrep.txt';
    link.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  }

  function time(value) {
    return value ? new Date(value * 1000).toLocaleString() : 'Never';
  }

  async function showCase(id) {
    const current = ++revision;
    const campaign = await request('cases/' + id);
    if (current !== revision || !dialog.open) return;
    body.replaceChildren();
    button('Back to cases', refresh);
    element('h3', 'Case ' + id + ' · ' + campaign.title);
    const title = field(body, 'Title', null, campaign.title);
    const owner = field(body, 'Owner', null, campaign.owner);
    const stage = field(body, 'Status', ['open', 'reviewing', 'closed', 'dismissed'], campaign.status);
    button('Save case', async () => {
      await request('cases/' + id, 'PATCH', { title: title.value, owner: owner.value || null, status: stage.value });
      await showCase(id);
    });
    button('Download SitRep', async () => downloadReport(id, await request('cases/' + id + '/sitrep')));
    const active = new Set(campaign.thread_ids);
    const selected = selectedThread();
    if (selected && !active.has(selected.id)) {
      button('Link selected thread', async () => {
        await request('cases/' + id, 'PATCH', { link: [selected.id] });
        await showCase(id);
      });
    }
    for (const [tid, evidence] of Object.entries(campaign.evidence)) {
      const section = card();
      element('h4', (evidence.thread.title || tid) + (active.has(tid) ? '' : ' · Unlinked'), section);
      element('p', 'Level ' + evidence.score.level + ' · Scoring ' + evidence.scoring_version, section);
      if (active.has(tid)) {
        button('Unlink thread', async () => {
          await request('cases/' + id, 'PATCH', { unlink: [tid] });
          await showCase(id);
        }, section);
      }
      const detail = element('details', '', section);
      element('summary', evidence.comments.length + ' captured comments', detail);
      for (const comment of evidence.comments) {
        const entry = element('p', 'Score ' + comment.score + ' · ' + comment.text, detail);
        if (comment.url && /^https?:\/\//i.test(comment.url)) {
          const link = element('a', ' Open source', entry);
          link.href = comment.url;
          link.target = '_blank';
          link.rel = 'noopener noreferrer';
        }
        const reasons = Object.values(comment.signals).flat().filter(Boolean);
        if (reasons.length) element('small', ' ' + reasons.join(' · '), entry);
      }
    }
    const reviews = card();
    element('h4', 'Analyst reviews', reviews);
    for (const review of campaign.reviews) {
      element('p', review.reviewer + ' · ' + review.decision.replaceAll('_', ' ') + ' · ' + review.reason, reviews);
    }
    const reviewer = field(reviews, 'Reviewer');
    const decision = field(reviews, 'Decision', ['likely_coordinated', 'organic', 'inconclusive']);
    const reason = field(reviews, 'Reason');
    reason.maxLength = 2000;
    button('Record review', async () => {
      await request('cases/' + id + '/reviews', 'POST', { reviewer: reviewer.value, decision: decision.value, reason: reason.value });
      await showCase(id);
    }, reviews);
  }

  function renderCases(cases, suggestions, feedback, simulated) {
    if (feedback.reviewed_cases) {
      const decisions = feedback.decisions;
      element('p', 'Reviewed cases: ' + feedback.reviewed_cases + ' · Likely coordinated: ' +
        (decisions.likely_coordinated || 0) + ' · Organic: ' + (decisions.organic || 0) +
        ' · Inconclusive: ' + (decisions.inconclusive || 0));
    }
    const selected = selectedThread();
    if (selected && Boolean(selected.simulated) === simulated) {
      const title = field(body, 'New case title', null, selected.title || selected.id);
      button('Create from selected thread', async () => {
        const campaign = await request('cases', 'POST', { title: title.value, simulated, thread_ids: [selected.id] });
        await showCase(campaign.id);
      });
    }
    for (const suggestion of suggestions) {
      const section = card();
      element('strong', 'Suggested case · ' + suggestion.thread_ids.length + ' related threads', section);
      for (const link of suggestion.links) {
        element('p', link.thread_ids.join(' ↔ ') + ' · ' + link.account_pairs + ' recurring account pairs', section);
      }
      const title = field(section, 'Title', null, 'Coordination across ' + suggestion.thread_ids.length + ' threads');
      async function save(statusValue) {
        const result = await request('cases', 'POST', {
          title: title.value, simulated, status: statusValue, thread_ids: suggestion.thread_ids,
        });
        if (statusValue === 'dismissed') await refresh();
        else await showCase(result.id);
      }
      button('Open case', () => save('open'), section);
      button('Dismiss suggestion', () => save('dismissed'), section);
    }
    for (const campaign of cases) {
      const section = card();
      element('p', campaign.title + ' · ' + campaign.status + ' · ' + campaign.thread_ids.length + ' threads', section);
      button('Open case ' + campaign.id, () => showCase(campaign.id), section);
    }
    if (!cases.length && !suggestions.length) element('p', 'No cases or suggestions yet.');
  }

  function renderSources(sources) {
    const kind = field(body, 'Source type', ['youtube', 'telegram']);
    const ref = field(body, 'Video ID or channel username');
    button('Add source', async () => {
      await request('sources', 'POST', { kind: kind.value, ref: ref.value });
      await refresh();
    });
    for (const source of sources) {
      const section = card();
      element('strong', source.id + (source.active ? '' : ' · Paused'), section);
      element('p', 'Last successful poll: ' + time(source.last_success) + ' · Last attempt: ' + time(source.last_attempt), section);
      if (source.last_backfill) element('p', 'Last backfill: ' + time(source.last_backfill), section);
      if (source.last_error) element('p', 'Last error: ' + source.last_error, section);
      const actions = [source.active ? 'pause' : 'resume', 'retry', 'backfill'];
      for (const action of actions) {
        button(action[0].toUpperCase() + action.slice(1), async () => {
          const result = await request('sources/' + encodeURIComponent(source.id) + '/' + action, 'POST');
          await refresh();
          if (result.errors) status.textContent = 'Poll failed. See the source error.';
        }, section);
      }
    }
  }

  function renderAlerts(alerts) {
    for (const alert of alerts) {
      const section = card();
      element('strong', (alert.exercise ? 'EXERCISE · ' : '') + 'Level ' + alert.level + ' · ' + alert.status, section);
      element('p', alert.thread_id + ' · ' + time(alert.sent_at) + ' · ' + alert.attempts + ' delivery attempts', section);
      if (alert.acknowledged_at) element('p', 'Acknowledged ' + time(alert.acknowledged_at), section);
      else button('Acknowledge', async () => {
        await request('alerts/' + alert.id + '/acknowledge', 'POST');
        await refresh();
      }, section);
    }
    if (!alerts.length) element('p', 'No alerts yet.');
  }

  async function refresh() {
    const current = ++revision;
    const activeTab = tab;
    const simulated = exercise.checked;
    body.replaceChildren();
    status.textContent = 'Loading…';
    try {
      const data = activeTab === 'cases'
        ? await Promise.all([request('cases?simulated=' + simulated), request('cases/suggestions?simulated=' + simulated),
          request('cases/feedback?simulated=' + simulated)])
        : await request(activeTab);
      if (current !== revision || !dialog.open) return;
      status.textContent = '';
      if (activeTab === 'cases') renderCases(...data, simulated);
      else if (activeTab === 'sources') renderSources(data);
      else renderAlerts(data);
    } catch (error) {
      if (current === revision) status.textContent = error.message;
    }
  }

  document.getElementById('liveOperations').onclick = () => {
    exercise.checked = Boolean(selectedThread()?.simulated);
    dialog.showModal();
    refresh();
  };
  dialog.querySelectorAll('[data-ops]').forEach(node => {
    node.onclick = () => { tab = node.dataset.ops; refresh(); };
  });
  exercise.onchange = refresh;
};
