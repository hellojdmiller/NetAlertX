"""Verify device comparison and untrusted export handling."""

import json
import tempfile
import unittest
from pathlib import Path

from inventory_review import compare, normalize_mac, read_devices, snapshot_time
from report_page import render_page


class InventoryReviewTests(unittest.TestCase):
    """Exercise meaningful inventory and evidence boundaries."""

    def test_missing_device_is_not_removal(self):
        """An absent row must remain unverified."""
        self.assertEqual(
            compare([{"devMac": "02:00:00:00:00:01"}], [])[0]["change"], "not_observed"
        )

    def test_normalizes_mac_formats(self):
        """Equivalent MAC formatting must not create a new device."""
        for value in ["0200.0000.00ab", "02-00-00-00-00-ab", "0200000000ab"]:
            self.assertEqual(normalize_mac(value.upper()), "02:00:00:00:00:ab")

    def test_rejects_duplicate_identity(self):
        """Conflicting duplicate rows cannot overwrite one another."""
        with self.assertRaises(ValueError):
            compare([], [{"devMac": "02:00:00:00:00:01"}, {"devMac": "020000000001"}])

    def test_rejects_invalid_mac(self):
        """Malformed identifiers must not become devices."""
        with self.assertRaises(ValueError):
            normalize_mac("not-a-mac")

    def test_missing_owner_is_distinct_from_blank(self):
        """Field coverage is different from an unassigned owner."""
        rows = compare(
            [],
            [
                {"devMac": "02:00:00:00:00:01"},
                {"devMac": "02:00:00:00:00:02", "devOwner": ""},
            ],
        )
        self.assertEqual(
            [row["owner_status"] for row in rows], ["not_exported", "unassigned"]
        )

    def test_missing_field_does_not_fake_change(self):
        """A field missing from a snapshot must remain uncompared."""
        result = compare(
            [{"devMac": "02:00:00:00:00:01", "devOwner": "IT"}],
            [{"devMac": "02:00:00:00:00:01"}],
        )[0]
        self.assertEqual(result["change"], "incomplete_comparison")
        self.assertEqual(result["changes"], [])
        self.assertIn("devOwner", result["uncompared_fields"])

    def test_changed_field_has_both_values(self):
        """Changes preserve baseline and current evidence."""
        old = {"devMac": "02:00:00:00:00:01", "devLastIP": "192.0.2.1"}
        new = dict(old, devLastIP="192.0.2.2")
        result = compare([old], [new])[0]
        self.assertEqual(result["change"], "changed")
        self.assertEqual(
            result["changes"],
            [{"field": "devLastIP", "before": "192.0.2.1", "after": "192.0.2.2"}],
        )

    def test_native_json_and_csv_exports(self):
        """Both native export representations should load."""
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "devices.json"
            path.write_text(
                json.dumps(
                    {"format": "json", "data": [{"devMac": "02:00:00:00:00:01"}]}
                )
            )
            self.assertEqual(len(read_devices(path)), 1)
            path = Path(directory) / "devices.csv"
            path.write_text('devMac,devName\n02:00:00:00:00:01,"Lab, printer"\n')
            self.assertEqual(read_devices(path)[0]["devName"], "Lab, printer")

    def test_empty_export_rejected(self):
        """An empty export must not produce an all-clear report."""
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "empty.json"
            path.write_text('{"data": []}')
            with self.assertRaises(ValueError):
                read_devices(path)

    def test_timezone_required(self):
        """Ambiguous local timestamps are rejected."""
        with self.assertRaises(ValueError):
            snapshot_time("2026-09-18T12:00:00")

    def test_html_values_are_text(self):
        """Untrusted export values cannot become HTML elements."""
        html = render_page(
            "Review",
            "Demo",
            "<img src=x>",
            "Now",
            [],
            ["Value"],
            [["<script>alert(1)</script>"]],
            "Note",
        )
        self.assertNotIn("<img src=x>", html)
        self.assertNotIn("<script>alert(1)</script>", html)
        self.assertIn("&lt;script&gt;", html)


if __name__ == "__main__":
    unittest.main()
