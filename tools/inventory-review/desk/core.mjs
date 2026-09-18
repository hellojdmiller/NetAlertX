import { recordName, recordKey } from './report_ui.mjs';

export function filterRecords(report, filter, query) {
  const term = query.trim().toLowerCase();
  return report.records.filter((record) => {
    const matches = filter === 'all' || (filter === 'attention' && record.attention) ||
      (filter === 'ownership' && ['unassigned', 'not_exported'].includes(record.owner_status)) ||
      (filter === 'regressed' && ['new_failure', 'regression', 'newly_confirmed_failure'].includes(record.change)) ||
      (filter === 'uncertain' && ['not_observed', 'inconclusive'].includes(record.change)) || record.change === filter;
    const text = [record.evidence_id, record.label, ...Object.values(record.device || record.finding || {})].join(' ').toLowerCase();
    return matches && text.includes(term);
  });
}

export function markdownText(value) {
  return String(value ?? '').replace(/[\r\n\t]+/g, ' ').replace(/([\\`*_{}\[\]<>|~])/g, '\\$1');
}

export function buildDraft(report, record) {
  const name = recordName(record);
  const purpose = { new_device: 'new device', changed: 'device changes', not_observed: 'unobserved device',
    unchanged: 'device ownership', incomplete_comparison: 'missing evidence' }[record.change];
  const title = `Review ${record.finding ? 'security finding' : purpose}: ${name}`.replace(/[\r\n\t]+/g, ' ').slice(0, 200);
  const facts = [
    `- Evidence: ${record.evidence_id}`,
    `- Report: ${report.report_id}`,
    `- Scope: ${markdownText(report.scope)}`,
    `- Baseline: ${markdownText(report.before_at)}`,
    `- Current snapshot: ${markdownText(report.after_at)}`,
    `- ${record.finding ? 'Finding' : 'Device'}: ${markdownText(name)} (${markdownText(recordKey(record))})`,
    `- Observation: ${markdownText(record.observation)}`,
  ];
  if (record.finding) {
    facts.push(`- Account and check: ${markdownText(record.detail_meta)}`);
    facts.push(`- Severity: ${markdownText(record.severity || 'not exported')}`);
    facts.push(`- Status: ${markdownText(record.previous_status || 'not observed')} → ${markdownText(record.current_status || 'not observed')}`);
  }
  for (const change of record.changes) {
    const label = record.cells.find((cell) => cell.field === change.field)?.label || change.field;
    facts.push(`- ${label}: ${markdownText(change.before) || '(blank)'} → ${markdownText(change.after) || '(blank)'}`);
  }
  return {
    id: `${report.report_id}:${record.evidence_id}`, report_id: report.report_id,
    evidence_id: record.evidence_id, title, status: 'draft',
    body: `## Review request\n\n${markdownText(record.recommendation)}\n\n## Source evidence\n\n${facts.join('\n')}\n\n` +
      '## Suggested checks\n\n- [ ] Confirm identity and collection scope.\n- [ ] Review the cited snapshot evidence.\n- [ ] Confirm ownership and any applicable change approval.\n- [ ] Record the proposed action and its verification method.\n\n' +
      '## Operating status\n\nRecommendation: proposed for review\nApproval: not recorded\nExecution: not started\nVerification: unverified\n\n' +
      'This is a local draft. No ticket has been submitted and no device action has been performed.\n',
  };
}

export function validSavedDraft(draft) {
  return draft && typeof draft === 'object' && /^[a-f0-9]{16}:E-[a-f0-9]{16}$/.test(draft.id) &&
    draft.id === `${draft.report_id}:${draft.evidence_id}` && draft.status === 'draft' &&
    typeof draft.title === 'string' && draft.title.trim().length > 0 && draft.title.length <= 200 &&
    typeof draft.body === 'string' && draft.body.length <= 20000;
}

export function draftMarkdown(draft) {
  return `# ${markdownText(draft.title)}\n\n${draft.body}\n`;
}
