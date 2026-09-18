"""Validate collected snapshot integrity and bind comparisons to their sources."""

import hashlib
import ipaddress
import json
import re
from collections import Counter
from urllib.parse import urlsplit, urlunsplit

from inventory_review import compare, index_devices, snapshot_time

MAX_ROWS = 25000
HASH = re.compile(r"[0-9a-f]{64}")


def digest(value):
    """Hash canonical JSON without accepting non-finite numbers."""
    content = json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def source_endpoint(value):
    """Canonicalize a credential-free API base, requiring TLS except on loopback."""
    try:
        parsed = urlsplit(value)
        host, port = parsed.hostname, parsed.port
        if (
            parsed.scheme not in ("https", "http")
            or not host
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
            or (port is not None and not 1 <= port <= 65535)
            or not re.fullmatch(r"[A-Za-z0-9._~/-]*", parsed.path)
            or any(part in (".", "..") for part in parsed.path.split("/"))
        ):
            raise ValueError
        loopback = host.lower() == "localhost"
        try:
            loopback = loopback or ipaddress.ip_address(host).is_loopback
        except ValueError:
            pass
        if parsed.scheme == "http" and not loopback:
            raise ValueError
        if not re.fullmatch(r"[A-Za-z0-9.:-]+", host):
            raise ValueError
        authority = f"[{host.lower()}]" if ":" in host else host.lower()
        if port and port != (443 if parsed.scheme == "https" else 80):
            authority += f":{port}"
        return urlunsplit((parsed.scheme, authority, parsed.path.rstrip("/"), "", ""))
    except (ValueError, TypeError, AttributeError) as error:
        raise ValueError(
            "Use an HTTPS API base without credentials, query or fragment; HTTP is allowed only on loopback"
        ) from error


def ordered_rows(rows):
    """Validate nonempty native rows and sort by their original MAC identifier."""
    if not isinstance(rows, list) or not rows or len(rows) > MAX_ROWS:
        raise ValueError("Expected 1–25,000 complete device rows")
    index_devices(rows)
    columns = set(rows[0])
    if any(set(row) != columns for row in rows):
        raise ValueError("Device rows have inconsistent column coverage")
    for row in rows:
        if any(
            value is not None and not isinstance(value, (str, int, float, bool))
            for value in row.values()
        ):
            raise ValueError("Native device values must be scalars")
        if type(row.get("devIsArchived")) is not int or row["devIsArchived"] not in (
            0,
            1,
        ):
            raise ValueError("Device archive status is missing or unsupported")
    result = sorted(rows, key=lambda row: row["devMac"])
    digest(result)
    return result


def validate_collection(collection):
    """Validate collection metadata, including traversal counts and integrity."""
    if not isinstance(collection, dict) or collection.get("schema_version") != 1:
        raise ValueError("Missing version 1 collection evidence")
    endpoint = collection.get("source_endpoint")
    if source_endpoint(endpoint) != endpoint:
        raise ValueError("Collection source is not canonical")
    scope = collection.get("scope")
    if (
        not isinstance(scope, str)
        or not scope.strip()
        or scope != scope.strip()
        or len(scope) > 240
    ):
        raise ValueError(
            "Collection scope must be a nonempty label of up to 240 characters"
        )
    start, end = collection.get("started_at"), collection.get("completed_at")
    if (
        not isinstance(start, str)
        or not isinstance(end, str)
        or not start.endswith("Z")
        or not end.endswith("Z")
        or snapshot_time(end) < snapshot_time(start)
    ):
        raise ValueError("Collection times must be chronological UTC timestamps")
    count = collection.get("row_count")
    if type(count) is not int or not 1 <= count <= MAX_ROWS:
        raise ValueError("Collection row count is outside supported bounds")
    data_hash = collection.get("data_sha256")
    if not isinstance(data_hash, str) or not HASH.fullmatch(data_hash):
        raise ValueError("Collection data hash is invalid")
    coverage = collection.get("coverage")
    if not isinstance(coverage, dict):
        raise ValueError("Collection coverage is missing")
    fixed = {
        "status": "consistent_api_traversals",
        "method": "two_paginated_passes_and_full_export",
        "atomicity": "not_guaranteed",
        "scan_freshness": "unverified",
    }
    if any(coverage.get(key) != value for key, value in fixed.items()):
        raise ValueError("Collection coverage or uncertainty status is unsupported")
    page_size, passes = coverage.get("page_size"), coverage.get("passes")
    if (
        type(page_size) is not int
        or not 1 <= page_size <= 1000
        or not isinstance(passes, list)
        or len(passes) != 2
    ):
        raise ValueError("Collection must record two bounded page traversals")
    for traversal in passes:
        if not isinstance(traversal, dict):
            raise ValueError("Invalid page traversal evidence")
        pages = traversal.get("page_rows")
        if (
            not isinstance(pages, list)
            or len(pages) < 2
            or type(pages[-1]) is not int
            or pages[-1] != 0
            or any(
                type(size) is not int or not 1 <= size <= page_size
                for size in pages[:-1]
            )
            or any(size != page_size for size in pages[:-2])
            or sum(pages) != count
            or traversal.get("rows") != count
            or traversal.get("data_sha256") != data_hash
        ):
            raise ValueError(
                "Page traversal evidence does not establish consistent coverage"
            )
    if (
        coverage.get("export_rows") != count
        or coverage.get("export_sha256") != data_hash
    ):
        raise ValueError("Export evidence does not match paginated evidence")
    totals = coverage.get("totals")
    if not isinstance(totals, list) or len(totals) != 3:
        raise ValueError("Three bracketing total observations are required")
    for total in totals:
        if (
            not isinstance(total, dict)
            or set(total) != {"devices", "archived"}
            or any(type(value) is not int or value < 0 for value in total.values())
            or sum(total.values()) != count
            or total != totals[0]
        ):
            raise ValueError("Named totals are inconsistent with collection coverage")
    unhashed = {
        key: value for key, value in collection.items() if key != "evidence_sha256"
    }
    if digest(unhashed) != collection.get("evidence_sha256"):
        raise ValueError("Collection evidence integrity check failed")
    return collection


def validate_snapshot(snapshot):
    """Check native rows against collection metadata before using them as evidence."""
    if not isinstance(snapshot, dict) or snapshot.get("format") != "json":
        raise ValueError("Expected a collected native-compatible JSON snapshot")
    collection = validate_collection(snapshot.get("collection"))
    rows = ordered_rows(snapshot.get("data"))
    if (
        rows != snapshot["data"]
        or len(rows) != collection["row_count"]
        or digest(rows) != collection["data_sha256"]
    ):
        raise ValueError("Snapshot rows do not match their recorded integrity hash")
    if snapshot.get("columns") != sorted(rows[0]):
        raise ValueError("Snapshot columns do not match the observed rows")
    archived = sum(row["devIsArchived"] for row in rows)
    if collection["coverage"]["totals"][0] != {
        "devices": len(rows) - archived,
        "archived": archived,
    }:
        raise ValueError("Snapshot archive statuses conflict with named totals")
    return snapshot


def validate_comparison_evidence(report):
    """Verify source consistency and the report's unkeyed integrity binding."""
    evidence = report.get("collection_evidence")
    if not isinstance(evidence, dict) or evidence.get("schema_version") != 1:
        raise ValueError("Missing comparison collection evidence")
    before, after = (
        validate_collection(evidence.get(key)) for key in ("before", "after")
    )
    for key in ("source_endpoint", "scope"):
        if before[key] != after[key]:
            raise ValueError("Collected snapshots must have the same source and scope")
    if snapshot_time(after["started_at"]) <= snapshot_time(before["completed_at"]):
        raise ValueError(
            "Current collection must start after the baseline collection finished"
        )
    expected = {
        "scope": before["scope"],
        "before_at": before["completed_at"],
        "after_at": after["completed_at"],
        "baseline_devices": before["row_count"],
        "current_devices": after["row_count"],
    }
    if any(report.get(key) != value for key, value in expected.items()):
        raise ValueError(
            "Comparison scope, times or counts do not match collection evidence"
        )
    body = {key: value for key, value in report.items() if key != "collection_evidence"}
    if digest(body) != evidence.get("report_sha256"):
        raise ValueError("Comparison evidence integrity check failed")
    return evidence


def build_comparison(previous, current):
    """Create a desk-compatible report from two verified, nonoverlapping collections."""
    before, after = (validate_snapshot(item) for item in (previous, current))
    changes = compare(before["data"], after["data"])
    report = {
        "schema_version": 1,
        "scope": before["collection"]["scope"],
        "before_at": before["collection"]["completed_at"],
        "after_at": after["collection"]["completed_at"],
        "baseline_devices": len(before["data"]),
        "current_devices": len(after["data"]),
        "counts": dict(Counter(item["change"] for item in changes)),
        "ownership": dict(Counter(item["owner_status"] for item in changes)),
        "devices": changes,
    }
    report["collection_evidence"] = {
        "schema_version": 1,
        "before": before["collection"],
        "after": after["collection"],
        "report_sha256": digest(report),
    }
    validate_comparison_evidence(report)
    return report
