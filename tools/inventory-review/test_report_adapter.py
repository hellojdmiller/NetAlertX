"""Exercise the shared desk boundary for inventory and security comparisons."""

import copy
import json
import unittest

from adapters.prowler_review_packet import prepare_packet
from desk_data import ai_evidence, validate_ai_summary
from report_adapter import canonical_source, prepare_report
from review_desk import ROOT, demo_report, document


class ReportAdapterTests(unittest.TestCase):
    """Keep security semantics and evidence identity intact across desk operations."""

    def setUp(self):
        """Load fictional source evidence with every common review category."""
        self.raw = json.loads((ROOT / "examples" / "security-delta.json").read_text())
        self.report = prepare_report(self.raw)

    def test_collected_download_retains_integrity_and_identity(self):
        """A portable collected report keeps its exact source binding."""
        raw = json.loads((ROOT / "examples" / "collected-review.json").read_text())
        prepared = prepare_report(raw)
        self.assertEqual(canonical_source(prepared), raw)
        self.assertEqual(prepare_report(canonical_source(prepared)), prepared)
        manual = {
            key: value for key, value in raw.items() if key != "collection_evidence"
        }
        self.assertNotEqual(prepare_report(manual)["report_id"], prepared["report_id"])

    def test_copied_collection_evidence_rejected(self):
        """An edited report cannot inherit another collection's coverage claim."""
        raw = json.loads((ROOT / "examples" / "collected-review.json").read_text())
        raw["devices"][0]["device"]["devName"] = "Changed outside collection"
        with self.assertRaises(ValueError):
            prepare_report(raw)

    def test_source_ids_match_prowler_packet(self):
        """A citation has the same meaning in either application's review output."""
        packet = prepare_packet(self.raw)
        self.assertEqual(packet["report_id"], self.report["report_id"])
        self.assertEqual(
            [r["evidence_id"] for r in packet["records"]],
            [r["evidence_id"] for r in self.report["records"]],
        )

    def test_download_reimport_retains_identity(self):
        """Source downloads must return to the same saved draft workspace."""
        self.assertEqual(prepare_report(canonical_source(self.report)), self.report)

    def test_inventory_download_reimport_retains_identity(self):
        """The second report adapter cannot break the original inventory workflow."""
        inventory = prepare_report(demo_report())
        self.assertEqual(prepare_report(canonical_source(inventory)), inventory)

    def test_ambiguous_report_rejected(self):
        """A report cannot make the source type depend on parser precedence."""
        self.raw["devices"] = demo_report()["devices"]
        with self.assertRaises(ValueError):
            prepare_report(self.raw)

    def test_claimed_fix_without_pass_rejected(self):
        """A FAIL cannot become fixed because its label was edited."""
        self.raw["findings"][0]["change"] = "verified_fix"
        with self.assertRaises(ValueError):
            prepare_report(self.raw)

    def test_missing_finding_has_no_current_values(self):
        """The last known source finding remains historical evidence."""
        missing = next(
            r for r in self.report["records"] if r["change"] == "not_observed"
        )
        self.assertTrue(
            all(
                c["current"] is None and c["current_state"] == "not_observed"
                for c in missing["cells"]
            )
        )
        self.assertIn("Resolution is unverified", missing["observation"])

    def test_historical_severity_not_reconstructed(self):
        """A current finding's severity and mute flag do not describe its baseline."""
        record = next(r for r in self.report["records"] if r["change"] == "regression")
        severity = next(c for c in record["cells"] if c["field"] == "SEVERITY")
        self.assertIsNone(severity["before"])
        self.assertEqual(severity["before_state"], "not_exported")
        self.assertEqual(severity["current"], "critical")

    def test_inconclusive_status_remains_literal(self):
        """Manual and error results are neither pass nor remediation."""
        record = next(
            r for r in self.report["records"] if r["change"] == "inconclusive"
        )
        self.assertTrue(record["attention"])
        self.assertEqual(record["current_status"], "MANUAL")
        self.assertEqual(record["cells"][0]["current"], "MANUAL")

    def test_model_receives_security_evidence_ids(self):
        """The common summary contract references the active security report only."""
        evidence = ai_evidence(self.report)
        ref = evidence["evidence"][0]["evidence_id"]
        value = {
            "observations": [{"text": "Review this finding.", "evidence_ids": [ref]}],
            "next_steps": [],
        }
        result = validate_ai_summary(value, self.report)
        self.assertEqual(result["report_id"], self.report["report_id"])
        inventory_ref = ai_evidence(prepare_report(demo_report()))["evidence"][0][
            "evidence_id"
        ]
        value["observations"][0]["evidence_ids"] = [inventory_ref]
        with self.assertRaises(ValueError):
            validate_ai_summary(value, self.report)

    def test_security_source_does_not_become_html(self):
        """Hostile export fields remain data inside the portable artifact."""
        raw = copy.deepcopy(self.raw)
        raw["scope"] = "</script><img src=x onerror=alert(1)>"
        html = document(raw, offline=True)
        self.assertNotIn(raw["scope"], html)
        self.assertIn("\\u003c/script>", html)
        self.assertNotIn("from './report_ui.mjs'", html)
        self.assertNotIn("from './core.mjs'", html)


if __name__ == "__main__":
    unittest.main()
