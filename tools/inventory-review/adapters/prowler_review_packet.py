"""Normalize Security Delta version 1 evidence for review tools.

Experimental extension in JD Miller's Prowler fork, licensed Apache-2.0.
This module uses only the Python standard library and never accesses a network.
"""

import hashlib
import json
from collections import Counter
from datetime import datetime

IDENTITY = ("PROVIDER", "ACCOUNT_UID", "CHECK_ID", "RESOURCE_UID", "REGION")
RATIONALES = {
    "new_failure": "The current check reports FAIL without a baseline observation. This does not establish when the condition began.",
    "regression": "The same finding identity changed from an explicit PASS to FAIL.",
    "persistent_failure": "The same finding identity reports FAIL in both snapshots. Muting does not resolve a failure.",
    "newly_confirmed_failure": "The baseline was inconclusive and the current check reports FAIL. An earlier PASS is not established.",
    "verified_fix": "The matching Prowler check changed from FAIL to PASS. Full remediation and compliance are not independently verified.",
    "not_observed": "No current observation matches this baseline finding. Resolution is unverified; absence is not a passing result.",
    "inconclusive": "The current check does not report an explicit PASS or FAIL. The outcome remains inconclusive.",
    "observed_pass": "The current check reports PASS without a matching baseline FAIL. This does not establish a verified fix.",
}
RECOMMENDATIONS = {
    "new_failure": "Confirm scan coverage and the reported condition, then propose a scoped change for review.",
    "regression": "Confirm comparable check definitions and investigate the change before proposing remediation.",
    "persistent_failure": "Review the existing remediation or exception record and confirm an accountable owner.",
    "newly_confirmed_failure": "Confirm the current failure and investigate why the baseline was inconclusive.",
    "verified_fix": "Retain the matching PASS evidence and review any wider acceptance criteria before closing work.",
    "not_observed": "Check account, region, check coverage and scan completion; obtain a current observation before closing work.",
    "inconclusive": "Investigate the manual, error or unknown status and obtain conclusive evidence before deciding an outcome.",
    "observed_pass": "Retain the observation and confirm comparable scan coverage before using it as assurance.",
}


def digest(value):
    """Create a deterministic identifier from structured, unambiguous content."""
    encoded = json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    )
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()[:16]


def classify(previous, current):
    """Derive the version 1 classification from explicit observations only."""
    if current is None:
        return "not_observed"
    if current == "FAIL":
        if previous is None:
            return "new_failure"
        if previous == "PASS":
            return "regression"
        return "persistent_failure" if previous == "FAIL" else "newly_confirmed_failure"
    if current == "PASS":
        return "verified_fix" if previous == "FAIL" else "observed_pass"
    return "inconclusive"


def valid_text(value, maximum=32768, blank=True):
    """Accept bounded exported strings, rejecting hidden control characters."""
    return (
        isinstance(value, str)
        and len(value) <= maximum
        and (blank or bool(value.strip()))
        and not any(ord(char) < 32 and char not in "\n\r\t" for char in value)
    )


def validate_report(report):
    """Reject contradictions instead of turning malformed evidence into success."""
    if (
        not isinstance(report, dict)
        or type(report.get("schema_version")) is not int
        or report["schema_version"] != 1
    ):
        raise ValueError("Choose a Security Delta version 1 JSON report")
    if not valid_text(report.get("scope"), 4096, blank=False):
        raise ValueError("Report scope must be nonempty text")
    moments = []
    for field in ("before_at", "after_at"):
        value = report.get(field)
        if not valid_text(value, 128, blank=False):
            raise ValueError("Snapshot times must be timezone-aware ISO timestamps")
        try:
            moment = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as error:
            raise ValueError(
                "Snapshot times must be timezone-aware ISO timestamps"
            ) from error
        if moment.utcoffset() is None:
            raise ValueError("Snapshot times must include a timezone")
        moments.append(moment)
    if moments[1] <= moments[0]:
        raise ValueError("Current snapshot must be newer than baseline")
    findings = report.get("findings")
    if not isinstance(findings, list) or not 1 <= len(findings) <= 25000:
        raise ValueError("Report must contain between 1 and 25000 findings")
    identities = set()
    observed = [0, 0]
    for item in findings:
        if not isinstance(item, dict):
            raise ValueError("Each finding must be an object")
        finding = item.get("finding")
        if (
            not isinstance(finding, dict)
            or len(finding) > 256
            or any(
                not valid_text(key, 256, blank=False) or not valid_text(value)
                for key, value in finding.items()
            )
        ):
            raise ValueError("Finding source fields must be bounded text values")
        if any(
            not valid_text(finding.get(field), blank=field == "REGION")
            for field in IDENTITY
        ):
            raise ValueError("Finding identity is incomplete")
        identity = tuple(finding[field] for field in IDENTITY)
        if identity in identities:
            raise ValueError("Duplicate finding identity")
        identities.add(identity)
        statuses = []
        for index, field in enumerate(("previous_status", "current_status")):
            if field not in item:
                raise ValueError(
                    "Both finding status fields must be present; null means not observed"
                )
            status = item[field]
            if status is not None:
                if (
                    not valid_text(status, 128, blank=False)
                    or status != status.strip().upper()
                ):
                    raise ValueError(
                        "Observed statuses must be nonempty uppercase text"
                    )
                observed[index] += 1
            statuses.append(status)
        if all(status is None for status in statuses):
            raise ValueError("A finding must be observed in at least one snapshot")
        if finding.get("STATUS") != (statuses[1] or statuses[0]):
            raise ValueError(
                "Source finding status contradicts the exported observation"
            )
        if item.get("change") != classify(*statuses):
            raise ValueError("Finding classification contradicts the exported statuses")
    for field, actual in zip(
        ("baseline_findings", "current_findings"), observed, strict=True
    ):
        if (
            type(report.get(field)) is not int
            or report[field] < 1
            or report[field] != actual
        ):
            raise ValueError(
                "Snapshot finding totals contradict the supplied observations"
            )
    counts = report.get("counts")
    if (
        not isinstance(counts, dict)
        or any(
            key not in RATIONALES or type(value) is not int or value < 0
            for key, value in counts.items()
        )
        or {key: value for key, value in counts.items() if value}
        != dict(Counter(item["change"] for item in findings))
    ):
        raise ValueError("Summary counts contradict the supplied findings")


def prepare_packet(report):
    """Return source-bound findings without adding operational approvals."""
    validate_report(report)
    canonical = {
        key: report[key]
        for key in (
            "schema_version",
            "scope",
            "before_at",
            "after_at",
            "baseline_findings",
            "current_findings",
            "counts",
            "findings",
        )
    }
    findings = [
        {
            key: item[key]
            for key in ("change", "previous_status", "current_status", "finding")
        }
        for item in report["findings"]
    ]
    counts = dict(Counter(item["change"] for item in findings))
    canonical["counts"] = counts
    canonical["findings"] = sorted(
        findings,
        key=lambda item: tuple(item["finding"][field] for field in IDENTITY),
    )
    report_id = digest(canonical)
    records = []
    evidence_ids = set()
    for item in findings:
        change = item["change"]
        identity = {field: item["finding"][field] for field in IDENTITY}
        previous = item["previous_status"] or "Not observed"
        current = item["current_status"] or "Not observed"
        evidence_id = "E-" + digest([report_id, identity])
        if evidence_id in evidence_ids:
            raise ValueError(
                "Evidence identifier collision; cannot safely link findings"
            )
        evidence_ids.add(evidence_id)
        records.append(
            {
                **item,
                "finding": dict(item["finding"]),
                "identity": identity,
                "evidence_id": evidence_id,
                "observation": f"{previous} → {current} for {identity['CHECK_ID']} on {identity['RESOURCE_UID']}.",
                "rationale": RATIONALES[change],
                "recommendation": RECOMMENDATIONS[change],
                "attention": change not in ("observed_pass", "verified_fix"),
            }
        )
    return {
        "packet_version": 1,
        "source": "prowler",
        "report_id": report_id,
        **{
            key: report[key]
            for key in (
                "scope",
                "before_at",
                "after_at",
                "baseline_findings",
                "current_findings",
            )
        },
        "counts": counts,
        "needs_review": sum(record["attention"] for record in records),
        "records": records,
    }
