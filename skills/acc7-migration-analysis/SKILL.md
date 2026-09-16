
---
name: acc7-migration-analysis
description: >
  Turn an extracted ACCv7 workspace into a testing-team verification document for a v7 → v8
  migration. Use this skill whenever the user wants to analyze, summarize, or produce
  "migration test points", a "QA checklist", "things to verify after migration",
  "verification insights", or a "dependency map" from an already-extracted ACCv7 workspace
  (the output of the `acc7-migration-extraction` skill). Trigger whenever an
  `acc7-migration-audit-*` workspace with an `extraction/` folder exists and the user wants
  the testing deliverable — even if they do not say the word "skill". This skill reads files
  ONLY — it makes no MCP calls. Run `acc7-migration-extraction` first to produce the
  workspace this consumes.
---
# ACCv7 Migration Analysis

Read an extracted ACCv7 workspace (produced by the `acc7-migration-extraction` skill) and
turn it into a **testing-team verification document** for the v7 → v8 migration.

This skill makes **no MCP calls**. It reads only the files under the workspace's
`extraction/` tree. If something needed is missing, that is an extraction gap — note it in
the deliverable and continue; do not try to fetch it live.

## Before you start

Find the workspace root (default naming: `./acc7-migration-audit-<instance>/`). Confirm it
contains an `extraction/` folder and a `00_manifest.json`. If the manifest `stage` is not
yet `analysis`, extraction may be incomplete — tell the user and ask whether to proceed
with what exists or finish extraction first.

Read `references/workspace-contract.md` to understand the exact layout and manifest format
you are consuming. Read `references/deliverable-template.md` before writing output — it
defines the required structure of the deliverable.

## What you produce

Inside the same workspace root, create the `analysis/` directory with:

- `analysis/dependency-graph.json` — derived dependency edges.
- `analysis/migration-test-insights.md` — **the deliverable** for the testing team.

---

## Analysis procedure (read files only, no MCP calls)

### Step 1 — Load and sanity-check the extraction

Read `00_manifest.json`, `extraction/connection.json`, `extraction/schemas_index.json`, and
`extraction/counts.json`. Note the instance version/build and extraction date for the
deliverable header. Scan the manifest `notes` for recorded gaps (e.g. sampled tables, the
delivery/workflow memo gap) — these must surface in the deliverable.

**Check record completeness.** Records live as `extraction/records/<ns>__<name>.jsonl`
(one record per line) with a companion `<ns>__<name>.meta.json`. For each, confirm
`complete: true` (i.e. `rows_written == expected_count`). If any shows `complete: false` or
`sampled: true`, that schema's data is partial — flag it prominently in the deliverable as
"incomplete extraction — counts below may understate" rather than treating it as verified.
Read `.jsonl` files line-by-line; do not assume they are a single JSON array.

### Step 2 — Build the dependency graph

From every definition in `extraction/definitions/`, read its `links` and build
`analysis/dependency-graph.json`: for each schema, the schemas it points to (via `target`)
and the join fields. Flag:

- **Custom → custom** links (internal dependencies that must migrate together).
- **Custom → built-in** links (e.g. a custom table extending `nms:recipient`).
- **Orphans** (custom schemas nothing links to, and that link to nothing) — verify they are
  still needed.

### Step 3 — Derive test-verification points

Turn the checkpointed facts into concrete, checkable points for the testing team. Organize
by object type. Be specific — not vague advice. The *kinds* of point to produce:

- **Schemas / fields:** every custom schema exists in v8 with the same fields, types,
  lengths, required flags; `md5`/field-count parity; enumerations preserved value-for-value.
- **Keys / indexes:** identity and uniqueness constraints reproduced; no duplicate-key
  regressions.
- **Dependencies (links):** each link resolves in v8; joined records still associate;
  cascade behavior unchanged.
- **Record counts:** per-schema v7 vs v8 counts match (from `counts.json`); dead tables
  intentionally excluded, not silently lost.
- **Operators / folders:** access model and folder tree reproduced.
- **Deliveries / workflows (metadata only):** each expected delivery/workflow is present by
  name and state — **and** a manual-inspection line for content/scheduling/activities,
  because the server could not read them.
- **XML/memo fields:** explicitly listed as not machine-verified; manual check required.

### Step 4 — Write the deliverable

Write `analysis/migration-test-insights.md` using the structure in
`references/deliverable-template.md`. It must:

- Lead with scope, instance version/build, and the extraction date.
- Group verification points by object type, each as a checkable item.
- Clearly separate **machine-verified inventory** from **manual-inspection-required** items
  (deliveries, workflows, memo/XML fields), so the testing team knows the confidence level
  of each line.
- Include the record-count reconciliation table (v7 counts now; v8 column blank to fill).
- List dead/empty objects as explicit "confirm excluded" decisions.

### Step 5 — Present

Save the deliverable and (if a file-presentation tool is available) present it to the user.
Summarize: how many verification points, broken down by machine-verified vs
manual-inspection-required, and call out the delivery/workflow gap once more so it is not a
surprise.

---

## Guardrails

- **No MCP calls.** This skill only reads the extraction workspace. If data is missing, note
  it as a gap — never fetch live.
- **Honest confidence.** Never present delivery/workflow internals or memo/XML fields as
  verified. If extraction couldn't read it, say so explicitly in the deliverable.
- **Traceability.** Every value in the deliverable should trace to a file under
  `extraction/`. Do not invent counts, fields, or names that were not captured.
- **Testers work from checklists.** Prefer many small checkable rows over prose paragraphs.
