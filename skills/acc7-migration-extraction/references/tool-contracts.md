
# ACCv7 MCP Server — Tool Contracts (reference)

Condensed contract for the connected read-only ACCv7 MCP server. Authentication is
internal (lazy `xtk:session#Logon`, cached in-process); there is **no logon tool** and
credentials never appear in tool arguments. All tools return `isError` (bool). Successful
results carry `"isError": false`.

## Tool order

`test_connection` → `list_schemas` → `count_records` (drop empty tables) →
`get_schema_definition` → `query_schema` / `get_entity`.

---

## test_connection

Verifies reachability + credentials. Makes no query, so failure is unambiguously auth or
connectivity. **Input:** none.
Returns `instance_url`, `login`, `auth_mode`, `session_established`, `logon_latency_ms`,
`instance{version,build,name,status}`, `operator{...}`.

## list_schemas

Discovery entry point; nothing hardcoded.
**Input:** `namespace` (string, optional — filter to one namespace e.g. `cus`),
`include_builtin` (bool, default `true`; reserved namespaces are `xtk,nl,nms,ncm,temp,ncl,crm,xxl`).
Use `include_builtin: false` to get the client-built schemas = migration scope.
Returns `rows[]` of `{schema, namespace, name, label, mappingType, md5}` plus
`returned/total_available/truncated`. **`md5` changes when the schema changes → drift
detection.**

## get_schema_definition

Structure of one schema.
**Input:** `schema` (qualified name, e.g. `nms:recipient`), `form` (enum, default
`inventory`): `inventory | compiled | source | wsdl`.

- `inventory` — compact JSON: fields (type/length/label/sqlname/required), links (with
  join conditions), keys, indexes, enumerations, plus a `counts` block. **Use this.**
- `compiled` — raw effective XML (base + extensions merged). May truncate; check `truncated`.
- `source` — raw authored XML (extension: only added fields + `extendedSchema`).
- `wsdl` — SOAP method signatures only.
  Fields with `"xml": true` live in a memo column → **cannot** be selected by `query_schema`;
  use `get_entity`. **Links = dependency edges for mapping.**

## count_records

Row count without transferring rows. Use to size migration, spot dead objects (0-row custom
table = don't migrate), reconcile before/after. Prefer over counting paged `query_schema`.
**Input:** `schema` (qualified), `where` (XPath-style condition, optional).
Returns `{schema, where, count}`.

## query_schema  (⏳ planned in server doc — confirm live before relying on it)

Reads records from any schema. Config objects (folders, forms, options, operators, typology
rules, deliveries, workflows) and sampling custom tables. **Selects SQL columns only.**
**Input:** `schema`, `fields` (string[] XPath expressions e.g. `["@internalName","@label"]`),
`where` (optional), `order_by` (string[], optional), `page_size` (int, default 200,
server-capped), `cursor` (opaque, from previous `next_cursor`).
Returns `rows[]`, `returned`, `truncated`, `next_cursor`, `paging` (`keyset|offset`).
**Paging is keyset on `@id`** (ACC `startLine` is off-by-one and duplicates a boundary row);
`@id` is auto-added to the select. Loop `cursor` until `next_cursor` is absent.

## get_entity  (⏳ planned in server doc — confirm live before relying on it)

One entity's complete XML including **memo-stored content** (delivery content, JS source,
etc.). **Input:** `entity_key` (`schema|name`), `must_exist` (bool, default `true`).
Key format: schemas with a `namespace` field → `namespace:name`
(`xtk:javascript|nms:aaexception.js`); others → bare name (`xtk:folder|aggregates`).
Returns `entity_key`, `chars`, `truncated`, `xml`.
**Hard limitation:** `nms:delivery` and `xtk:workflow` CANNOT be fetched — ACC's key
resolver matches on `@name` and neither schema has it. Use `query_schema` for their
metadata only.

---

## Error contract

Failures are structured data, not strings:
`{isError:true, isRetryable, errorType, message, retryGuidance, retryAfterSeconds?}`.

| errorType           | Retryable | Meaning / action                                         |
| ------------------- | --------- | -------------------------------------------------------- |
| ConfigurationError  | no        | Bad/missing connection details                           |
| AuthError           | no        | Credentials rejected                                     |
| PermissionError_    | no        | Operator lacks rights on the object                      |
| QueryError          | no        | Malformed field/condition — fix and retry               |
| SchemaNotFoundError | no        | Schema/entity doesn't exist                              |
| EntityKeyError      | no        | Schema not addressable by entity key                     |
| SessionExpiredError | yes (0s)  | Session renewed — retry now                             |
| TransportError      | yes (5s)  | Instance unreachable — retry after`retryAfterSeconds` |
| AccError            | no        | Unclassified fault — read the message                   |

Retry policy for the runbook: retry only `SessionExpiredError` (immediately) and
`TransportError` (after `retryAfterSeconds`). Everything else → fix the request or record
the gap in the manifest and move on.

## Budget / limits (server defaults)

`ACC_MAX_ROWS=500` hard ceiling per call; `ACC_DEFAULT_PAGE_SIZE=200`;
`ACC_MAX_RESPONSE_CHARS=120000` (context guard — large `compiled` XML truncates). Always
prefer `form:inventory` and paged `query_schema` over anything that risks the char cap.

## Memo-column limitation (critical for deliverables)

- Delivery config (`content`/`scheduling`/`typologyFilter`) and workflow activities live in
  an XML memo column that is **unreachable** by this server.
- `query_schema` on `nms:delivery` sees 234 of 581 fields (SQL only).
- Therefore delivery/workflow **internals are never machine-verified** — always mark them
  "manual inspection required in v7 console" in the deliverable.
