# Collect inventory evidence from NetAlertX

The collector reads a NetAlertX instance through its authenticated API and saves a native-compatible JSON device snapshot with collection evidence. It never initiates a scan or changes a device. Python 3.10 or newer is sufficient; there are no additional dependencies.

Use the API base for the instance you administer. This is the service that exposes `/devices`, which may have a different port or path from the web interface. The checked-out implementation is in [`api_server_start.py`](../../server/api_server/api_server_start.py), [`device_instance.py`](../../server/models/device_instance.py), and [`db_helper.py`](../../server/db/db_helper.py).

## Collect two snapshots

Run from this directory. The command prompts for the NetAlertX API token without displaying it:

```sh
python3 snapshot_collect.py collect \
  --url https://netalertx.example.test \
  --scope "Home lab — all device records" \
  --output baseline.snapshot.json
```

Later, run the same command with `--output current.snapshot.json`. Keep the same API base and scope label. The scope is an operator label, not a filter: collection always reads every device record exposed by the API, including archived devices.

For a noninteractive run, arrange for a secret manager or your runtime to supply the token in an environment variable, then pass only its name:

```sh
python3 snapshot_collect.py collect \
  --url https://netalertx.example.test \
  --scope "Home lab — all device records" \
  --token-env NETALERTX_COLLECTOR_TOKEN \
  --output current.snapshot.json
```

Do not put the token into the URL or command arguments. The token is used only in the authorization header and is not included in saved collection evidence or command output. An interactive secret prompt requires a terminal. The collector ignores environment proxies, refuses redirects, verifies TLS certificates, and permits plain HTTP only for loopback addresses such as `http://127.0.0.1:20212`. There is no insecure-TLS override.

`--page-size` can be 1–1000 (default 1000). `--timeout` sets a request's socket timeout in seconds, between 1 and 120 (default 15). Responses and input snapshot files are limited to 64 MiB; inventories must contain 1–25,000 device records. Existing output files are never overwritten. A failed collection produces no snapshot output.

## What the collector checks

1. Read `/devices/totals/named`. Its `devices` field counts active records, **excluding archived records**; `archived` supplies the other records.
2. Traverse `/devices?limit=…&offset=…` in `devMac` order through an explicit empty terminal page. Reject duplicate normalized MACs, inconsistent columns, unsupported archive flags, out-of-order rows, or more records after a short page.
3. Read `/devices/export/json`. This legacy export returns `data` and `columns` without the page endpoint's `success` wrapper. Verify that its full row content matches the paginated collection.
4. Read named totals, repeat the paginated traversal, then read totals again. Both traversals and the independent export must contain exactly the same rows. All three active/archived totals must match the rows.

Any contradictory or incomplete result stops collection. Retry when the inventory is stable. An actively changing instance may fail this conservative check even when its API is working correctly.

These checks establish **collector-recorded API coverage**, not a transactionally atomic database snapshot. Changes between reads that leave the same final content cannot be excluded. Collection time is the collector computer's UTC time, not the time of the network scan; scan freshness remains **unverified**. The API can expose an old database or stale cache consistently. Missing devices still mean absent from these records, not proven removed or offline.

## Compare collected snapshots

```sh
python3 snapshot_collect.py compare \
  --before baseline.snapshot.json \
  --after current.snapshot.json \
  --output collected-review.json

python3 review_desk.py --report collected-review.json
```

The comparison checks snapshot hashes, source identity, scope, row counts and chronological collection windows. The current collection must start after the baseline finishes. It produces the desk's version 1 comparison JSON plus `collection_evidence`. Manual device exports can still use `inventory_review.py`; they do not gain collector-recorded coverage by being imported.

Collection hashes detect accidental changes and bind a comparison to its recorded evidence. They are ordinary, unkeyed SHA-256 hashes: they are **not signatures or proof of origin**. Someone who changes the data can recompute hashes. Keep snapshots in storage you trust, protect access to the files, and verify claims against the instance when making operational decisions.

## File shape and boundaries

Each snapshot contains native `format`, `data` and `columns` properties. The additional `collection` object records:

- Version, canonical source endpoint, operator scope, UTC start/end times and row count.
- A SHA-256 hash of canonical full device rows and a separate metadata integrity hash.
- Per-pass page row counts, terminal empty page, full-export count/hash and three named-total observations.
- The explicit `consistent_api_traversals`, `not_guaranteed` atomicity and `unverified` scan-freshness states.

The comparison's `collection_evidence` contains both collection objects and a hash of the exact comparison body. Changing a comparison while retaining old evidence is rejected; importing the valid original report preserves its binding. This is validation of recorded evidence, not independent observation of a deployment.

Device records can contain infrastructure identifiers, owner names, notes and custom fields. Collection preserves the full native row for integrity checking, so the snapshots should be treated as sensitive operational records. Only supported inventory fields enter the comparison.

## Validation

```sh
python3 -m unittest discover -s . -p test_snapshot_collect.py -v
```

Tests use a local HTTP fixture shaped like the checked-out API, including archived totals, pagination, the legacy export wrapper, changing/truncated responses, redirects, authentication failures, integrity failures, and the real command-line workflow. No live instance, real credential, or actual network scan was used to validate this extension. Live deployment verification remains a separate step.
