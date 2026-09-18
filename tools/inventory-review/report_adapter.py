"""Adapt inventory and Prowler comparison evidence to the local review workspace."""

from copy import deepcopy

from adapters.prowler_review_packet import prepare_packet
from desk_data import prepare_report as prepare_inventory
from snapshot_evidence import validate_comparison_evidence

SECURITY_LABELS = {
    "new_failure": "New failure",
    "regression": "Regression",
    "persistent_failure": "Still failing",
    "newly_confirmed_failure": "Newly confirmed failure",
    "verified_fix": "Check now passes",
    "not_observed": "Not observed",
    "inconclusive": "Inconclusive",
    "observed_pass": "Observed pass",
}


def security_cells(record):
    """Show exact statuses and identity, without inventing historical attributes."""
    cells = []
    old, new = record["previous_status"], record["current_status"]
    for field, label in (
        ("STATUS", "Check result"),
        ("RESOURCE_UID", "Resource"),
        ("CHECK_ID", "Check"),
        ("ACCOUNT_UID", "Account"),
        ("PROVIDER", "Provider"),
        ("REGION", "Region"),
        ("SEVERITY", "Severity"),
        ("MUTED", "Muted"),
    ):
        value = record["finding"].get(field)
        before = old if field == "STATUS" else value
        current = new if field == "STATUS" else value
        before_state = "observed" if before is not None else "not_exported"
        current_state = "observed" if current is not None else "not_exported"
        if old is None:
            before, before_state = None, "not_observed"
        elif new is not None and field in ("SEVERITY", "MUTED"):
            before, before_state = None, "not_exported"
        if new is None:
            current, current_state = None, "not_observed"
        cells.append(
            {
                "field": field,
                "label": label,
                "before": before,
                "current": current,
                "before_state": before_state,
                "current_state": current_state,
                "changed": field == "STATUS"
                and old is not None
                and new is not None
                and old != new,
            }
        )
    return cells


def prepare_report(raw):
    """Validate one recognized comparison and preserve source-specific semantics."""
    if not isinstance(raw, dict) or ("devices" in raw) == ("findings" in raw):
        raise ValueError(
            "Choose one inventory-review or Prowler Security Delta JSON report"
        )
    if "devices" in raw:
        evidence = (
            validate_comparison_evidence(raw) if "collection_evidence" in raw else None
        )
        result = prepare_inventory(raw, evidence)
        if evidence is not None:
            result["collection_evidence"] = deepcopy(evidence)
            result["_collected_source"] = deepcopy(raw)
        result["report_type"] = "inventory"
        return result
    packet = prepare_packet(raw)
    records = []
    for item in packet["records"]:
        finding = item["finding"]
        records.append(
            {
                **item,
                "label": SECURITY_LABELS[item["change"]],
                "entity_name": finding["RESOURCE_UID"],
                "entity_key": " / ".join(
                    finding.get(key, "")
                    for key in (
                        "PROVIDER",
                        "ACCOUNT_UID",
                        "CHECK_ID",
                        "RESOURCE_UID",
                        "REGION",
                    )
                ),
                "secondary_text": f"{finding['CHECK_ID']} / {finding['ACCOUNT_UID']}",
                "detail_meta": " / ".join(
                    finding.get(key, "")
                    for key in ("PROVIDER", "ACCOUNT_UID", "REGION", "CHECK_ID")
                ),
                "severity": finding.get("SEVERITY") or "not exported",
                "owner_status": "not_applicable",
                "changes": [],
                "observation": item["observation"] + " " + item["rationale"],
                "cells": security_cells(item),
            }
        )
    return {**packet, "desk_version": 1, "report_type": "security", "records": records}


def canonical_source(report):
    """Serialize validated source evidence for downloads and subsequent requests."""
    if "_collected_source" in report:
        return deepcopy(report["_collected_source"])
    result = {key: report[key] for key in ("scope", "before_at", "after_at")}
    result["schema_version"] = 1
    result["counts"] = report["counts"]
    security = report["report_type"] == "security"
    count_type = "findings" if security else "devices"
    for side in ("baseline", "current"):
        result[f"{side}_{count_type}"] = report[f"{side}_{count_type}"]
    keys = (
        ("change", "previous_status", "current_status", "finding")
        if security
        else ("change", "device", "owner_status", "changes", "uncompared_fields")
    )
    result[count_type] = [
        {key: item[key] for key in keys} for item in report["records"]
    ]
    return result


def public_report(report):
    """Keep preserved source bytes out of the derived browser presentation."""
    return {key: value for key, value in report.items() if not key.startswith("_")}
