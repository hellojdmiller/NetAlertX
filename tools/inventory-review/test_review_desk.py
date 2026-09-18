"""Test evidence integrity, local API boundaries, and AI summary handling."""

import copy
import http.client
import json
import threading
import unittest
from http.server import ThreadingHTTPServer

from desk_ai import OllamaClient
from desk_data import ai_evidence, prepare_report, validate_ai_summary
from review_desk import MAX_BODY, demo_report, document, handler_for


class DeskEvidenceTests(unittest.TestCase):
    """Check that imported evidence cannot silently acquire stronger meaning."""

    def setUp(self):
        """Create a fresh fictional comparison for every test."""
        self.raw = demo_report()
        self.report = prepare_report(self.raw)

    def test_stable_ids_survive_record_reordering(self):
        """Identity must depend on evidence, not presentation order."""
        reordered = copy.deepcopy(self.raw)
        reordered["devices"].reverse()
        self.assertEqual(prepare_report(reordered), self.report)

    def test_changed_snapshot_changes_evidence_ids(self):
        """Drafts and citations must not bleed across snapshots."""
        revised = copy.deepcopy(self.raw)
        revised["after_at"] = "2026-09-19T12:00:00Z"
        self.assertNotEqual(
            prepare_report(revised)["report_id"], self.report["report_id"]
        )

    def test_missing_device_has_no_current_values(self):
        """Historical values remain clearly historical."""
        record = next(
            item for item in self.report["records"] if item["change"] == "not_observed"
        )
        self.assertTrue(
            all(
                cell["current"] is None and cell["current_state"] == "not_observed"
                for cell in record["cells"]
            )
        )
        self.assertIn("unverified", record["observation"])

    def test_missing_owner_is_not_invented(self):
        """An injected ownership label must not override source evidence."""
        raw = copy.deepcopy(self.raw)
        record = next(
            item for item in raw["devices"] if "devOwner" not in item["device"]
        )
        record["owner_status"] = "assigned"
        prepared = prepare_report(raw)
        match = next(
            item
            for item in prepared["records"]
            if item["device"]["devMac"] == record["device"]["devMac"]
        )
        self.assertEqual(match["owner_status"], "not_exported")

    def test_missing_comparison_field_stays_unknown(self):
        """Baseline evidence missing from the export is not reconstructed."""
        raw = copy.deepcopy(self.raw)
        record = next(item for item in raw["devices"] if item["change"] == "unchanged")
        record["change"] = "incomplete_comparison"
        record["uncompared_fields"] = ["devOwner"]
        result = next(
            item
            for item in prepare_report(raw)["records"]
            if item["device"]["devMac"] == record["device"]["devMac"]
        )
        owner = next(cell for cell in result["cells"] if cell["field"] == "devOwner")
        self.assertEqual(owner["before_state"], "not_comparable")
        self.assertIsNone(owner["before"])

    def test_conflicting_counts_rejected(self):
        """A misleading summary count cannot override source records."""
        self.raw["current_devices"] = 0
        with self.assertRaises(ValueError):
            prepare_report(self.raw)

    def test_duplicate_mac_rejected(self):
        """Duplicate identities cannot overwrite observations."""
        self.raw["devices"].append(self.raw["devices"][0])
        with self.assertRaises(ValueError):
            prepare_report(self.raw)

    def test_conflicting_change_value_rejected(self):
        """The stated after-value must match the device's current value."""
        record = next(
            item for item in self.raw["devices"] if item["change"] == "changed"
        )
        record["changes"][0]["after"] = "invented"
        with self.assertRaises(ValueError):
            prepare_report(self.raw)

    def test_unknown_state_rejected(self):
        """Unsupported states fail visibly rather than becoming success."""
        self.raw["devices"][0]["change"] = "verified_removed"
        with self.assertRaises(ValueError):
            prepare_report(self.raw)

    def test_malformed_state_rejected(self):
        """Nontext state values fail with an actionable validation error."""
        self.raw["devices"][0]["change"] = []
        with self.assertRaises(ValueError):
            prepare_report(self.raw)

    def test_literal_template_markers_preserved(self):
        """Source text must not change when embedded in the page template."""
        self.raw["scope"] = "Literal __MODE__ <!--STYLES-->"
        html = document(self.raw, offline=True)
        self.assertIn("Literal __MODE__ \\u003c!--STYLES-->", html)

    def test_evidence_ids_have_room_for_large_reports(self):
        """Large inventories retain distinct references for navigation and drafts."""
        for record in self.report["records"]:
            self.assertRegex(record["evidence_id"], r"^E-[0-9a-f]{16}$")

    def test_source_script_is_not_executable(self):
        """Embedded reports escape script-closing source strings."""
        self.raw["scope"] = '</script><script>alert("source")</script>'
        html = document(self.raw, offline=True)
        self.assertNotIn(self.raw["scope"], html)
        self.assertIn("\\u003c/script>", html)

    def test_unsupported_version_rejected(self):
        """Unknown schemas do not inherit version-one semantics."""
        self.raw["schema_version"] = 2
        with self.assertRaises(ValueError):
            prepare_report(self.raw)

    def test_valid_ai_draft_preserves_report_reference(self):
        """AI responses remain marked drafts attached to their report."""
        ref = ai_evidence(self.report)["evidence"][0]["evidence_id"]
        result = validate_ai_summary(
            {
                "observations": [
                    {"text": "Review the newly observed device.", "evidence_ids": [ref]}
                ],
                "next_steps": [],
            },
            self.report,
        )
        self.assertEqual(result["kind"], "ai_draft")
        self.assertEqual(result["report_id"], self.report["report_id"])

    def test_ai_cannot_cite_unknown_evidence(self):
        """Fabricated citations must reject the entire draft."""
        with self.assertRaises(ValueError):
            validate_ai_summary(
                {
                    "observations": [
                        {"text": "Claim", "evidence_ids": ["E-fabricated"]}
                    ],
                    "next_steps": [],
                },
                self.report,
            )

    def test_ai_cannot_claim_uncited_observations(self):
        """Every model observation requires at least one supplied citation."""
        with self.assertRaises(ValueError):
            validate_ai_summary(
                {
                    "observations": [{"text": "Claim", "evidence_ids": []}],
                    "next_steps": [],
                },
                self.report,
            )

    def test_ai_extra_action_fields_rejected(self):
        """Model output cannot add executable instructions to the response schema."""
        ref = ai_evidence(self.report)["evidence"][0]["evidence_id"]
        with self.assertRaises(ValueError):
            validate_ai_summary(
                {
                    "observations": [{"text": "Claim", "evidence_ids": [ref]}],
                    "next_steps": [],
                    "tool_calls": [],
                },
                self.report,
            )

    def test_model_context_is_bounded(self):
        """Large reports disclose context truncation and reject omitted citations."""
        report = copy.deepcopy(self.report)
        template = report["records"][0]
        report["records"] = [
            dict(template, evidence_id=f"E-{index:08x}") for index in range(50)
        ]
        context = ai_evidence(report)
        self.assertEqual(context["included_records"], 40)
        self.assertEqual(context["omitted_records"], 10)
        with self.assertRaises(ValueError):
            validate_ai_summary(
                {
                    "observations": [{"text": "Claim", "evidence_ids": ["E-00000031"]}],
                    "next_steps": [],
                },
                report,
            )


class FakeOllama(OllamaClient):
    """A local protocol fixture that never accesses a real model or network."""

    def __init__(self, remote=False, tool_call=False):
        """Configure predictable metadata and response behavior."""
        self.remote = remote
        self.tool_call = tool_call
        self.calls = []

    def request(self, path, payload=None, timeout=3):
        """Return mock protocol responses and capture bounded generation requests."""
        self.calls.append((path, payload))
        if path == "/api/tags":
            return {
                "models": [
                    {"name": "local-demo", "size": 123, "details": {"format": "gguf"}},
                    {"name": "cloud-demo", "size": 123, "details": {"format": "gguf"}},
                    {
                        "name": "remote-demo",
                        "size": 123,
                        "details": {"format": "gguf"},
                        "remote_host": "https://example.invalid",
                    },
                ]
            }
        if path == "/api/show":
            return (
                {"remote_host": "https://example.invalid"}
                if self.remote
                else {"details": {"format": "gguf"}}
            )
        refs = json.loads(payload["messages"][1]["content"])["evidence"]
        summary = {
            "observations": [
                {
                    "text": "Review the cited device.",
                    "evidence_ids": [refs[0]["evidence_id"]],
                }
            ],
            "next_steps": [],
        }
        message = {"content": json.dumps(summary)}
        if self.tool_call:
            message["tool_calls"] = [{"name": "unexpected"}]
        return {"done": True, "message": message}


class ModelProtocolTests(unittest.TestCase):
    """Exercise local-model selection and response validation with fixtures."""

    def test_cloud_and_remote_entries_are_excluded(self):
        """Model discovery excludes cloud-capable entries."""
        self.assertEqual(FakeOllama().models(), ["local-demo"])

    def test_local_summary_has_no_tools(self):
        """The protocol sends evidence and a fixed system prompt, not tools."""
        client = FakeOllama()
        result = client.summarize("local-demo", prepare_report(demo_report()))
        self.assertEqual(result["kind"], "ai_draft")
        chat = client.calls[-1][1]
        self.assertNotIn("tools", chat)
        self.assertFalse(chat["stream"])

    def test_remote_model_metadata_rejected(self):
        """Remote metadata cannot be used even if the list appeared local."""
        with self.assertRaises(ValueError):
            FakeOllama(remote=True).summarize(
                "local-demo", prepare_report(demo_report())
            )

    def test_tool_call_output_rejected(self):
        """Unexpected tool calls never reach the UI."""
        with self.assertRaises(ValueError):
            FakeOllama(tool_call=True).summarize(
                "local-demo", prepare_report(demo_report())
            )


class DeskHTTPTests(unittest.TestCase):
    """Verify actual request handling on a temporary loopback server."""

    @classmethod
    def setUpClass(cls):
        """Start a server using only fictional report and model fixtures."""
        cls.server = ThreadingHTTPServer(
            ("127.0.0.1", 0), handler_for(demo_report(), FakeOllama(), True)
        )
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        """Stop and join the temporary server."""
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=5)

    def request(self, method, path, payload=None, headers=None):
        """Make one bounded HTTP request and return status, headers, and body."""
        connection = http.client.HTTPConnection(
            "127.0.0.1", self.server.server_port, timeout=5
        )
        body = json.dumps(payload) if payload is not None else None
        supplied = {
            "Content-Type": "application/json",
            "Origin": f"http://127.0.0.1:{self.server.server_port}",
        }
        if headers:
            supplied.update(headers)
        connection.request(method, path, body=body, headers=supplied)
        response = connection.getresponse()
        result = response.status, dict(response.getheaders()), response.read()
        connection.close()
        return result

    def test_serves_workspace_and_module(self):
        """The real browser entry points are available with a restrictive policy."""
        status, headers, body = self.request("GET", "/")
        self.assertEqual(status, 200)
        self.assertIn(b"Review the network", body)
        self.assertIn("frame-ancestors 'none'", headers["Content-Security-Policy"])
        self.assertEqual(self.request("GET", "/core.mjs")[0], 200)

    def test_import_and_summary_real_routes(self):
        """Both actual POST routes produce correctly scoped results."""
        status, _, body = self.request(
            "POST", "/api/prepare", {"report": demo_report()}
        )
        self.assertEqual(status, 200)
        prepared = json.loads(body)["report"]
        status, _, body = self.request(
            "POST", "/api/summary", {"report": demo_report(), "model": "local-demo"}
        )
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["report_id"], prepared["report_id"])

    def test_cross_origin_request_rejected(self):
        """Another website cannot submit evidence or consume local inference."""
        self.assertEqual(
            self.request(
                "POST",
                "/api/prepare",
                {"report": demo_report()},
                {"Origin": "https://example.invalid"},
            )[0],
            403,
        )

    def test_unexpected_host_rejected(self):
        """Unexpected hostnames cannot pass the local host boundary."""
        self.assertEqual(
            self.request("GET", "/", headers={"Host": "example.invalid"})[0], 403
        )

    def test_arbitrary_file_path_rejected(self):
        """The server is not a general filesystem browser."""
        self.assertEqual(self.request("GET", "/../../README.md")[0], 404)

    def test_oversized_request_rejected(self):
        """The server refuses oversized bodies before reading them."""
        self.assertEqual(
            self.request(
                "POST", "/api/prepare", {}, {"Content-Length": str(MAX_BODY + 1)}
            )[0],
            413,
        )

    def test_invalid_report_rejected(self):
        """Malformed input returns a usable error instead of replacing a report."""
        self.assertEqual(self.request("POST", "/api/prepare", {"report": []})[0], 400)


if __name__ == "__main__":
    unittest.main()
