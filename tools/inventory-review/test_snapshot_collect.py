"""Exercise collector coverage and credential boundaries with the upstream HTTP contract."""

import copy
import getpass
import io
import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
from contextlib import redirect_stderr
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

from snapshot_collect import (
    APIClient,
    collect_snapshot,
    main,
    parse_json,
    save_new_json,
)
from snapshot_evidence import (
    build_comparison,
    digest,
    source_endpoint,
    validate_comparison_evidence,
    validate_snapshot,
)


def device(number, archived=0):
    """Build one native database-shaped fictional device row."""
    return {
        "devMac": f"02:00:00:00:00:{number:02x}",
        "devName": f"Lab device {number}",
        "devOwner": "Lab IT",
        "devLastIP": f"192.0.2.{number}",
        "devIsArchived": archived,
        "devType": "Laptop",
        "devVlan": "20",
        "devSite": "Fictional lab",
        "devLocation": "Bench",
    }


class FixtureServer(ThreadingHTTPServer):
    """Serve the checked-out API's distinct page, export and named-total shapes."""

    def __init__(self):
        """Initialize isolated HTTP state containing only fictional device values."""
        super().__init__(("127.0.0.1", 0), FixtureHandler)
        self.rows = [device(1), device(2), device(3, archived=1)]
        self.token = "fixture-secret-not-a-real-token"
        self.requests = []
        self.mode = "normal"
        self.pass_number = 0
        self.total_number = 0


class FixtureHandler(BaseHTTPRequestHandler):
    """Implement only GET routes without logging fixture authorization headers."""

    def log_message(self, *args):
        """Silence local HTTP access logs during tests."""

    def do_GET(self):
        """Return fixture data or intentionally broken variants for negative tests."""
        server = self.server
        server.requests.append(
            (self.command, self.path, self.headers.get("Authorization"))
        )
        if self.headers.get("Authorization") != "Bearer " + server.token:
            self.send_error(401)
            return
        if server.mode == "redirect":
            self.send_response(302)
            self.send_header("Location", "/unexpected-destination")
            self.end_headers()
            return
        parsed = urlsplit(self.path)
        rows = copy.deepcopy(server.rows)
        if parsed.path == "/devices":
            query = parse_qs(parsed.query)
            limit, offset = int(query["limit"][0]), int(query["offset"][0])
            if offset == 0:
                server.pass_number += 1
            if server.mode == "changed" and server.pass_number == 2:
                rows[0]["devName"] = "Renamed during collection"
            if server.mode == "duplicate":
                rows[1]["devMac"] = rows[0]["devMac"]
            if server.mode == "unordered":
                rows.reverse()
            if server.mode == "columns":
                rows[1].pop("devOwner")
            if server.mode == "short":
                limit = 1
            if server.mode == "truncated":
                rows = rows[:2]
            payload = {
                "success": server.mode != "failed",
                "devices": rows[offset : offset + limit],
            }
        elif parsed.path == "/devices/export/json":
            if server.mode == "export_mismatch":
                rows.pop()
            payload = {"data": list(reversed(rows)), "columns": list(server.rows[0])}
        else:
            server.total_number += 1
            active = sum(row["devIsArchived"] == 0 for row in rows)
            if server.mode == "totals" and server.total_number > 1:
                active += 1
            payload = {
                "success": True,
                "totals": {
                    "devices": active,
                    "archived": sum(row["devIsArchived"] == 1 for row in rows),
                    "connected": 1,
                    "favorites": 0,
                    "new": 0,
                    "down": 0,
                },
            }
        body = json.dumps(payload).encode()
        if server.mode == "malformed":
            body = body[:-1]
        self.send_response(200)
        self.send_header(
            "Content-Type", "text/html" if server.mode == "html" else "application/json"
        )
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class CollectorTests(unittest.TestCase):
    """Check real HTTP requests against stable and inconsistent fixture inventories."""

    def setUp(self):
        """Start one isolated fixture listener and a collector client."""
        self.server = FixtureServer()
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.endpoint = f"http://127.0.0.1:{self.server.server_port}"
        self.client = APIClient(self.endpoint, self.server.token)

    def tearDown(self):
        """Close the listener and join its worker after each test."""
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=3)

    def snapshot(self, day=1):
        """Collect a fixture snapshot with deterministic chronological timestamps."""
        timestamps = iter(
            [f"2026-09-{day:02d}T10:00:00Z", f"2026-09-{day:02d}T10:00:01Z"]
        )
        return collect_snapshot(
            self.client, "Fictional lab", page_size=2, clock=lambda: next(timestamps)
        )

    def test_success_includes_archived_rows_and_empty_terminal_pages(self):
        """Verify archived devices are included even though the active total excludes them."""
        result = self.snapshot()
        self.assertEqual(result["collection"]["row_count"], 3)
        coverage = result["collection"]["coverage"]
        self.assertEqual(coverage["passes"][0]["page_rows"], [2, 1, 0])
        self.assertEqual(coverage["totals"], [{"devices": 2, "archived": 1}] * 3)
        self.assertEqual(coverage["scan_freshness"], "unverified")
        self.assertEqual(coverage["atomicity"], "not_guaranteed")
        self.assertEqual(result["data"], self.server.rows)
        self.assertTrue(all(method == "GET" for method, _, _ in self.server.requests))
        self.assertTrue(
            all(
                auth == "Bearer " + self.server.token
                for _, _, auth in self.server.requests
            )
        )
        self.assertNotIn(self.server.token, json.dumps(result))

    def test_full_page_inventory_requires_terminal_empty_page(self):
        """Keep reading after a full final page instead of assuming completeness."""
        self.server.rows.pop()
        result = self.snapshot()
        self.assertEqual(
            result["collection"]["coverage"]["passes"][0]["page_rows"], [2, 0]
        )

    def test_inconsistent_collection_is_rejected(self):
        """Refuse truncation, duplication, reordering, changes and contradictory totals."""
        for mode in (
            "changed",
            "duplicate",
            "unordered",
            "columns",
            "short",
            "truncated",
            "failed",
            "export_mismatch",
            "totals",
        ):
            with self.subTest(mode=mode):
                self.server.mode = mode
                self.server.pass_number = self.server.total_number = 0
                with self.assertRaises(ValueError):
                    self.snapshot()

    def test_invalid_response_is_rejected(self):
        """Reject invalid JSON and browser pages instead of treating them as empty data."""
        for mode in ("html", "malformed"):
            with self.subTest(mode=mode):
                self.server.mode = mode
                with self.assertRaises(ValueError):
                    self.snapshot()

    def test_empty_inventory_does_not_establish_coverage(self):
        """Keep an empty response distinct from a complete nonempty snapshot."""
        self.server.rows.clear()
        with self.assertRaisesRegex(ValueError, "empty"):
            self.snapshot()

    def test_redirect_is_never_followed(self):
        """Prevent bearer-token forwarding even to another path on the same fixture host."""
        self.server.mode = "redirect"
        with self.assertRaisesRegex(ValueError, "HTTP 302"):
            self.snapshot()
        self.assertEqual(len(self.server.requests), 1)

    def test_auth_failure_has_no_credential_in_error(self):
        """Report only the HTTP status when authentication fails."""
        token = "another-fake-secret"
        with self.assertRaisesRegex(ValueError, "HTTP 401") as raised:
            collect_snapshot(APIClient(self.endpoint, token), "Lab")
        self.assertNotIn(token, str(raised.exception))

    def test_environment_proxy_is_ignored(self):
        """Read the fixture directly even with a broken proxy configured."""
        with patch.dict(
            os.environ,
            {
                "http_proxy": "http://127.0.0.1:1",
                "HTTP_PROXY": "http://127.0.0.1:1",
                "no_proxy": "",
                "NO_PROXY": "",
            },
        ):
            client = APIClient(self.endpoint, self.server.token)
            self.assertEqual(
                collect_snapshot(client, "Lab")["collection"]["row_count"], 3
            )

    def test_response_size_is_bounded(self):
        """Reject oversized response bodies without producing a partial result."""
        with patch("snapshot_collect.MAX_RESPONSE_BYTES", 16):
            with self.assertRaisesRegex(ValueError, "64 MiB"):
                self.snapshot()

    def test_only_fixed_read_paths_are_allowed(self):
        """Reject arbitrary API paths before making any request."""
        with self.assertRaises(ValueError):
            self.client.get("/device/02:00:00:00:00:01")
        self.assertEqual(self.server.requests, [])

    def test_compare_binds_report_and_provenance(self):
        """Tie changed device evidence to source snapshots and reject copied metadata."""
        before = self.snapshot(1)
        self.server.rows[0]["devLastIP"] = "192.0.2.100"
        after = self.snapshot(2)
        report = build_comparison(before, after)
        self.assertEqual(report["counts"]["changed"], 1)
        self.assertEqual(
            validate_comparison_evidence(report), report["collection_evidence"]
        )
        report["devices"][0]["changes"][0]["after"] = "192.0.2.200"
        with self.assertRaisesRegex(ValueError, "integrity"):
            validate_comparison_evidence(report)

    def test_snapshot_tampering_is_rejected(self):
        """Detect changed data, collection metadata and schema columns on disk."""
        original = self.snapshot()
        for change in ("data", "collection", "columns"):
            with self.subTest(change=change):
                value = copy.deepcopy(original)
                if change == "data":
                    value["data"][0]["devName"] = "Edited"
                elif change == "collection":
                    value["collection"]["scope"] = "Different lab"
                else:
                    value["columns"].append("invented")
                with self.assertRaises(ValueError):
                    validate_snapshot(value)

    def test_source_scope_and_time_mismatch_is_rejected(self):
        """Do not compare snapshots from different instances, scope labels or overlapping times."""
        before, after = self.snapshot(1), self.snapshot(2)
        for key, value in (
            ("scope", "Other lab"),
            ("source_endpoint", "https://example.test"),
            ("started_at", before["collection"]["completed_at"]),
        ):
            with self.subTest(key=key):
                changed = copy.deepcopy(after)
                changed["collection"][key] = value
                changed["collection"]["evidence_sha256"] = digest(
                    {
                        name: value
                        for name, value in changed["collection"].items()
                        if name != "evidence_sha256"
                    }
                )
                with self.assertRaises(ValueError):
                    build_comparison(before, changed)
        with self.assertRaisesRegex(ValueError, "start after"):
            build_comparison(before, before)

    def test_cli_collect_uses_named_secret_environment(self):
        """Run the real command and ensure only the variable name appears in arguments."""
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "snapshot.json"
            command = [
                sys.executable,
                str(Path(__file__).with_name("snapshot_collect.py")),
                "collect",
                "--url",
                self.endpoint,
                "--scope",
                "Fictional lab",
                "--page-size",
                "2",
                "--token-env",
                "TEST_COLLECTOR_TOKEN",
                "--output",
                str(output),
            ]
            result = subprocess.run(
                command,
                env={**os.environ, "TEST_COLLECTOR_TOKEN": self.server.token},
                capture_output=True,
                text=True,
                timeout=15,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertNotIn(
                self.server.token, result.stdout + result.stderr + output.read_text()
            )
            validate_snapshot(json.loads(output.read_text()))


class EvidenceBoundaryTests(unittest.TestCase):
    """Validate connection, JSON and file boundaries independent of HTTP fixtures."""

    def test_source_url_rules(self):
        """Reject credential URLs and insecure remote transport; normalize safe API bases."""
        self.assertEqual(
            source_endpoint("https://EXAMPLE.test:443/api/"), "https://example.test/api"
        )
        self.assertEqual(source_endpoint("http://[::1]:8773/"), "http://[::1]:8773")
        for endpoint in (
            "http://example.test",
            "https://user:secret@example.test",
            "https://@example.test",
            "https://example.test?token=secret",
            "https://example.test/#secret",
            "file:///tmp/devices",
            "https://example.test/a/../b",
            "https://example.test:0",
        ):
            with self.subTest(endpoint=endpoint):
                with self.assertRaises(ValueError):
                    source_endpoint(endpoint)

    def test_duplicate_fields_and_nonfinite_numbers_are_rejected(self):
        """Keep ambiguous and nonstandard JSON out of saved evidence."""
        for content in ('{"data":[],"data":[1]}', '{"data":NaN}', '{"data":Infinity}'):
            with self.assertRaises(ValueError):
                parse_json(content)

    def test_existing_output_is_never_overwritten(self):
        """Keep an earlier artifact unchanged if its output path is reused."""
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "snapshot.json"
            output.write_text("earlier evidence")
            with self.assertRaises(FileExistsError):
                save_new_json(output, {"data": []})
            self.assertEqual(output.read_text(), "earlier evidence")
            self.assertEqual(list(Path(directory).iterdir()), [output])

    def test_hidden_prompt_refuses_echo_fallback(self):
        """Stop instead of falling back to visible input when a terminal cannot disable echo."""
        with tempfile.TemporaryDirectory() as directory:
            args = [
                "snapshot_collect.py",
                "collect",
                "--url",
                "https://example.test",
                "--scope",
                "Lab",
                "--output",
                str(Path(directory) / "snapshot.json"),
            ]
            with (
                patch.object(sys, "argv", args),
                patch("sys.stdin.isatty", return_value=True),
            ):
                with (
                    patch("getpass.getpass", side_effect=getpass.GetPassWarning),
                    redirect_stderr(io.StringIO()),
                ):
                    with self.assertRaises(SystemExit) as raised:
                        main()
            self.assertEqual(raised.exception.code, 2)
            self.assertEqual(list(Path(directory).iterdir()), [])

    def test_oversized_saved_json_leaves_no_partial_file(self):
        """Keep a result that exceeds the input size limit from becoming an unreadable artifact."""
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "snapshot.json"
            with (
                patch("snapshot_collect.MAX_RESPONSE_BYTES", 16),
                self.assertRaises(ValueError),
            ):
                save_new_json(output, {"data": "fictional data" * 20})
            self.assertEqual(list(Path(directory).iterdir()), [])


if __name__ == "__main__":
    unittest.main()
