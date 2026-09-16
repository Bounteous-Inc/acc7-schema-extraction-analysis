
# Workspace Contract (shared between extraction and analysis skills)

This is the interface between `acc7-migration-extraction` (writer) and
`acc7-migration-analysis` (reader). Both skills must agree on this exact layout and manifest
format. Do not deviate — the analysis skill locates files by these paths.

## Workspace root

Default: `./acc7-migration-audit-<instance>/` where `<instance>` is the `name` from
`test_connection`. Whatever root is chosen, both skills use the same one.

## Layout

Create directories lazily — do not scaffold everything upfront.

```
<workspace-root>/
├── 00_manifest.json                   # run state + resumability (see below)
├── scripts/                           # WRITTEN by acc7-migration-extraction
│   └── extract_records.py             # streams SOAP record pages straight to disk
├── extraction/                        # WRITTEN by acc7-migration-extraction
│   ├── connection.json                # test_connection result (instance version/build)
│   ├── schemas_index.json             # list_schemas output (custom + builtin flags, md5)
│   ├── counts.json                    # per-schema row counts
│   ├── definitions/
│   │   └── <ns>__<name>.json          # get_schema_definition (form: inventory) per schema
│   ├── records/
│   │   ├── <ns>__<name>.jsonl         # one record per line (streamed by the script)
│   │   └── <ns>__<name>.meta.json     # rows_written, expected_count, complete, last_cursor
│   └── entities/
│       └── <ns>__<name>__<key>.json   # get_entity results (JS libs, forms, etc.)
└── analysis/                          # WRITTEN by acc7-migration-analysis
    ├── dependency-graph.json          # derived: schema -> [linked schemas] from inventory links
    └── migration-test-insights.md     # THE DELIVERABLE for the testing team
```

Records are **JSON Lines** (`.jsonl`, one record per line) so extraction can append
crash-safely and analysis can stream them without loading a giant array. Each `.jsonl` has a
companion `.meta.json` proving completeness (`rows_written == expected_count`).

**Filename rule:** replace the `:` in a qualified name with `__` (e.g. `nms:recipient` →
`nms__recipient`) so names are filesystem-safe.

**Ownership:** extraction writes `extraction/` and the manifest; analysis writes
`analysis/`. Analysis never writes into `extraction/`; extraction never creates `analysis/`.

## Manifest (`00_manifest.json`)

Extraction writes it at start and updates after every completed unit of work (each schema
counted, each definition fetched, each schema queried), so extraction is resumable. It sets
`stage: "analysis"` when extraction finishes. Analysis reads it to confirm extraction is
complete and to pick up recorded gaps from `notes`.

```json
{
  "instance": "partners",
  "instance_version": "7.4.3",
  "workspace_root": "./acc7-migration-audit-partners",
  "stage": "extraction",
  "started_at": "2026-08-31T10:00:00Z",
  "steps": {
    "test_connection": "done",
    "list_schemas": "done",
    "count_records": { "done": ["cus:loyaltyTier"], "pending": ["cus:store"] },
    "get_schema_definition": { "done": ["cus:loyaltyTier"], "pending": ["cus:store"] },
    "query_schema": { "done": [], "pending": ["xtk:workflow", "nms:delivery"] },
    "get_entity": { "done": [], "pending": [] }
  },
  "notes": []
}
```

- `stage`: `"extraction"` while extraction runs; `"analysis"` once extraction is done and
  the workspace is ready for the analysis skill.
- `steps.*`: either `"done"` or a `{done:[], pending:[]}` progress object.
- `notes`: free-text gap records (empty tables, sampled-not-exhaustive schemas, the
  delivery/workflow memo gap). The analysis skill surfaces these in the deliverable.
