"""Review two NetAlertX device exports without contacting the network."""

import argparse
import csv
import json
import re
from collections import Counter
from datetime import datetime
from pathlib import Path

from report_page import render_page

FIELDS = (
    "devName",
    "devOwner",
    "devLastIP",
    "devType",
    "devVlan",
    "devSite",
    "devLocation",
)
UNKNOWN = ("", "(unknown)", "unknown", "(name not found)")
LABELS = dict(
    zip(
        FIELDS,
        ("Name", "Owner", "IP address", "Type", "VLAN", "Site", "Location"),
        strict=True,
    )
)


def normalize_mac(value):
    """Normalize exported MACs and the built-in Internet root identifier."""
    if not isinstance(value, str):
        raise ValueError("devMac must be a string")
    value = value.strip().lower()
    if value == "internet":
        return value
    if not re.fullmatch(
        r"(?:[0-9a-f]{2}:){5}[0-9a-f]{2}|(?:[0-9a-f]{2}-){5}[0-9a-f]{2}|[0-9a-f]{12}|(?:[0-9a-f]{4}\.){2}[0-9a-f]{4}",
        value,
    ):
        raise ValueError("Invalid devMac in export")
    digits = re.sub(r"[:.\-]", "", value)
    return ":".join(digits[index : index + 2] for index in range(0, 12, 2))


def index_devices(rows):
    """Index devices by normalized MAC and reject ambiguous duplicate rows."""
    result = {}
    for number, row in enumerate(rows, 1):
        if not isinstance(row, dict):
            raise ValueError(f"Device {number}: expected an object")
        mac = normalize_mac(row.get("devMac"))
        if mac in result:
            raise ValueError(
                f"Device {number}: duplicate MAC; use one complete export per file"
            )
        clean = {}
        for field in FIELDS:
            if field in row:
                value = row[field]
                if value is not None and not isinstance(value, (str, int, float)):
                    raise ValueError(f"Device {number}: {field} must be a scalar")
                clean[field] = "" if value is None else str(value).strip()
        clean["devMac"] = mac
        result[mac] = clean
    return result


def read_devices(path):
    """Read native device JSON or comma CSV exports; reject empty snapshots."""
    if path.suffix.lower() == ".csv":
        with path.open(encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            if not reader.fieldnames or "devMac" not in reader.fieldnames:
                raise ValueError("CSV must include devMac")
            if len(reader.fieldnames) != len(set(reader.fieldnames)):
                raise ValueError("Duplicate CSV headers")
            rows = list(reader)
        if any(
            None in row or any(value is None for value in row.values()) for row in rows
        ):
            raise ValueError("Malformed CSV row length")
    else:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
        rows = payload.get("data") if isinstance(payload, dict) else payload
    if not isinstance(rows, list) or not rows:
        raise ValueError(
            "Expected a nonempty complete device export; empty data cannot establish coverage"
        )
    index_devices(rows)
    return rows


def compare(previous, current):
    """Separate observed changes, missing devices and missing field coverage."""
    before, after = index_devices(previous), index_devices(current)
    result = []
    for mac in sorted(before.keys() | after.keys()):
        old, new = before.get(mac), after.get(mac)
        changes = []
        gaps = []
        if old is not None and new is not None:
            for field in FIELDS:
                if field not in old or field not in new:
                    gaps.append(field)
                elif old[field] != new[field]:
                    changes.append(
                        {"field": field, "before": old[field], "after": new[field]}
                    )
        kind = (
            "not_observed"
            if new is None
            else "new_device"
            if old is None
            else "changed"
            if changes
            else "incomplete_comparison"
            if gaps
            else "unchanged"
        )
        device = new if new is not None else old
        owner = (
            "not_observed"
            if new is None
            else "not_applicable"
            if mac == "internet"
            else "not_exported"
            if "devOwner" not in new
            else "unassigned"
            if new["devOwner"].lower() in UNKNOWN
            else "assigned"
        )
        result.append(
            {
                "change": kind,
                "device": device,
                "owner_status": owner,
                "changes": changes,
                "uncompared_fields": gaps,
            }
        )
    order = {
        "new_device": 0,
        "changed": 1,
        "not_observed": 2,
        "incomplete_comparison": 3,
        "unchanged": 4,
    }
    return sorted(
        result, key=lambda item: (order[item["change"]], item["device"]["devMac"])
    )


def snapshot_time(value):
    """Require explicit timezone information for snapshot ordering."""
    moment = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if moment.utcoffset() is None:
        raise ValueError("Snapshot times must include a timezone")
    return moment


def main():
    """Generate a portable report for two operator-selected device exports."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--before", type=Path, required=True)
    parser.add_argument("--after", type=Path, required=True)
    parser.add_argument("--before-at", required=True)
    parser.add_argument("--after-at", required=True)
    parser.add_argument("--scope", required=True)
    parser.add_argument("--output", type=Path, required=True, help=".html or .json")
    args = parser.parse_args()
    try:
        if snapshot_time(args.after_at) <= snapshot_time(args.before_at):
            raise ValueError("Current snapshot must be newer than baseline")
        if args.output.resolve() in (args.before.resolve(), args.after.resolve()):
            raise ValueError("Output cannot overwrite an input")
        if args.output.suffix.lower() not in (".html", ".json"):
            raise ValueError("Output must end in .html or .json")
        previous, current = read_devices(args.before), read_devices(args.after)
        changes = compare(previous, current)
        counts = dict(Counter(item["change"] for item in changes))
        owner_counts = dict(Counter(item["owner_status"] for item in changes))
        report = {
            "schema_version": 1,
            "scope": args.scope,
            "before_at": args.before_at,
            "after_at": args.after_at,
            "baseline_devices": len(previous),
            "current_devices": len(current),
            "counts": counts,
            "ownership": owner_counts,
            "devices": changes,
        }
        if args.output.suffix.lower() == ".json":
            content = json.dumps(report, indent=2) + "\n"
        else:
            rows = []
            for item in changes:
                device = item["device"]
                detail = "; ".join(
                    f"{LABELS[change['field']]}: {change['before'] or '(blank)'} → "
                    f"{change['after'] or '(blank)'}"
                    for change in item["changes"]
                )
                if item["uncompared_fields"]:
                    detail += (
                        ("; " if detail else "")
                        + "Not compared: "
                        + ", ".join(
                            LABELS[field] for field in item["uncompared_fields"]
                        )
                    )
                if item["change"] == "new_device":
                    detail = "First observed in the current export"
                elif item["change"] == "not_observed":
                    detail = "Last known baseline values; current state is unverified"
                rows.append(
                    [
                        item["change"].replace("_", " "),
                        device.get("devName", "not exported"),
                        device["devMac"],
                        device.get("devLastIP", "not exported"),
                        device.get("devSite", "not exported"),
                        device.get("devOwner")
                        or item["owner_status"].replace("_", " "),
                        item["owner_status"].replace("_", " "),
                        detail or "No field changes observed",
                    ]
                )
            cards = [
                ("New devices", counts.get("new_device", 0)),
                ("Changed devices", counts.get("changed", 0)),
                ("Not observed", counts.get("not_observed", 0)),
                (
                    "Owners needing review",
                    owner_counts.get("unassigned", 0)
                    + owner_counts.get("not_exported", 0),
                ),
            ]
            content = render_page(
                "Inventory review",
                "NetAlertX · Network change review",
                args.scope,
                args.before_at + " → " + args.after_at,
                cards,
                [
                    "Change",
                    "Device",
                    "MAC",
                    "IP",
                    "Site",
                    "Owner",
                    "Ownership evidence",
                    "Details",
                ],
                rows,
                "Not observed means absent from this export; it does not prove a device was removed or is offline. "
                "Missing fields are not treated as blank values. New devices may be newly discovered, not newly installed. "
                "Use complete exports from the same instance and scope. Times and scope are operator supplied.",
            )
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(content, encoding="utf-8")
        print(
            json.dumps(
                {
                    "output": str(args.output),
                    "counts": counts,
                    "ownership": owner_counts,
                }
            )
        )
    except (ValueError, OSError, csv.Error) as error:
        parser.exit(2, f"Cannot create report: {error}\n")


if __name__ == "__main__":
    main()
