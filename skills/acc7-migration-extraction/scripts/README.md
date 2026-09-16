
# extract_records.py — MCP-client extraction

Pulls an ACCv7 instance into a workspace on disk by calling **your MCP server's tools**
(not by bypassing it). It connects as an MCP client, calls the tools by name, and streams
each result to files. The bulk data lands in this script's memory and goes to disk
line-by-line — it never passes through an AI model's context.

## Why a script and not just the AI model

When an AI model calls an MCP tool, the result returns **into the model's context** — that
is how the protocol works. For huge tables that overflows the context and forces truncation
or summarizing, i.e. lost data. A program has no such limit: it receives the same tool
result and streams it straight to a file. That is the one and only reason this script
exists. It uses the exact same server and tools the model would; it just isn't bottlenecked
by a context window.

## Install

```bash
pip install "mcp[cli]"     # the official MCP Python SDK
```

## Transport — chosen at runtime from MCP_TRANSPORT

The script matches whatever transport your server runs:

```bash
# HTTP: connect to the running server URL; instance details go as X-ACC-* headers
export MCP_TRANSPORT=http
export MCP_HTTP_URL=http://127.0.0.1:8000/mcp

# STDIO: spawn the server as a subprocess; instance details go in its env
export MCP_TRANSPORT=stdio
export MCP_STDIO_CMD=/abs/path/.venv/bin/acc-mcp-server
# export MCP_STDIO_ARGS="--flag value"   # optional
```

Instance credentials (both transports; native operator — an Adobe ID will not work):

```bash
export ACC_BASE_URL=https://your-instance-host
export ACC_LOGIN=your_native_operator
export ACC_PASSWORD=your_password
# optional: ACC_MAX_RETRIES=3
```

## Operations (run in this order)

```bash
WS=./acc7-migration-audit-<instance>

python extract_records.py connection --out "$WS"
python extract_records.py schemas    --out "$WS" --no-builtin     # custom = migration scope
python extract_records.py count      --out "$WS" --schema cus:order
python extract_records.py definition --out "$WS" --schema cus:order            # form=inventory
python extract_records.py records    --out "$WS" --schema cus:order \
    --fields @internalName,@label
python extract_records.py records    --out "$WS" --schema cus:order --resume   # after interruption
python extract_records.py entity     --out "$WS" \
    --entity-key "xtk:javascript|nms:aaexception.js"
```

Maps operation → tool: connection→`test_connection`, schemas→`list_schemas`,
count→`count_records`, definition→`get_schema_definition`, records→`query_schema`,
entity→`get_entity`.

## Completeness guarantee

Each `records` run writes `records/<ns>__<n>.meta.json`:

```json
{ "schema": "cus:order", "rows_written": 148213, "expected_count": 148213,
  "complete": true, "last_cursor": null, "sampled": false }
```

- Records stream to `records/<ns>__<n>.jsonl`, one row per line, flushed per page.
- Paging follows the tool's own `cursor` / `next_cursor` (keyset — no boundary dupes).
- `complete` is true only when `rows_written == expected_count` (from the `count` step).
- Short run → prints a warning and **exits 3**; rerun with `--resume` (continues from the
  saved `last_cursor`).
- `--sample-max N` is the only way to stop early; it sets `sampled: true`, never silent.

## Exit codes

- `0` success / complete
- `1` MCP or tool error (structured JSON on stderr: `errorType`, `message`)
- `2` bad invocation (missing env var or flag, or SDK not installed)
- `3` records incomplete — rerun with `--resume`

## First-run check

Run `count` then `records` on one small known schema and confirm the printed total equals
the count (meta `complete: true`). That validates transport + tools + streaming end to end;
after it, trust the script on the large tables. This is also where you confirm your server's
`query_schema` / `get_entity` are live (they were marked "planned" in the server doc).
