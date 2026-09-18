import { filterRecords, buildDraft, validSavedDraft, draftMarkdown } from './core.mjs';
import { presentation, recordName, recordSubtitle, recordMetadata } from './report_ui.mjs';

const $ = (id) => document.getElementById(id);
const initial = JSON.parse($('initialData').textContent);
const offline = document.body.dataset.mode === 'offline';
const storageKey = 'netalertx-review-desk:drafts:v1';
const pageSize = 30;
let report = initial.report;
let source = initial.source;
let selected = report.records[0]?.evidence_id;
let page = 0;
let draftBuffer = null;
let aiBusy = false;
let savedDrafts = [];
const workspaces = new Map((initial.workspaces || [initial]).map((item) => [item.report.report_type || 'inventory', item]));

function notice(text, error = false) {
  $('notice').textContent = text;
  $('notice').classList.toggle('error', error);
  $('notice').hidden = false;
}

function el(tag, text = '', className = '') {
  const node = document.createElement(tag);
  node.textContent = text;
  if (className) node.className = className;
  return node;
}

function dateLabel(value) {
  return new Intl.DateTimeFormat(undefined, { month: 'short', day: 'numeric', timeZone: 'UTC' }).format(new Date(value));
}

function activeDrafts() {
  return savedDrafts.filter((draft) => draft.report_id === report.report_id && report.records.some((record) => record.evidence_id === draft.evidence_id));
}

function showView(name) {
  $('reviewView').hidden = name !== 'review';
  $('draftsView').hidden = name !== 'drafts';
  const active = name === 'drafts' ? 'drafts' : report.report_type || 'inventory';
  for (const [id, value] of [['reviewTab', 'inventory'], ['securityTab', 'security'], ['draftsTab', 'drafts']]) {
    $(id).classList.toggle('active', active === value);
    if (active === value) $(id).setAttribute('aria-current', 'page');
    else $(id).removeAttribute('aria-current');
  }
  if (name === 'drafts') renderDrafts();
}

function selectEvidence(id, focus = true) {
  selected = id;
  $('filter').value = 'all';
  $('search').value = '';
  page = Math.floor(report.records.findIndex((record) => record.evidence_id === id) / pageSize);
  showView('review');
  renderQueue();
  if (focus) $('detailPanel').focus({ preventScroll: true });
  if (matchMedia('(max-width: 800px)').matches) $('detailPanel').scrollIntoView({ block: 'start' });
}

function citation(id) {
  const button = el('button', id, 'evidence-link');
  button.setAttribute('aria-label', `Open evidence ${id}`);
  button.addEventListener('click', () => selectEvidence(id));
  return button;
}

function activateWorkspace(type) {
  if (aiBusy) { notice('Wait for the current AI summary before switching reports.'); return; }
  const workspace = workspaces.get(type);
  if (!workspace) { notice(`Import a ${type === 'security' ? 'Prowler Security Delta' : 'NetAlertX inventory-review'} JSON report to open this view.`); if (!offline) $('reportFile').click(); return; }
  $('notice').hidden = true;
  report = workspace.report; source = workspace.source; selected = report.records[0]?.evidence_id; page = 0;
  $('search').value = '';
  $('demoLabel').hidden = !workspace.demo;
  renderOverview(); renderQueue(); showView('review');
}

function renderCollection() {
  const panel = $('collectionEvidence');
  panel.replaceChildren();
  const evidence = report.collection_evidence;
  panel.hidden = !evidence;
  if (!evidence) return;
  panel.append(el('h3', 'Collection evidence'), el('p', 'Collector-recorded API coverage. Reads were consistent across each collection window; an atomic snapshot is not guaranteed. Integrity hashes do not independently attest to the source or scan freshness.'));
  for (const [side, label] of [['before', 'Baseline'], ['after', 'Current']]) {
    const item = evidence[side];
    const line = el('div', '', 'collection-row');
    line.append(el('strong', label), el('span', item.source_endpoint),
      el('span', `${item.row_count} rows · ${item.started_at} → ${item.completed_at}`),
      el('span', 'Consistent API reads; scan freshness unverified.'));
    panel.append(line);
  }
}

function renderOverview() {
  const view = presentation(report);
  $('viewTitle').textContent = view.title;
  $('queueHeading').textContent = view.heading;
  $('scopeLine').textContent = report.scope;
  $('overviewSentence').textContent = view.sentence;
  $('searchLabel').textContent = `Search ${view.plural}`;
  $('filterLabel').textContent = `Filter ${view.plural}`;
  $('search').placeholder = view.search;
  $('queueEmpty').textContent = `No ${view.plural} match. Try another search or filter.`;
  $('filter').replaceChildren(...view.filters.map(([value, label]) => {
    const option = el('option', label); option.value = value; return option;
  }));
  $('beforeDate').textContent = dateLabel(report.before_at);
  $('afterDate').textContent = dateLabel(report.after_at);
  $('beforeDate').title = report.before_at;
  $('afterDate').title = report.after_at;
  $('beforeCount').textContent = `${view.before} ${view.plural} observed`;
  $('afterCount').textContent = `${view.after} ${view.plural} observed`;
  $('reportLabel').textContent = `${view.type === 'security' ? 'Prowler' : 'NetAlertX'} report ${report.report_id} · UTC`;
  $('coverageNote').textContent = view.coverage;
  renderCollection();
  const metrics = view.metrics;
  $('metrics').replaceChildren(...metrics.map(([filter, count, label]) => {
    const button = el('button', '', 'metric');
    button.dataset.filter = filter;
    button.append(el('strong', count), el('span', label));
    button.addEventListener('click', () => { $('filter').value = filter; page = 0; renderQueue(); });
    return button;
  }));
  const records = report.records.filter((record) => record.attention).slice(0, 3);
  $('evidenceSummary').replaceChildren(...records.map((record) => {
    const item = el('div', '', 'brief-item');
    item.append(el('p', record.observation), citation(record.evidence_id));
    return item;
  }));
  if (!records.length) $('evidenceSummary').append(el('p', 'No attention items were identified in the supplied comparison.'));
  $('aiSummary').hidden = true;
  $('aiSummary').replaceChildren();
  renderDrafts();
}

function renderQueue() {
  const rows = filterRecords(report, $('filter').value, $('search').value);
  page = Math.min(Math.max(0, page), Math.max(0, Math.ceil(rows.length / pageSize) - 1));
  if (!rows.some((record) => record.evidence_id === selected)) selected = rows[0]?.evidence_id;
  $('queueCount').textContent = `${rows.length} of ${report.records.length}`;
  $('queueEmpty').hidden = rows.length > 0;
  $('detailPanel').hidden = rows.length === 0;
  document.querySelectorAll('.metric').forEach((button) => {
    const active = button.dataset.filter === $('filter').value;
    button.classList.toggle('selected', active);
    button.setAttribute('aria-pressed', String(active));
  });
  $('deviceList').replaceChildren(...rows.slice(page * pageSize, (page + 1) * pageSize).map((record) => {
    const row = el('li');
    const button = el('button', '', `device-row ${record.change}${selected === record.evidence_id ? ' selected' : ''}`);
    const name = recordName(record);
    button.setAttribute('aria-label', `Review ${name}`);
    button.setAttribute('aria-pressed', String(selected === record.evidence_id));
    button.dataset.evidenceId = record.evidence_id;
    const icon = el('span', { new_device: '+', changed: '↔', not_observed: '?', unchanged: '✓', incomplete_comparison: '…', regression: '↗', new_failure: '!', newly_confirmed_failure: '!', persistent_failure: '!', verified_fix: '✓', observed_pass: '✓', inconclusive: '?' }[record.change], 'device-icon');
    icon.setAttribute('aria-hidden', 'true');
    const content = el('span', '', 'device-row-content');
    content.append(el('strong', name), el('small', recordSubtitle(record)));
    const footer = el('span', '', 'row-footer');
    footer.append(el('span', record.label, `status ${record.change}`));
    if (record.severity) footer.append(el('span', record.severity, 'severity'));
    if (['unassigned', 'not_exported'].includes(record.owner_status)) footer.append(el('span', 'Owner needs review', 'owner-warning'));
    content.append(footer);
    button.append(icon, content);
    button.addEventListener('click', () => {
      selected = record.evidence_id; renderQueue();
      [...$('deviceList').querySelectorAll('button')].find((item) => item.dataset.evidenceId === selected)?.focus({ preventScroll: true });
    });
    row.append(button);
    return row;
  }));
  $('prevPage').disabled = page === 0;
  $('nextPage').disabled = (page + 1) * pageSize >= rows.length;
  $('pageLabel').textContent = rows.length ? `Page ${page + 1} of ${Math.ceil(rows.length / pageSize)}` : 'No matches';
  if (selected) renderDetail(report.records.find((record) => record.evidence_id === selected));
}

function renderDetail(record) {
  $('detailStatus').className = `status ${record.change}`;
  $('detailStatus').textContent = record.label;
  $('evidenceId').textContent = record.evidence_id;
  $('deviceName').textContent = recordName(record);
  $('deviceMeta').textContent = recordMetadata(record);
  $('deviceObservation').textContent = record.observation;
  $('recommendation').textContent = record.recommendation;
  $('evidenceRows').replaceChildren(...record.cells.map((cell) => {
    const tr = el('tr', '', cell.changed ? 'changed' : '');
    tr.append(el('td', cell.label));
    for (const side of ['before', 'current']) {
      const status = cell[`${side}_state`];
      const text = status === 'observed' ? cell[side] || '(blank)' :
        { not_observed: 'Not observed', not_exported: 'Not exported', not_comparable: 'Not comparable' }[status];
      tr.append(el('td', text, status === 'observed' ? '' : 'missing'));
    }
    return tr;
  }));
  const existing = activeDrafts().some((draft) => draft.evidence_id === record.evidence_id);
  $('draftButton').textContent = existing ? 'Edit ticket draft' : 'Create ticket draft';
}

function download(name, text, type = 'text/markdown;charset=utf-8') {
  const url = URL.createObjectURL(new Blob([text], { type }));
  const link = el('a');
  link.href = url;
  link.download = name;
  document.body.append(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

function openDraft(id) {
  const record = report.records.find((item) => item.evidence_id === id);
  const existing = activeDrafts().find((draft) => draft.evidence_id === id);
  draftBuffer = { ...(existing || buildDraft(report, record)) };
  $('draftTitle').value = draftBuffer.title;
  $('draftBody').value = draftBuffer.body;
  $('draftError').hidden = true;
  $('draftDialog').showModal();
  $('draftTitle').focus();
}

function editedDraft() {
  return { ...draftBuffer, title: $('draftTitle').value.trim(), body: $('draftBody').value, status: 'draft' };
}

function draftError() {
  $('draftError').textContent = 'Enter a title with text and keep the body within 20,000 characters.';
  $('draftError').hidden = false;
}

function renderDrafts() {
  const drafts = activeDrafts();
  $('draftCount').textContent = drafts.length;
  $('draftsEmpty').hidden = drafts.length > 0;
  $('downloadAll').disabled = drafts.length === 0;
  $('draftList').replaceChildren(...drafts.map((draft) => {
    const card = el('article', '', 'draft-card');
    card.append(el('span', 'Draft only', 'status'), el('h2', draft.title),
      el('p', `Evidence ${draft.evidence_id}. Approval not recorded. Execution not started.`));
    const edit = el('button', 'Edit draft', 'button primary');
    edit.addEventListener('click', () => openDraft(draft.evidence_id));
    const save = el('button', 'Download', 'button subtle');
    save.addEventListener('click', () => download(`review-${draft.evidence_id}.md`, draftMarkdown(draft)));
    card.append(edit, save, citation(draft.evidence_id));
    return card;
  }));
}

async function request(path, payload, timeout = 10000) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeout);
  try {
    const response = await fetch(path, { method: payload ? 'POST' : 'GET', signal: controller.signal,
      ...(payload ? { headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) } : {}) });
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || 'The request failed.');
    return result;
  } catch (error) {
    if (error.name === 'AbortError') throw new Error('The request timed out. Please retry.');
    throw error;
  } finally { clearTimeout(timer); }
}

async function refreshModels() {
  if (aiBusy) return;
  $('modelLabel').hidden = true;
  $('generateAI').disabled = true;
  if (offline) {
    $('modelStatus').textContent = 'This is a portable preview. Run the local review desk to connect Ollama.';
    $('refreshModels').disabled = true;
    return;
  }
  $('modelStatus').textContent = 'Checking for locally stored models…';
  try {
    const result = await request('/api/models');
    $('modelSelect').replaceChildren(...result.models.map((model) => {
      const option = el('option', model); option.value = model; return option;
    }));
    $('modelLabel').hidden = result.models.length === 0;
    $('generateAI').disabled = result.models.length === 0 || report.needs_review === 0;
    $('modelStatus').textContent = result.models.length ? `${result.models.length} local model${result.models.length === 1 ? '' : 's'} available.` :
      result.message || 'No locally stored models found. Add a model in Ollama, then refresh.';
  } catch (error) { $('modelStatus').textContent = error.message; }
}

function renderAI(result) {
  const panel = $('aiSummary');
  panel.replaceChildren(el('h3', 'Local AI draft'), el('p', `${result.model}. Based on ${result.included_records} of ${result.total_records} records. Check each claim against its citations; valid citations do not guarantee factual accuracy.`));
  for (const [key, title] of [['observations', 'Observations'], ['next_steps', 'Suggested checks']]) {
    if (!result.summary[key].length) continue;
    panel.append(el('h3', title));
    const list = el('ul');
    for (const item of result.summary[key]) {
      const line = el('li', item.text + ' ');
      line.append(...item.evidence_ids.map(citation));
      list.append(line);
    }
    panel.append(list);
  }
  const save = el('button', 'Download AI draft', 'button subtle');
  save.addEventListener('click', () => download(`ai-summary-${report.report_id}.json`, JSON.stringify(result.summary, null, 2), 'application/json'));
  panel.append(save);
  panel.hidden = false;
}

$('reviewTab').addEventListener('click', () => activateWorkspace('inventory'));
$('securityTab').addEventListener('click', () => activateWorkspace('security'));
$('draftsTab').addEventListener('click', () => showView('drafts'));
$('backToReview').addEventListener('click', () => showView('review'));
$('search').addEventListener('input', () => { page = 0; renderQueue(); });
$('filter').addEventListener('change', () => { page = 0; renderQueue(); });
$('prevPage').addEventListener('click', () => { page--; renderQueue(); });
$('nextPage').addEventListener('click', () => { page++; renderQueue(); });
$('draftButton').addEventListener('click', () => openDraft(selected));
$('cancelDraft').addEventListener('click', () => $('draftDialog').close());
$('draftForm').addEventListener('submit', (event) => {
  event.preventDefault();
  const draft = editedDraft();
  if (!validSavedDraft(draft)) { draftError(); return; }
  savedDrafts = [...savedDrafts.filter((item) => item.id !== draft.id), draft];
  let persisted = true;
  try {
    const serialized = JSON.stringify(savedDrafts);
    if (serialized.length > 2 * 1024 * 1024 || savedDrafts.length > 500) throw new Error('Storage limit');
    localStorage.setItem(storageKey, serialized);
  } catch { persisted = false; }
  $('draftDialog').close();
  renderDrafts(); renderQueue();
  notice(persisted ? 'Draft saved in this browser. Nothing has been submitted.' : 'Draft saved for this session. Browser storage is unavailable; download it to keep a copy.');
});
$('downloadEdited').addEventListener('click', () => {
  const draft = editedDraft();
  if (!validSavedDraft(draft)) { draftError(); return; }
  download(`review-${draft.evidence_id}.md`, draftMarkdown(draft));
});
$('downloadAll').addEventListener('click', () => download(`review-drafts-${report.report_id}.md`, activeDrafts().map(draftMarkdown).join('\n---\n\n')));
$('downloadReport').addEventListener('click', () => download(`${report.report_type || 'inventory'}-report-${report.report_id}.json`, JSON.stringify(source, null, 2), 'application/json'));
$('importButton').addEventListener('click', () => {
  if (offline) notice('Run the local review desk to import an inventory-review or Security Delta JSON report.');
  else if (aiBusy) notice('Wait for the current AI summary before importing another report.');
  else $('reportFile').click();
});
$('reportFile').addEventListener('change', async () => {
  const file = $('reportFile').files[0];
  if (!file) return;
  $('importButton').disabled = true; $('aiButton').disabled = true;
  try {
    if (file.size > 8 * 1024 * 1024) throw new Error('Choose a report smaller than 8 MB.');
    const incoming = JSON.parse(await file.text());
    const result = await request('/api/prepare', { report: incoming });
    const type = result.report.report_type || 'inventory';
    workspaces.set(type, { report: result.report, source: result.source || incoming, demo: false });
    activateWorkspace(type);
    notice(`Imported ${file.name}. Drafts and evidence are scoped to this report.`);
  } catch (error) { notice(`Report was not imported: ${error.message}`, true); }
  finally { $('reportFile').value = ''; $('importButton').disabled = false; $('aiButton').disabled = false; }
});
$('aiButton').addEventListener('click', () => { $('aiDialog').showModal(); if (!aiBusy) refreshModels(); });
$('closeAI').addEventListener('click', () => $('aiDialog').close());
$('refreshModels').addEventListener('click', refreshModels);
$('generateAI').addEventListener('click', async () => {
  const reportId = report.report_id;
  aiBusy = true; $('generateAI').disabled = true; $('refreshModels').disabled = true;
  $('modelStatus').textContent = 'Generating a cited draft. This can take up to 45 seconds…';
  try {
    const result = await request('/api/summary', { model: $('modelSelect').value, report: source }, 55000);
    if (reportId !== report.report_id || result.report_id !== report.report_id) throw new Error('The report changed; generate a fresh summary.');
    renderAI(result); $('aiDialog').close(); notice('AI draft ready. Review the cited source records before using its suggestions.');
  } catch (error) { $('modelStatus').textContent = error.message; }
  finally { aiBusy = false; $('generateAI').disabled = false; $('refreshModels').disabled = false; }
});

try {
  const value = localStorage.getItem(storageKey);
  if (value && value.length <= 2 * 1024 * 1024) {
    const parsed = JSON.parse(value);
    if (Array.isArray(parsed)) savedDrafts = parsed.filter(validSavedDraft).slice(-500);
  }
} catch { notice('Browser storage is unavailable. Drafts can still be downloaded.'); }
$('demoLabel').hidden = !initial.demo;
renderOverview(); renderQueue(); showView('review');
