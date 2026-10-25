# Historical Qualtrics import contract (INV-1110)

If raw historical Qualtrics exports are later supplied, they enter the analysis estate as a
**separate source batch** — never merged into a server-run batch, and never pooled with new
pilot outcomes without identifying generator, manipulation, scripting, timing and
measurement changes (spec §9.2). The N=30 narrative report is **not** a substitute for raw
participant records and cannot satisfy this contract.

Implementation: `backend/app/export/historical.py`. Contract id
`investigators.historical_import/1`.

## Execution status

`import_historical(bundle)` is intentionally a stub: it validates the bundle and returns
`status="not_applicable"` — with the validation report and the computed `import_sha256`
when the bundle is valid. No write path exists until raw inputs are actually supplied and
reviewed; import execution is an explicit operational act, not a side effect of validation.

## Bundle shape

```json
{
  "schema": "investigators.historical_import/1",
  "source_batch": {
    "batch_id": "qualtrics-s3-historical",
    "study": "s3",
    "generator": "qualtrics_raw_export",
    "source_file_sha256": "<sha256 of the raw export file>"
  },
  "fields": {
    "inv_condition": {"available": true},
    "inv_checkin_at": {"available": false}
  },
  "rows": [
    {
      "source_row_id": "<original Qualtrics ResponseId>",
      "fields": {"inv_condition": "eval", "inv_correct_total": 3},
      "row_sha256": "<sha256 of canonical JSON of fields>"
    }
  ]
}
```

## Rules enforced by `validate_bundle`

- `schema` must be exactly `investigators.historical_import/1`.
- `source_batch.batch_id` must match the batch-id format
  (`^[a-z0-9][a-z0-9_.-]{1,63}$`); `study`, `generator` and `source_file_sha256` are
  required so historical rows always identify their generator.
- `fields` is a non-empty map keyed by **original** variable names (`inv_*`); every entry
  carries an `available` flag — the unavailable metadata is preserved, not papered over.
- Each row must carry `source_row_id` (unique within the bundle), its `fields`, and a
  `row_sha256` equal to the canonical JSON checksum — original field values are preserved
  byte-identically under the declared checksum.
- A row may only carry fields declared `available` — absent source data is omitted, never
  invented.
- Server-side products (`seed*`, `event_seq`, `render_seq`, `delivered_seq`,
  `committed_seq`, `item_timestamp*`, `rendered_at`) are rejected outright: a raw Qualtrics
  export cannot assert world seeds, render events or item timestamps, and importing them
  would be fabrication (spec §9.2).

## What the contract does not do

- It does not create `app` rows yet — no table mapping is committed until real inputs are
  inspected (column names, encodings and duplicate handling all need the actual file).
- It does not assign a `compatibility_key` — historical rows have no compatible bundle;
  aggregates key them as `unkeyed:<batch>:<sha256>` at most, so they can never silently
  pool with server sessions (INV-1108 grouping applies).
- It does not fabricate events, seeds or timestamps under any circumstances.
