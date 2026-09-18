export function isSecurity(report) {
  return report.report_type === 'security';
}

export function recordName(record) {
  return record.entity_name || record.device?.devName || record.device?.devMac || 'Unnamed record';
}

export function recordKey(record) {
  return record.entity_key || record.device?.devMac || '';
}

export function recordSubtitle(record) {
  return record.secondary_text || `${record.device?.devLastIP || 'IP not exported'} / ${record.device?.devSite || 'Site not exported'}`;
}

export function recordMetadata(record) {
  return record.detail_meta || `${recordKey(record)} / ${record.device?.devSite || 'Site not exported'}`;
}

export function presentation(report) {
  if (isSecurity(report)) return {
    type: 'security', title: 'Review the security findings.', singular: 'finding', plural: 'findings',
    heading: 'Security review', search: 'Search resource, check, account…',
    before: report.baseline_findings, after: report.current_findings,
    sentence: `${report.needs_review} of ${report.records.length} findings need review. Missing and inconclusive results remain unresolved.`,
    filters: [['attention', 'Needs review'], ['all', 'All findings'], ['regressed', 'New / regressed'],
      ['persistent_failure', 'Still failing'], ['verified_fix', 'Check now passes'], ['uncertain', 'Missing / inconclusive']],
    metrics: [['attention', report.needs_review, 'Need review'],
      ['regressed', ['new_failure', 'regression', 'newly_confirmed_failure'].reduce((sum, key) => sum + (report.counts[key] || 0), 0), 'New / regressed'],
      ['persistent_failure', report.counts.persistent_failure || 0, 'Still failing'],
      ['verified_fix', report.counts.verified_fix || 0, 'Check now passes'],
      ['uncertain', (report.counts.not_observed || 0) + (report.counts.inconclusive || 0), 'Missing / inconclusive']],
    coverage: 'A matching FAIL → PASS means this check now passes; full remediation is not independently verified. Missing findings remain unresolved. Scan scope, completion, and comparable check definitions require review.',
  };
  return {
    type: 'inventory', title: 'Review the network.', singular: 'device', plural: 'devices',
    heading: 'Device review', search: 'Search name, IP, owner…',
    before: report.baseline_devices, after: report.current_devices,
    sentence: `${report.needs_review} of ${report.records.length} device records need review. ${report.owner_gaps} ${report.owner_gaps === 1 ? "has" : "have"} missing ownership evidence.`,
    filters: [['attention', 'Needs review'], ['all', 'All devices'], ['new_device', 'New devices'],
      ['changed', 'Changed'], ['not_observed', 'Not observed'], ['ownership', 'Owner gaps']],
    metrics: [['attention', report.needs_review, 'Need review'], ['new_device', report.counts.new_device || 0, 'New devices'],
      ['changed', report.counts.changed || 0, 'Changed'], ['not_observed', report.counts.not_observed || 0, 'Not observed'],
      ['ownership', report.owner_gaps, 'Owner gaps']],
    coverage: 'An absent device is not proof of removal or disconnection. Snapshot times and scope were supplied with the report; scan freshness has not been independently verified.',
  };
}
