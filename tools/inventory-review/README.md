# Network review desk

A local workspace for reviewing NetAlertX inventory changes, opening cited evidence, and preparing editable ticket drafts. Optional Ollama summaries stay marked as drafts and must cite supplied records. This is a standalone extension in JD Miller's fork.

## Open the review desk

From the repository root, with Python 3.10 or newer:

```sh
python3 tools/inventory-review/review_desk.py --demo
```

Open **http://127.0.0.1:8770/**. The fictional demo contains a new device, an owner gap, an IP change, an unobserved device, and an unchanged gateway. Stop the local server with Ctrl+C. No packages are required. Use `--port 8775` if the default port is occupied.

1. Select a review category or search the device queue.
2. Open an evidence reference to see its before/after values.
3. Create a ticket draft, edit the title and body, and save or download Markdown.
4. Open **Ticket drafts** to revisit drafts for the current report.

Drafts are saved in this browser, under this local address, and bound to the report and device evidence. Changing snapshots or scope creates a different report identity. Download drafts for durable storage; clearing browser storage removes saved copies. When storage is unavailable or its limit is reached (500 drafts or approximately 2 MB), new edits remain in this session and the desk asks you to download a copy. Saving a draft does not submit a ticket or approve, execute, or verify a change.

## Bring a comparison report

Generate a `.json` comparison with the command below, then use **Import report**, or start directly with:

```sh
python3 tools/inventory-review/review_desk.py --report /path/to/inventory-review.json
```

The desk accepts version 1 **inventory-review JSON**, not raw device exports. Reports must be smaller than 8 MB and contain at most 25,000 device records. Invalid or conflicting reports fail visibly and leave the displayed report intact. Imports remain in this browser session; reloading returns to the report used to start the server. Saved drafts remain associated with their original report.

## Portable preview

```sh
python3 tools/inventory-review/review_desk.py --demo --export /tmp/network-review-desk.html
```

The resulting single HTML file contains its styles, scripts, and fictional report. It supports search, evidence navigation, draft editing, and downloads without a server. Browser storage for a directly opened file varies by browser, so download drafts you want to keep. Importing another report and connecting Ollama require the local server. Replace `--demo` with `--report /path/to/inventory-review.json` to export your own comparison. The generated file includes that report's device data.

## Optional local AI

With Ollama already running on this computer and a locally stored GGUF model available, select **Ask local AI**, choose a model, and generate a cited summary. The desk connects only to `127.0.0.1:11434`, ignores environment proxies, refuses redirects, and filters out model entries indicating cloud or remote execution. It does not install Ollama or download a model. The integration uses Ollama's documented [model list](https://docs.ollama.com/api/tags) and [chat](https://docs.ollama.com/api/chat) APIs.

Only the first 40 attention records' derived observations and review suggestions, plus scope and snapshot times, are sent to the local service. Included and total record counts are shown; large reports are not fully summarized. Device text is treated as untrusted input. Responses with missing or unknown citations, unsupported fields, or tool calls are rejected. No tools are provided to the model. A valid citation identifies a source record; **it does not prove that the model's claim is accurate**. Check each claim before using a suggestion.

The deterministic evidence summary and ticket drafts work without AI. Generation times out after 45 seconds; a large or cold model may need a retry. A missing service produces an explicit unavailable state.

## Create a comparison

Compare two complete NetAlertX device exports and produce a searchable HTML report or structured JSON. Python 3.10+ is the only requirement. The command reads local files and does not connect to a network or modify NetAlertX.

### Try the fictional comparison

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

### Use real exports

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
node --test tools/inventory-review/desk/core.test.mjs
```

The desk has 41 Python tests (including the original 11 comparison tests) and 8 JavaScript tests. They cover evidence validation, missing data, draft identity, malformed AI output, model filtering, local HTTP boundaries, and hostile source text. Browser checks cover report import and rejection, evidence navigation, edited draft persistence and download, phone layout, the portable preview, and AI summary rendering against a fixed-response test fixture.

No live NetAlertX deployment or network scan was performed. No real Ollama model was run. The complete upstream application test suite was not run; this extension remains on a feature branch. The local server binds to loopback and has no multiuser authentication; it is intended for one operator on a trusted computer, not shared hosting.

## Next upgrades

1. A snapshot collector that uses the authenticated API, follows pagination, and records scan scope and collection time.
2. Owner assignments from a separate reviewed registry, with confidence and evidence age shown explicitly.
3. Evaluation of real local models against an adversarial device-name dataset, including unsupported claims with valid citations.
4. A reviewed ticket-system adapter with explicit destination and submission controls.

This is an experimental extension maintained in JD Miller's fork, not an upstream NetAlertX release. The upstream GPL license and attribution remain in place. Never commit real device exports to a public repository.
