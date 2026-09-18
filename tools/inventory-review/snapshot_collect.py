"""Collect read-only NetAlertX API snapshots or compare two recorded collections."""

import argparse
import getpass
import http.client
import json
import os
import re
import ssl
import sys
import tempfile
import time
import urllib.error
import urllib.request
import warnings
from pathlib import Path

from snapshot_evidence import (
    MAX_ROWS,
    build_comparison,
    digest,
    ordered_rows,
    source_endpoint,
    validate_snapshot,
)

MAX_RESPONSE_BYTES = 64 * 1024 * 1024


def utc_time():
    """Return an independent collector timestamp in UTC, without server dependencies."""
    now = time.time_ns()
    return (
        time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(now // 1_000_000_000))
        + f".{(now % 1_000_000_000) // 1000:06d}Z"
    )


def strict_object(pairs):
    """Reject duplicate object fields instead of silently accepting ambiguous JSON."""
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON field in API response")
        result[key] = value
    return result


def invalid_constant(value):
    """Reject nonstandard numeric constants in JSON."""
    raise ValueError("Non-finite number in JSON")


def parse_json(content):
    """Parse strict JSON used for responses and saved collection files."""
    return json.loads(
        content, object_pairs_hook=strict_object, parse_constant=invalid_constant
    )


class NoRedirect(urllib.request.HTTPRedirectHandler):
    """Keep authorization headers at the exact configured API endpoint."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        """Refuse all HTTP redirects without following their destination."""
        return None


class APIClient:
    """Read fixed NetAlertX endpoints using verified TLS and no environment proxies."""

    def __init__(self, endpoint, token, timeout=15):
        """Validate connection settings without recording the supplied token."""
        self.endpoint = source_endpoint(endpoint)
        if (
            not isinstance(token, str)
            or not token
            or not re.fullmatch(r"[!-~]+", token)
        ):
            raise ValueError("Provide a nonempty API token without whitespace")
        if not 1 <= timeout <= 120:
            raise ValueError("Request timeout must be between 1 and 120 seconds")
        self._token, self.timeout = token, timeout
        self.opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({}),
            NoRedirect(),
            urllib.request.HTTPSHandler(context=ssl.create_default_context()),
        )

    def get(self, path):
        """Return bounded JSON from the allowlisted read-only API paths."""
        if not re.fullmatch(
            r"/devices(?:\?limit=\d+&offset=\d+|/export/json|/totals/named)", path
        ):
            raise ValueError("Unsupported collection API path")
        request = urllib.request.Request(
            self.endpoint + path,
            headers={
                "Authorization": "Bearer " + self._token,
                "Accept": "application/json",
                "Accept-Encoding": "identity",
                "User-Agent": "NetAlertX-Inventory-Collector/1",
            },
            method="GET",
        )
        try:
            with self.opener.open(request, timeout=self.timeout) as response:
                if (
                    response.status != 200
                    or response.headers.get_content_type() != "application/json"
                ):
                    raise ValueError(
                        "API returned an unexpected response status or content type"
                    )
                if (
                    response.headers.get("Content-Encoding", "identity").lower()
                    != "identity"
                ):
                    raise ValueError("Compressed API responses are not supported")
                raw = response.read(MAX_RESPONSE_BYTES + 1)
                if len(raw) > MAX_RESPONSE_BYTES:
                    raise ValueError(
                        "API response exceeded the 64 MiB limit; no snapshot was saved"
                    )
                return parse_json(raw)
        except urllib.error.HTTPError as error:
            status = error.code
            error.close()
            raise ValueError(
                f"API request failed with HTTP {status}; no snapshot was saved"
            ) from None
        except (
            urllib.error.URLError,
            http.client.HTTPException,
            TimeoutError,
            OSError,
        ):
            raise ValueError(
                "API connection failed or timed out; no snapshot was saved"
            ) from None
        except (UnicodeError, json.JSONDecodeError):
            raise ValueError(
                "API returned malformed JSON; no snapshot was saved"
            ) from None


def read_totals(client):
    """Read active and archived totals; the upstream devices count excludes archives."""
    payload = client.get("/devices/totals/named")
    if (
        not isinstance(payload, dict)
        or payload.get("success") is not True
        or not isinstance(payload.get("totals"), dict)
    ):
        raise ValueError(
            "Named device totals did not return the expected success contract"
        )
    totals = {key: payload["totals"].get(key) for key in ("devices", "archived")}
    if any(type(value) is not int or value < 0 for value in totals.values()):
        raise ValueError("Named device totals are missing or invalid")
    if not 1 <= sum(totals.values()) <= MAX_ROWS:
        raise ValueError(
            "Named totals are empty or exceed 25,000 devices; coverage was not established"
        )
    return totals


def traverse(client, page_size):
    """Read ordered pages through an empty terminal page, rejecting drift and gaps."""
    rows, pages = [], []
    short_page = False
    while True:
        payload = client.get(f"/devices?limit={page_size}&offset={len(rows)}")
        if (
            not isinstance(payload, dict)
            or payload.get("success") is not True
            or not isinstance(payload.get("devices"), list)
        ):
            raise ValueError("Device page did not return the expected success contract")
        page = payload["devices"]
        pages.append(len(page))
        if len(page) > page_size or len(rows) + len(page) > MAX_ROWS:
            raise ValueError("Device page exceeded collection bounds")
        if not page:
            break
        if short_page:
            raise ValueError(
                "API returned more devices after a short page; possible truncation"
            )
        short_page = len(page) < page_size
        rows.extend(page)
    normalized = ordered_rows(rows)
    if normalized != rows:
        raise ValueError(
            "Device pages are not ordered by devMac; collection may have changed"
        )
    return rows, {"page_rows": pages, "rows": len(rows), "data_sha256": digest(rows)}


def collect_snapshot(client, scope, page_size=1000, clock=utc_time):
    """Cross-check two paginated passes, full export and three bracketing totals."""
    if not isinstance(scope, str) or not scope.strip() or len(scope.strip()) > 240:
        raise ValueError("Provide a scope label of up to 240 characters")
    if type(page_size) is not int or not 1 <= page_size <= 1000:
        raise ValueError("Page size must be between 1 and 1000")
    started = clock()
    totals = [read_totals(client)]
    first, first_pass = traverse(client, page_size)
    export = client.get("/devices/export/json")
    if not isinstance(export, dict) or export.get("success") is False:
        raise ValueError("Full export response is invalid")
    export_rows = ordered_rows(export.get("data"))
    if not isinstance(export.get("columns"), list) or sorted(
        export["columns"]
    ) != sorted(export_rows[0]):
        raise ValueError("Full export columns do not match device evidence")
    totals.append(read_totals(client))
    second, second_pass = traverse(client, page_size)
    totals.append(read_totals(client))
    data_hash = digest(first)
    if data_hash != digest(second) or data_hash != digest(export_rows):
        raise ValueError(
            "Device contents changed or API coverage differs; retry when the inventory is stable"
        )
    archived = sum(row["devIsArchived"] for row in first)
    if any(
        total != {"devices": len(first) - archived, "archived": archived}
        for total in totals
    ):
        raise ValueError("Device totals changed or disagree with the collected rows")
    collection = {
        "schema_version": 1,
        "source_endpoint": client.endpoint,
        "scope": scope.strip(),
        "started_at": started,
        "completed_at": clock(),
        "row_count": len(first),
        "data_sha256": data_hash,
        "coverage": {
            "status": "consistent_api_traversals",
            "method": "two_paginated_passes_and_full_export",
            "page_size": page_size,
            "passes": [first_pass, second_pass],
            "export_rows": len(export_rows),
            "export_sha256": digest(export_rows),
            "totals": totals,
            "atomicity": "not_guaranteed",
            "scan_freshness": "unverified",
        },
    }
    collection["evidence_sha256"] = digest(collection)
    snapshot = {
        "format": "json",
        "data": first,
        "columns": sorted(first[0]),
        "collection": collection,
    }
    return validate_snapshot(snapshot)


def save_new_json(path, value):
    """Save a complete result without overwriting an existing file or exposing partial output."""
    path = path.resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=path.parent, delete=False
        ) as handle:
            temporary = Path(handle.name)
            json.dump(value, handle, indent=2, allow_nan=False)
            handle.write("\n")
            if handle.tell() > MAX_RESPONSE_BYTES:
                raise ValueError("Saved JSON would exceed 64 MiB; no output was saved")
        os.link(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def read_snapshot(path):
    """Read a bounded snapshot file before validating its recorded provenance."""
    with path.open("rb") as handle:
        content = handle.read(MAX_RESPONSE_BYTES + 1)
    if len(content) > MAX_RESPONSE_BYTES:
        raise ValueError("Snapshot file exceeds 64 MiB")
    return validate_snapshot(parse_json(content))


def main():
    """Run explicit collection or local comparison without command-line credentials."""
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    capture = commands.add_parser(
        "collect", help="Read and cross-check a NetAlertX API inventory"
    )
    capture.add_argument(
        "--url", required=True, help="HTTPS API base; HTTP only for loopback"
    )
    capture.add_argument(
        "--scope",
        required=True,
        help="Stable instance/network label; no API filtering is applied",
    )
    capture.add_argument(
        "--token-env", help="Name of an environment variable; otherwise prompt secretly"
    )
    capture.add_argument("--page-size", type=int, default=1000)
    capture.add_argument("--timeout", type=int, default=15)
    capture.add_argument("--output", type=Path, required=True)
    comparison = commands.add_parser(
        "compare", help="Compare two integrity-checked collected snapshots"
    )
    comparison.add_argument("--before", type=Path, required=True)
    comparison.add_argument("--after", type=Path, required=True)
    comparison.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        if args.output.exists():
            raise ValueError("Output already exists; choose a new file")
        if args.command == "collect":
            source_endpoint(args.url)
            if args.token_env:
                if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", args.token_env):
                    raise ValueError(
                        "Provide an environment variable name, not a token"
                    )
                token = os.environ.get(args.token_env, "")
            else:
                if not sys.stdin.isatty():
                    raise ValueError(
                        "The secret prompt requires a terminal; use --token-env for noninteractive collection"
                    )
                with warnings.catch_warnings():
                    warnings.simplefilter("error", getpass.GetPassWarning)
                    try:
                        token = getpass.getpass("NetAlertX API token (hidden): ")
                    except getpass.GetPassWarning:
                        raise ValueError(
                            "Cannot hide token entry in this terminal; use --token-env"
                        ) from None
            value = collect_snapshot(
                APIClient(args.url, token, args.timeout), args.scope, args.page_size
            )
        else:
            value = build_comparison(
                read_snapshot(args.before), read_snapshot(args.after)
            )
        save_new_json(args.output, value)
        print(
            json.dumps(
                {
                    "output": str(args.output),
                    "status": "saved",
                    "scan_freshness": "unverified",
                    "atomicity": "not_guaranteed",
                }
            )
        )
    except (ValueError, TypeError, OSError, EOFError) as error:
        parser.exit(2, f"Cannot {args.command}: {error}\n")


if __name__ == "__main__":
    main()
