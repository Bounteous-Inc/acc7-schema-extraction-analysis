
# Deliverable Template — Migration Test Insights

Use this structure for `analysis/migration-test-insights.md`. Fill every bracket
from the checkpointed extraction files. Keep each verification point **specific and
checkable** — a tester should be able to mark it pass/fail without guessing.

The single most important rule: **separate machine-verified inventory from
manual-inspection-required items.** Every line must make its confidence level obvious.

---

```markdown
# ACCv7 → ACCv8 Migration — Test Verification Points

**Instance:** [name] · **Version/Build:** [version] / [build]
**Extraction date:** [ISO date] · **Scope:** [N] custom schemas, [M] built-in config schemas
**Prepared for:** Testing / QA team

## How to read this document
- ✅ **Verified inventory** — read directly from the v7 instance; check these against v8.
- 🔍 **Manual inspection required** — the audit could NOT read this (memo/XML content:
  delivery content & scheduling, workflow activities, XML-stored fields). A person must
  open the v7 console and compare against v8 by hand.

---

## 1. Scope & exclusions
- Custom schemas in scope: [list]
- Built-in config schemas included: [list]
- **Dead/empty objects excluded** (0 rows in v7 — confirm intentional):
  - [schema] — 0 rows — [decision: drop / migrate empty?]

## 2. Schema & field parity  ✅
For each custom schema, verify it exists in v8 with matching structure.

| Schema | Fields (v7) | Keys | Indexes | Enums | v8 present? | Fields match? | Notes |
|---|---|---|---|---|---|---|---|
| [ns:name] | [count] | [count] | [count] | [count] | ☐ | ☐ | [xml-field flags] |

Per-schema detail (fields with `"xml": true` flagged as 🔍 not machine-verifiable):
- **[ns:name]** — fields: [key fields + types]; enumerations to preserve: [list].

## 3. Dependencies (links)  ✅
Each link must resolve in v8 and joined records must still associate.

| Source schema | → Target | Join | Type | Verify |
|---|---|---|---|---|
| [ns:name] | [target] | [src→dst] | custom→custom / custom→builtin | ☐ |

Migrate-together groups (connected custom schemas): [groups].
Orphans (verify still needed): [list].

## 4. Record-count reconciliation  ✅
Fill the v8 column after migration; counts should match unless intentionally excluded.

| Schema | v7 count | v8 count | Match? |
|---|---|---|---|
| [ns:name] | [n] | | ☐ |

## 5. Operators & folders  ✅
- Operators present in v8: ☐  (v7 list: [logins])
- Folder tree / access model reproduced: ☐

## 6. Deliveries  🔍 (metadata only — content NOT machine-verified)
Metadata below was read from SQL columns. **Content, scheduling, and typology filter live
in a memo column the audit cannot read — inspect each manually in the v7 console.**

| Delivery (internalName) | Label | Status | Msg type | Present in v8? | Content verified manually? |
|---|---|---|---|---|---|
| [name] | [label] | [status] | [type] | ☐ | 🔍 ☐ |

## 7. Workflows  🔍 (metadata only — activities NOT machine-verified)
Same limitation: workflow **activities/steps live in a memo column the audit cannot read**.

| Workflow (internalName) | Label | State | Present in v8? | Logic verified manually? |
|---|---|---|---|---|
| [name] | [label] | [state] | ☐ | 🔍 ☐ |

## 8. Manual-inspection checklist  🔍
Consolidated list of everything requiring human review in the v7 console:
- Delivery content/scheduling/typology for: [deliveries]
- Workflow activities for: [workflows]
- XML/memo-stored fields on: [schemas + fields]

## 9. Drift-detection baseline (optional)
Schema `md5` values captured at extraction — re-run post-migration to spot changes.
| Schema | v7 md5 |
|---|---|
| [ns:name] | [md5] |
```

---

## Notes for whoever fills this in

- Pull counts from `extraction/counts.json`, structure from `definitions/`, links
  from `analysis/dependency-graph.json`, delivery/workflow metadata from `records/`.
- Never invent a value you didn't checkpoint. If extraction did not capture it, mark it 🔍 or
  note the gap — do not guess.
- Prefer many small checkable rows over prose paragraphs; testers work from checklists.
