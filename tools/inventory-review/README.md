# Inventory review

Compare two complete NetAlertX device exports and produce a searchable HTML report or structured JSON. Python 3.10+ is the only requirement. The command reads local files and does not connect to a network or modify NetAlertX.

## Try the fictional demo

From the repository root:

```sh
python3 tools/inventory-review/inventory_review.py \
  --before tools/inventory-review/examples/before.json \
  --after tools/inventory-review/examples/after.json \
  --before-at 2026-09-17T12:00:00Z \
  --after-at 2026-09-18T12:00:00Z \
  --scope "Fictional lab" --output /tmp/inventory-review.html
```

Open the generated HTML file in a browser. Use a `.json` output filename for machine-readable results. Search filters rows locally; summary cards always describe the complete comparison.

## Use real exports

Use complete **device exports** from the same NetAlertX instance and scope. Supported formats are the native JSON object containing a `data` array, a raw array of device objects, or native comma-separated CSV with `devMac` headers. GraphQL pages, REST pagination wrappers, SQLite databases, and event exports are not accepted.

The adapter follows [`DeviceInstance.exportDevices`](../../server/models/device_instance.py). Times and scope are supplied by the operator; the tool cannot establish export completeness or freshness itself. Randomized MAC addresses can make an existing physical device look new. Internet root identifiers are accepted but excluded from ownership review.

The report shows:

- New, changed, unchanged, incompletely compared, and not-observed devices.
- Before/after values for name, owner, IP, type, VLAN, site, and location.
- Blank ownership separately from an owner field that was not exported.
- Missing field coverage without interpreting a missing field as an empty value.

**Not observed is not proof of removal or disconnection.** The report includes historical values for missing devices. Archived devices remain in scope if present in the export. Empty exports, duplicate MACs, invalid identifiers, malformed rows, and unordered or timezone-free snapshot times fail visibly with exit code 2. Exit code 0 means a report was generated; it does not mean the network is healthy.

## Verification

```sh
python3 -m unittest discover -s tools/inventory-review -v
```

The first version was checked with fictional JSON/CSV exports, generated HTML/JSON reports, and focused tests. No live NetAlertX deployment or network scan was performed; the complete upstream application test suite was not run.

## Next upgrades

1. A snapshot collector that uses the authenticated API, follows pagination, and records scan scope and collection time.
2. Owner assignments from a separate reviewed registry, with confidence and evidence age shown explicitly.
3. Ticket drafts for new or unowned devices, plus human approval before any external write.
4. A local AI summary that cites device records and treats device names and comments as untrusted text.

This is an experimental extension maintained in JD Miller's fork, not an upstream NetAlertX release. The upstream GPL license and attribution remain in place. Never commit real device exports to a public repository.
