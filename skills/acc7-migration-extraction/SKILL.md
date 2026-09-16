
---
name: acc7-migration-extraction
description: >
  Extract the full structure and configuration of an Adobe Campaign Classic v7 (ACCv7)
  instance ahead of a migration to v8, by driving the connected read-only ACCv7 MCP server
  and checkpointing everything to disk. Use this skill whenever the user wants to pull,
  inventory, snapshot, or extract schemas/config from an ACCv7 instance; asks to "capture
  the v7 instance", "pull the schemas", "snapshot before migration", "inventory the
  instance", or to gather the raw material an audit/testing analysis will later use. Trigger
  whenever an ACCv7 MCP server is connected and the goal is to read structure/config out of
  it — even if the user does not say the word "skill". This skill ONLY extracts and writes
  files; the separate `acc7-migration-analysis` skill turns that output into the testing
  deliverable. Run this one first.
---
# ACCv7 Migration Extraction

Drive the connected **ACCv7 MCP server** to inventory a live v7 instance and checkpoint
everything to a workspace on disk. This skill does **not** analyze — it only extracts. A
separate skill, `acc7-migration-analysis`, consumes the workspace this produces and writes
the testing-team deliverable.

The split is deliberate: a full instance is far more data than fits in one context window,
and extraction (live MCP calls) and analysis (pure file reading) are cleanly separable
jobs that may run in different sessions. Everything here is checkpointed so a dying run is
resumable.

## Before you start

Confirm the ACCv7 MCP server is connected by calling **`test_connection`**. If it errors,
stop and report the `errorType`/`message` — do not proceed. Everything depends on a working
session.

Decide the **workspace root** with the user (default: `./acc7-migration-audit-<instance>/`
where `<instance>` is the `name` from `test_connection`). This same root is what the
analysis skill will later read, so tell the user the exact path you chose.

Read `references/tool-contracts.md` for the exact input/output shape of every tool, the
error contract, and the memo-column limitation. Read `references/workspace-contract.md` for
the exact workspace layout and manifest format — the analysis skill depends on this
contract, so follow it precisely. Read `references/extraction-script.md` before extracting —
**all** ACC calls go through a **script that streams straight to disk**, never by hand-copying
tool results through the conversation.

## What you produce

The workspace defined in `references/workspace-contract.md`, specifically the `extraction/`
tree and a `00_manifest.json` with `stage` advanced to `analysis` when you finish. **Every**
ACC call is performed by the script at `scripts/extract_records.py`, which writes its output
into the workspace — structural results to their `extraction/*.json` files and record sets to
`extraction/records/*.jsonl` with a companion `*.meta.json` proving completeness (see
`references/extraction-script.md`). You do **not** create the `analysis/` directory — that
belongs to the next skill.

---

## Extraction procedure (all calls go through the script, no analysis)

**Everything is extracted by a script that calls ACC directly and streams each response to
disk — uniformly, whatever the size.** Claude never pipes tool results through the
conversation into files. This one data path makes every step crash-safe, resumable, and
verifiable the same way. Read `references/extraction-script.md` for the script contract before
you begin.

First, **install the extraction script**. A proven implementation ships with this skill at
`scripts/extract_records.py` — an **MCP client** (official MCP Python SDK) that calls the
server's tools, pages via their `cursor`/`next_cursor`, streams to JSONL, and writes
completeness sidecars. Copy it into the workspace at `scripts/extract_records.py` rather than
writing one from scratch, and install the SDK once with `pip install "mcp[cli]"`. Its
companion `scripts/README.md` documents every operation, the `MCP_TRANSPORT` http/stdio
selection, and required env vars. The script reaches the server the same way this project
does (transport from `MCP_TRANSPORT`; native-operator credentials via `ACC_*`).

Then run it in the order below. Write `00_manifest.json` at the start and update it after
**every** completed unit of work. On a fresh run, read the manifest first and skip anything
already marked done — this is what makes extraction resumable.

Follow the tool order from the server's own guidance:
`test_connection` → `list_schemas` → `count_records` → `get_schema_definition` →
`query_schema` / `get_entity`.

### Step 1 — connection (`test_connection`)

Run the script's connection call. It writes the full result to `extraction/connection.json`.
Read back `instance.version`, `instance.build`, and `name` into the manifest. If it reports
an error, stop.

### Step 2 — schema list (`list_schemas`) — define the migration scope

The migration scope is **what the client built**, not Adobe's defaults. Run the script's
schema-list call twice — once with `include_builtin: false` (custom schemas, the priority)
and once with `include_builtin: true` (you need a few built-in config schemas:
`xtk:workflow`, `nms:delivery`, `xtk:folder`, `nms:operator`). The script writes the merged
result to `extraction/schemas_index.json`, tagging each row `is_custom: true|false` and
**capturing the `md5` of every schema** (free drift detection for a later before/after
comparison).

Build the working set:

- **All custom (`is_custom: true`) schemas** — full audit targets.
- **A fixed set of built-in config schemas** carrying migration-relevant configuration:
  `xtk:workflow`, `nms:delivery`, `nms:operator`, `xtk:folder`, `nms:typology`,
  `nms:deliveryTemplate`, plus any the user names. Do **not** pull built-in recipient/data
  or tracking-log tables — those are data, not migration structure.

### Step 3 — counts (`count_records`) — drop dead objects

Run the script's count call for every schema in the working set; it writes all counts to
`extraction/counts.json`. A custom schema with **0 rows** is a dead object — record it in the
manifest `notes` as a migration decision point ("empty custom table — confirm intentional /
candidate to drop") and skip it in the record step (still keep its definition). Counts also
become the `expected_count` used to verify record extraction in Step 7.

### Step 4 — definitions (`get_schema_definition`) — structure + dependency edges

Run the script's definition call (default `form: "inventory"`) for every schema in the
working set; it writes each to `extraction/definitions/<ns>__<name>.json`.

Capture in full (the analysis skill relies on these being present):

- **`fields`** — especially any marked `"xml": true`. Those live in the memo column and
  **cannot** be read by `query_schema`. Keep the flag.
- **`links`** — the **dependency edges**. Keep `name`, `target`, and `joins`.
- **`keys`** and **`indexes`** — identity/uniqueness for post-migration reasoning.
- **`enumerations`** — value lists that must survive migration intact.

Only use `form: "compiled"` or `"source"` if the user explicitly needs raw XML; those can
exceed the response budget and truncate (check the `truncated` flag before parsing).

### Step 5 — records (`query_schema`) — streamed to disk

Run the script's record call for every in-scope schema that has rows. It pages with the
**keyset cursor** until `next_cursor` is absent and **appends each record as a line** to
`extraction/records/<ns>__<name>.jsonl` as it arrives — never buffering the whole table —
plus a `.meta.json` recording `rows_written`, `expected_count` (from Step 3), `complete`,
and `last_cursor`.

- **Select SQL columns only** (inventory fields without `"xml": true`). Suggested selects:
  - `xtk:workflow` → `["@internalName", "@label", "@state"]` (optionally `where @state = 11`).
  - `nms:delivery` → `["@internalName", "@label", "@status", "@messageType"]`.
  - `nms:operator` → `["@login", "@label"]`.
  - `xtk:folder` → `["@name", "@label", "@model"]`.
  - Custom schemas with rows → their SQL fields (full set for the audit).
- **Default to full extraction** for custom tables — that is the whole point. Sampling a huge
  table is allowed only as an explicit, user-agreed decision, recorded with `sampled: true`
  and a manifest note. Never sample silently.

### Step 6 — `get_entity` (memo-backed source, where reachable)

For schemas whose real content lives in the memo blob **and that are addressable by entity
key** — e.g. `xtk:javascript` (JS libraries), `xtk:form`, `xtk:jssp` — run the script's
entity call to fetch full source. Key format: schemas with a `namespace` field use
`namespace:name` (e.g. `xtk:javascript|nms:aaexception.js`); others use a bare name. The
script writes each to `extraction/entities/`.

### Step 7 — Verify completeness (this is the safeguard against missing data)

Before declaring extraction done, reconcile what the script wrote against what the instance
reported. For every schema in `extraction/records/`, compare its `.meta.json`
`rows_written` to `expected_count` (the `count_records` value):

- **Match** → mark the schema `done` in the manifest.
- **Short** → do **not** mark done. Record the gap in manifest `notes` and rerun the script
  from `last_cursor` (keyset paging makes resume safe). Repeat until it matches.
- **Over** → a likely offset-boundary duplicate; ensure keyset paging and de-duplicate on
  `@id`.

Extraction is finished only when every in-scope schema shows `complete: true` (or an
explicit, noted `sampled: true`). This reconciliation is what turns "we wrote some files"
into "we provably captured every row."

### ⚠️ The memo gap — deliveries and workflows (record this in the manifest)

`nms:delivery` and `xtk:workflow` store their real configuration — delivery
`content`/`scheduling`/`typologyFilter`, and workflow activities — in an XML memo column
that **this server cannot reach**: `get_entity` fails on them (ACC's key resolver needs
`@name`, which neither has) and `query_schema` only sees SQL columns.

So extraction captures delivery/workflow **metadata** (names, labels, state, status,
message type) but **not** their content or step logic. This is a hard server limitation,
not a mistake. Add a manifest `notes` entry recording that delivery/workflow internals were
not extracted, so the analysis skill flags them for manual inspection.

### End of extraction

Set `stage: "analysis"` in `00_manifest.json`. Give the user a short summary: instance
version, # custom schemas, # dead (empty) tables, # config schemas queried, and a clear
note about the delivery/workflow memo gap. Then tell them to run the
**`acc7-migration-analysis`** skill against this same workspace root to produce the testing
deliverable.

---

## Guardrails

- **Read-only.** This server has no write path; never imply otherwise.
- **Structure over data.** Audit configuration and structure. Do not bulk-extract recipient
  PII or tracking logs — they are not migration structure and inflate the workspace.
- **No analysis here.** Do not derive insights or write the deliverable — that is the
  analysis skill's job. Just extract faithfully and record gaps in the manifest.
- **Checkpoint constantly.** Update the manifest after each unit so any run is resumable.
- **Errors are data.** On `isError`, read `errorType`: retry only `SessionExpiredError`
  (now) and `TransportError` (after `retryAfterSeconds`); everything else means fix the
  request or record the gap and move on.
