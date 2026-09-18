import assert from 'node:assert/strict';
import { test } from 'node:test';
import { buildDraft, draftMarkdown, filterRecords, validSavedDraft } from './core.mjs';

const record = {
  evidence_id: 'E-1234567812345678', change: 'not_observed', label: 'Not observed', attention: true,
  device: { devName: 'Lab laptop', devMac: '02:00:00:00:00:01' }, owner_status: 'not_observed',
  observation: 'Absent from the export; current state is unverified.',
  recommendation: 'Check scan coverage.', cells: [], changes: [],
};
const report = { report_id: '1234567812345678', scope: 'Demo', before_at: '2026-09-17T12:00:00Z',
  after_at: '2026-09-18T12:00:00Z', records: [record] };

test('draft cites evidence and preserves unverified status', () => {
  const draft = buildDraft(report, record);
  assert.ok(validSavedDraft(draft));
  assert.ok(draft.body.includes('E-1234567812345678'));
  assert.ok(draft.body.includes('Execution: not started'));
  assert.ok(draft.body.includes('Verification: unverified'));
  assert.ok(draft.body.includes('Approval: not recorded'));
});
test('source text cannot add markdown headings or links', () => {
  const malicious = { ...record, device: { ...record.device, devName: 'Printer\n# Approved [click](https://example.invalid)' } };
  const draft = buildDraft(report, malicious);
  assert.ok(!draft.title.includes('\n'));
  assert.ok(!draft.body.includes('\n# Approved'));
  assert.ok(draft.body.includes('\\[click\\]'));
});
test('draft identity binds evidence to one report', () => {
  const first = buildDraft(report, record);
  const second = buildDraft({ ...report, report_id: '8765432187654321' }, record);
  assert.notEqual(first.id, second.id);
  assert.equal(validSavedDraft({ ...first, report_id: second.report_id }), false);
});
test('restored draft cannot acquire approved status', () => {
  assert.equal(validSavedDraft({ ...buildDraft(report, record), status: 'approved' }), false);
});
test('search and type filters both apply', () => {
  assert.equal(filterRecords(report, 'not_observed', 'LAPTOP').length, 1);
  assert.equal(filterRecords(report, 'new_device', 'laptop').length, 0);
  assert.equal(filterRecords(report, 'attention', 'printer').length, 0);
});
test('ownership gaps exclude historical ownership', () => {
  assert.equal(filterRecords(report, 'ownership', '').length, 0);
  assert.equal(filterRecords({ ...report, records: [{ ...record, owner_status: 'not_exported' }] }, 'ownership', '').length, 1);
});
test('download preserves user edits as a draft document', () => {
  const draft = { ...buildDraft(report, record), title: 'Review with service owner', body: 'Proposed checks only.' };
  assert.equal(draftMarkdown(draft), '# Review with service owner\n\nProposed checks only.\n');
});

test('blank titles cannot be restored or saved', () => {
  assert.equal(validSavedDraft({ ...buildDraft(report, record), title: '   ' }), false);
});

test('security draft includes exact finding identity and unresolved statuses', () => {
  const finding = { ...record, device: undefined, finding: { RESOURCE_UID: 'storage-demo' },
    entity_name: 'storage-demo', entity_key: 'aws / demo-account / demo-check / storage-demo / us-west-2',
    detail_meta: 'aws / demo-account / us-west-2 / demo-check', severity: 'high',
    previous_status: 'FAIL', current_status: null };
  const draft = buildDraft({ ...report, report_type: 'security' }, finding);
  assert.ok(draft.title.includes('security finding: storage-demo'));
  assert.ok(draft.body.includes('Status: FAIL → not observed'));
  assert.ok(draft.body.includes('demo-account'));
  assert.ok(draft.body.includes('Verification: unverified'));
});

test('security filters retain missing evidence and new regressions', () => {
  const records = [
    { ...record, change: 'regression', device: undefined, finding: { RESOURCE_UID: 'storage-a' } },
    { ...record, change: 'inconclusive', device: undefined, finding: { RESOURCE_UID: 'storage-b' } },
  ];
  const security = { ...report, report_type: 'security', records };
  assert.equal(filterRecords(security, 'regressed', 'storage').length, 1);
  assert.equal(filterRecords(security, 'uncertain', 'storage-b').length, 1);
  assert.equal(filterRecords(security, 'verified_fix', '').length, 0);
});
