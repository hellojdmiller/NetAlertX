"""Turn inventory comparisons into cited observations and draft tickets."""

import hashlib
import json
from collections import Counter

from inventory_review import FIELDS, LABELS, UNKNOWN, index_devices, snapshot_time

KINDS = {"new_device", "changed", "not_observed", "unchanged", "incomplete_comparison"}
KIND_LABELS = {
    "new_device": "New device",
    "changed": "Changed",
    "not_observed": "Not observed",
    "unchanged": "Unchanged",
    "incomplete_comparison": "Incomplete evidence",
}
NEXT_STEPS = {
    "new_device": "Confirm the device identity, business purpose, and owner before changing access.",
    "changed": "Confirm that the observed changes match an authorized change record.",
    "not_observed": "Check collection coverage and the device record before concluding it was removed or is offline.",
    "incomplete_comparison": "Collect the missing fields before concluding that the device is unchanged.",
    "unchanged": "Review ownership evidence and confirm whether any follow-up is needed.",
}


def bounded_text(value, name, maximum=2000):
    """Require bounded text and reject control characters in imported evidence."""
    if (
        not isinstance(value, str)
        or len(value) > maximum
        or any(ord(c) < 32 and c not in "\n\t\r" for c in value)
    ):
        raise ValueError(f"Invalid {name}")
    return value


def normalize_record(raw):
    """Validate comparison evidence and derive ownership from source fields."""
    if (
        not isinstance(raw, dict)
        or not isinstance(raw.get("change"), str)
        or raw["change"] not in KINDS
    ):
        raise ValueError("Unknown device comparison state")
    device = next(iter(index_devices([raw.get("device")]).values()))
    for field, value in device.items():
        bounded_text(value, field)
    changes = raw.get("changes")
    gaps = raw.get("uncompared_fields")
    if not isinstance(changes, list) or not isinstance(gaps, list):
        raise ValueError("Comparison fields must be arrays")
    if any(not isinstance(field, str) or field not in FIELDS for field in gaps) or len(
        gaps
    ) != len(set(gaps)):
        raise ValueError("Invalid missing-field coverage")
    clean_changes = []
    seen = set()
    for change in changes:
        if not isinstance(change, dict) or change.get("field") not in FIELDS:
            raise ValueError("Invalid changed field")
        field = change["field"]
        before = bounded_text(change.get("before"), "baseline value")
        after = bounded_text(change.get("after"), "current value")
        if (
            field in seen
            or field in gaps
            or before == after
            or device.get(field) != after
        ):
            raise ValueError("Conflicting change evidence")
        seen.add(field)
        clean_changes.append({"field": field, "before": before, "after": after})
    kind = raw["change"]
    if bool(changes) != (kind == "changed") or (kind == "unchanged" and gaps):
        raise ValueError("Comparison status conflicts with its evidence")
    if kind == "incomplete_comparison" and not gaps:
        raise ValueError("Incomplete comparison must identify missing fields")
    if kind in ("new_device", "not_observed") and gaps:
        raise ValueError("An unmatched device cannot have compared fields")
    owner = (
        "not_observed"
        if kind == "not_observed"
        else "not_applicable"
        if device["devMac"] == "internet"
        else "not_exported"
        if "devOwner" not in device
        else "unassigned"
        if device["devOwner"].lower() in UNKNOWN
        else "assigned"
    )
    return {
        "change": kind,
        "device": device,
        "owner_status": owner,
        "changes": sorted(clean_changes, key=lambda item: item["field"]),
        "uncompared_fields": sorted(gaps),
    }


def source_cells(record):
    """Expose known snapshot values without reconstructing missing evidence."""
    changes = {change["field"]: change for change in record["changes"]}
    cells = []
    for field in FIELDS:
        current = record["device"].get(field)
        before = changes[field]["before"] if field in changes else current
        before_state = "observed" if before is not None else "not_exported"
        current_state = "observed" if current is not None else "not_exported"
        if record["change"] == "new_device":
            before, before_state = None, "not_observed"
        elif record["change"] == "not_observed":
            current, current_state = None, "not_observed"
        elif field in record["uncompared_fields"]:
            before, before_state = None, "not_comparable"
        cells.append(
            {
                "field": field,
                "label": LABELS[field],
                "before": before,
                "current": current,
                "before_state": before_state,
                "current_state": current_state,
                "changed": field in changes,
            }
        )
    return cells


def observation(record):
    """Describe the directly supported observation in one concise sentence."""
    name = record["device"].get("devName") or record["device"]["devMac"]
    kind = record["change"]
    if kind == "new_device":
        text = f"{name} appears in the current export without a baseline observation."
    elif kind == "not_observed":
        text = f"{name} is absent from the current export; its current state is unverified."
    elif kind == "changed":
        fields = ", ".join(
            LABELS[item["field"]]
            if item["field"] in ("devLastIP", "devVlan")
            else LABELS[item["field"]].lower()
            for item in record["changes"]
        )
        text = f"{name} has observed changes to {fields}."
    elif kind == "incomplete_comparison":
        text = f"{name} has missing comparison fields; unchanged status is not established."
    else:
        text = f"{name} has no observed changes in the compared fields."
    if record["owner_status"] == "unassigned":
        text += " Its current owner field is blank or unknown."
    elif record["owner_status"] == "not_exported":
        text += " Its owner field was not exported."
    return text


def prepare_report(raw):
    """Build a stable cited review document from a validated inventory report."""
    if (
        not isinstance(raw, dict)
        or type(raw.get("schema_version")) is not int
        or raw["schema_version"] != 1
    ):
        raise ValueError("Choose a version 1 inventory-review JSON report")
    scope = bounded_text(raw.get("scope"), "scope", 300).strip()
    if not scope:
        raise ValueError("A report scope is required")
    before_at = bounded_text(raw.get("before_at"), "baseline time", 80)
    after_at = bounded_text(raw.get("after_at"), "current time", 80)
    if snapshot_time(after_at) <= snapshot_time(before_at):
        raise ValueError("The current snapshot must be newer than its baseline")
    if (
        not isinstance(raw.get("devices"), list)
        or not 1 <= len(raw["devices"]) <= 25000
    ):
        raise ValueError("Reports must contain between 1 and 25,000 device records")
    records = [normalize_record(item) for item in raw["devices"]]
    records.sort(key=lambda record: record["device"]["devMac"])
    if len({record["device"]["devMac"] for record in records}) != len(records):
        raise ValueError("Duplicate device identity")
    baseline_count = sum(item["change"] != "new_device" for item in records)
    current_count = sum(item["change"] != "not_observed" for item in records)
    if baseline_count == 0 or current_count == 0:
        raise ValueError("Both snapshots must contain observed devices")
    for field, actual in [
        ("baseline_devices", baseline_count),
        ("current_devices", current_count),
    ]:
        if type(raw.get(field)) is not int or raw[field] != actual:
            raise ValueError("Reported snapshot counts conflict with device evidence")
    normalized = {
        "scope": scope,
        "before_at": before_at,
        "after_at": after_at,
        "devices": records,
    }
    digest = hashlib.sha256(
        json.dumps(normalized, sort_keys=True).encode()
    ).hexdigest()[:16]
    for record in records:
        record["evidence_id"] = (
            "E-"
            + hashlib.sha256(
                (digest + record["device"]["devMac"]).encode()
            ).hexdigest()[:16]
        )
        record["label"] = KIND_LABELS[record["change"]]
        record["attention"] = record["change"] != "unchanged" or record[
            "owner_status"
        ] in ("unassigned", "not_exported")
        record["observation"] = observation(record)
        record["recommendation"] = NEXT_STEPS[record["change"]]
        record["cells"] = source_cells(record)
    if len({record["evidence_id"] for record in records}) != len(records):
        raise ValueError("Evidence identities collided; this report cannot be reviewed")
    order = {
        "new_device": 0,
        "not_observed": 1,
        "changed": 2,
        "incomplete_comparison": 3,
        "unchanged": 4,
    }
    records.sort(
        key=lambda record: (
            not record["attention"],
            order[record["change"]],
            record["device"]["devMac"],
        )
    )
    return {
        "desk_version": 1,
        "report_id": digest,
        "scope": scope,
        "before_at": before_at,
        "after_at": after_at,
        "baseline_devices": baseline_count,
        "current_devices": current_count,
        "counts": dict(Counter(item["change"] for item in records)),
        "needs_review": sum(item["attention"] for item in records),
        "owner_gaps": sum(
            item["owner_status"] in ("unassigned", "not_exported") for item in records
        ),
        "records": records,
    }


def ai_evidence(report):
    """Bound model context and explicitly identify any omitted device records."""
    records = [item for item in report["records"] if item["attention"]][:40]
    return {
        "scope": report["scope"],
        "before_at": report["before_at"],
        "after_at": report["after_at"],
        "total_records": len(report["records"]),
        "included_records": len(records),
        "omitted_records": len(report["records"]) - len(records),
        "evidence": [
            {
                "evidence_id": item["evidence_id"],
                "observation": item["observation"],
                "suggested_review": item["recommendation"],
            }
            for item in records
        ],
    }


def validate_ai_summary(value, report):
    """Require bounded draft text and citations to evidence actually sent to the model."""
    if not isinstance(value, dict) or set(value) != {"observations", "next_steps"}:
        raise ValueError("The model did not return the expected summary structure")
    allowed = {item["evidence_id"] for item in ai_evidence(report)["evidence"]}
    for key, minimum, maximum in [("observations", 1, 6), ("next_steps", 0, 4)]:
        entries = value.get(key)
        if not isinstance(entries, list) or not minimum <= len(entries) <= maximum:
            raise ValueError("The model returned an invalid number of summary items")
        for item in entries:
            if not isinstance(item, dict) or set(item) != {"text", "evidence_ids"}:
                raise ValueError("The model returned unsupported summary fields")
            if not bounded_text(item.get("text"), "model text", 600).strip():
                raise ValueError("The model returned empty summary text")
            refs = item.get("evidence_ids")
            if (
                not isinstance(refs, list)
                or not 1 <= len(refs) <= 8
                or any(not isinstance(ref, str) or ref not in allowed for ref in refs)
            ):
                raise ValueError("The model cited missing or unprovided evidence")
            if len(refs) != len(set(refs)):
                raise ValueError("The model repeated an evidence citation")
    return {
        "report_id": report["report_id"],
        "kind": "ai_draft",
        "summary": value,
        "included_records": len(allowed),
        "total_records": len(report["records"]),
    }
