#!/usr/bin/env python3
"""
extract_records.py — MCP-client extraction for the ACCv7 migration audit.

This talks to YOUR ACCv7 MCP server (the tools you built) — it does NOT bypass it.
It connects over the same transport the server uses, calls the tools by name, and
STREAMS each tool result straight to disk. The heavy data lands in this script's
memory and goes to files line-by-line; it never flows through an AI model's context.
That is the entire reason this script exists: a program can receive a large tool
result and stream it to a file; a model cannot, because for a model "receiving" a
result and "holding it in context" are the same event.

Transport is chosen at runtime from the MCP_TRANSPORT env var, matching the server:
    MCP_TRANSPORT=http    -> connect to a running server URL (streamable HTTP)
    MCP_TRANSPORT=stdio   -> spawn the server as a subprocess and speak over stdio

One operation per run, matching the extraction skill's steps. Each writes into the
workspace:
    connection  -> extraction/connection.json           (tool: test_connection)
    schemas     -> extraction/schemas_index.json         (tool: list_schemas)
    count       -> updates extraction/counts.json         (tool: count_records)
    definition  -> extraction/definitions/<ns>__<n>.json  (tool: get_schema_definition)
    records     -> extraction/records/<ns>__<n>.jsonl     (tool: query_schema, paged)
                   + <ns>__<n>.meta.json                  (completeness proof)
    entity      -> extraction/entities/<...>.json          (tool: get_entity)

Requires the official MCP Python SDK:
    pip install "mcp[cli]"

Connection / transport env:
    MCP_TRANSPORT        "http" (default) or "stdio"

    # http transport:
    MCP_HTTP_URL         e.g. http://127.0.0.1:8000/mcp        (required for http)
    # Instance details are passed to the server as X-ACC-* headers under http:
    ACC_BASE_URL, ACC_LOGIN, ACC_PASSWORD                       (sent as headers)

    # stdio transport:
    MCP_STDIO_CMD        command to launch the server,
                         e.g. "/abs/path/.venv/bin/acc-mcp-server"  (required for stdio)
    MCP_STDIO_ARGS       optional, space-separated extra args
    # Instance details are passed to the spawned server as env vars under stdio:
    ACC_BASE_URL, ACC_LOGIN, ACC_PASSWORD                       (set in child env)

An Adobe ID cannot use the ACC SOAP API — ACC_LOGIN MUST be a native operator.

Examples:
    export MCP_TRANSPORT=http MCP_HTTP_URL=http://127.0.0.1:8000/mcp
    export ACC_BASE_URL=https://host ACC_LOGIN=mcp_api ACC_PASSWORD=***
    python extract_records.py connection --out ./acc7-migration-audit-partners
    python extract_records.py schemas    --out ./ws --no-builtin
    python extract_records.py count      --out ./ws --schema nms:delivery --where "@status=0"
    python extract_records.py definition --out ./ws --schema cus:order
    python extract_records.py records    --out ./ws --schema cus:order \\
                                         --fields @internalName,@label
    python extract_records.py records    --out ./ws --schema cus:order --resume
    python extract_records.py entity     --out ./ws \\
                                         --entity-key "xtk:javascript|nms:aaexception.js"

Exit codes:
    0 success / complete
    1 MCP or tool error (structured JSON on stderr)
    2 bad invocation (missing env var or flag)
    3 records incomplete — rerun with --resume
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator

# ---- MCP SDK imports (fail with a clear message if not installed) ---------- #
try:
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client
    from mcp.client.streamable_http import streamablehttp_client
except Exception:  # pragma: no cover
    sys.stderr.write(
        "ERROR: the MCP Python SDK is required. Install with:\n"
        '    pip install "mcp[cli]"\n'
    )
    sys.exit(2)


RESERVED_NAMESPACES = {"xtk", "nl", "nms", "ncm", "temp", "ncl", "crm", "xxl"}


# --------------------------------------------------------------------------- #
# Env / config
# --------------------------------------------------------------------------- #

def _require_env(name: str) -> str:
    val = os.environ.get(name)
    if not val:
        sys.stderr.write(f"ERROR: environment variable {name} is required.\n")
        sys.exit(2)
    return val


def _acc_conn_env() -> dict[str, str]:
    """Instance connection details, shared by both transports."""
    return {
        "ACC_BASE_URL": _require_env("ACC_BASE_URL"),
        "ACC_LOGIN": _require_env("ACC_LOGIN"),
        "ACC_PASSWORD": _require_env("ACC_PASSWORD"),
    }


# --------------------------------------------------------------------------- #
# Transport selection — the conditional the user asked for
# --------------------------------------------------------------------------- #

@asynccontextmanager
async def open_session() -> AsyncIterator[ClientSession]:
    """
    Open an MCP ClientSession using the transport named by MCP_TRANSPORT.
    Yields an initialized session ready for call_tool().
    """
    transport = os.environ.get("MCP_TRANSPORT", "http").lower()

    if transport == "stdio":
        cmd = _require_env("MCP_STDIO_CMD")
        args = os.environ.get("MCP_STDIO_ARGS", "").split()
        # Under stdio the server reads instance details from its own env.
        child_env = {**os.environ, **_acc_conn_env(), "MCP_TRANSPORT": "stdio"}
        params = StdioServerParameters(command=cmd, args=args, env=child_env)
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                yield session

    elif transport == "http":
        url = _require_env("MCP_HTTP_URL")
        # Under http the server takes instance details from X-ACC-* request headers.
        conn = _acc_conn_env()
        headers = {
            "X-ACC-Base-Url": conn["ACC_BASE_URL"],
            "X-ACC-Login": conn["ACC_LOGIN"],
            "X-ACC-Password": conn["ACC_PASSWORD"],
        }
        async with streamablehttp_client(url, headers=headers) as (read, write, _):
            async with ClientSession(read, write) as session:
                await session.initialize()
                yield session

    else:
        sys.stderr.write(
            f"ERROR: MCP_TRANSPORT must be 'http' or 'stdio', got '{transport}'.\n"
        )
        sys.exit(2)


# --------------------------------------------------------------------------- #
# Tool-result unwrapping
# --------------------------------------------------------------------------- #

class ToolError(Exception):
    def __init__(self, error_type: str, message: str) -> None:
        super().__init__(message)
        self.error_type = error_type
        self.message = message


def _unwrap(result: Any) -> dict[str, Any]:
    """
    Turn an MCP CallToolResult into a plain dict.

    MCP tools return content blocks; this server returns JSON as text (and may also
    populate structuredContent). We prefer structuredContent, else parse the first
    text block as JSON. The server's own error contract ({isError, errorType, ...})
    is surfaced as a ToolError.
    """
    data: dict[str, Any] | None = None
    structured = getattr(result, "structuredContent", None)
    if isinstance(structured, dict) and structured:
        data = structured
    else:
        for block in getattr(result, "content", []) or []:
            text = getattr(block, "text", None)
            if text:
                try:
                    data = json.loads(text)
                except json.JSONDecodeError:
                    data = {"_raw_text": text}
                break
    if data is None:
        if getattr(result, "isError", False):
            raise ToolError("AccError", "Tool returned an error with no body")
        return {}

    if data.get("isError") is True:
        raise ToolError(data.get("errorType", "AccError"),
                        data.get("message", "Unknown tool error"))
    return data


async def call(session: ClientSession, tool: str,
               args: dict[str, Any]) -> dict[str, Any]:
    """Call a tool with retry on the server's retryable error types."""
    max_retries = int(os.environ.get("ACC_MAX_RETRIES", "3"))
    attempt = 0
    while True:
        attempt += 1
        try:
            result = await session.call_tool(tool, args)
            return _unwrap(result)
        except ToolError as e:
            if e.error_type == "SessionExpiredError" and attempt <= max_retries:
                continue
            if e.error_type == "TransportError" and attempt <= max_retries:
                await asyncio.sleep(5)
                continue
            raise


# --------------------------------------------------------------------------- #
# Workspace IO
# --------------------------------------------------------------------------- #

def _safe_name(name: str) -> str:
    return name.replace(":", "__").replace("|", "__").replace("/", "_")


def _ws_paths(out_root: str) -> dict[str, str]:
    ex = os.path.join(out_root, "extraction")
    return {
        "root": out_root, "extraction": ex,
        "connection": os.path.join(ex, "connection.json"),
        "schemas_index": os.path.join(ex, "schemas_index.json"),
        "counts": os.path.join(ex, "counts.json"),
        "definitions": os.path.join(ex, "definitions"),
        "records": os.path.join(ex, "records"),
        "entities": os.path.join(ex, "entities"),
    }


def _ensure_dirs(paths: dict[str, str]) -> None:
    for key in ("extraction", "definitions", "records", "entities"):
        os.makedirs(paths[key], exist_ok=True)


def _write_json(path: str, obj: Any) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def _load_json(path: str, default: Any) -> Any:
    if not os.path.exists(path):
        return default
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _iter_jsonl(path: str):
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                yield json.loads(line)


# --------------------------------------------------------------------------- #
# Operations — each calls a tool and streams/writes its result
# --------------------------------------------------------------------------- #

async def op_connection(session: ClientSession, paths: dict[str, str]) -> None:
    data = await call(session, "test_connection", {})
    _write_json(paths["connection"], data)
    inst = data.get("instance", {})
    print(f"[connection] OK  login={data.get('login')}  "
          f"version={inst.get('version')} build={inst.get('build')}")


async def op_schemas(session: ClientSession, paths: dict[str, str],
                     include_builtin: bool, namespace: str | None) -> None:
    args: dict[str, Any] = {"include_builtin": include_builtin}
    if namespace:
        args["namespace"] = namespace
    data = await call(session, "list_schemas", args)
    rows = data.get("rows", [])
    for r in rows:
        ns = r.get("namespace", "")
        r["is_custom"] = ns not in RESERVED_NAMESPACES
    _write_json(paths["schemas_index"], data)
    custom = sum(1 for r in rows if r.get("is_custom"))
    print(f"[schemas] wrote {len(rows)} schemas ({custom} custom) "
          f"-> {paths['schemas_index']}")


async def op_count(session: ClientSession, paths: dict[str, str],
                   schema: str, where: str | None) -> None:
    args: dict[str, Any] = {"schema": schema}
    if where:
        args["where"] = where
    data = await call(session, "count_records", args)
    n = data.get("count")
    counts = _load_json(paths["counts"], {})
    counts[schema] = {"count": n, "where": where}
    _write_json(paths["counts"], counts)
    print(f"[count] {schema} = {n}" + (f"  where={where}" if where else ""))


async def op_definition(session: ClientSession, paths: dict[str, str],
                        schema: str, form: str) -> None:
    data = await call(session, "get_schema_definition",
                      {"schema": schema, "form": form})
    out = os.path.join(paths["definitions"], f"{_safe_name(schema)}.json")
    _write_json(out, data)
    if data.get("truncated"):
        print(f"[definition] {schema} -> {out}  WARNING: truncated "
              f"(use form=inventory)")
    else:
        print(f"[definition] {schema} -> {out}  (form={form})")


async def op_records(session: ClientSession, paths: dict[str, str], schema: str,
                     fields: list[str], where: str | None, resume: bool,
                     sample_max: int | None, page_size: int) -> None:
    """
    Stream records to <ns>__<n>.jsonl by looping query_schema with its cursor until
    next_cursor is absent, writing a .meta.json completeness proof. Resumable: on
    --resume we re-drive from the saved cursor (or restart if none was saved).
    """
    base = _safe_name(schema)
    jsonl_path = os.path.join(paths["records"], f"{base}.jsonl")
    meta_path = os.path.join(paths["records"], f"{base}.meta.json")

    counts = _load_json(paths["counts"], {})
    expected = counts.get(schema, {}).get("count")

    rows_written = 0
    cursor: str | None = None
    mode = "w"

    if resume and os.path.exists(meta_path):
        prev = _load_json(meta_path, {})
        cursor = prev.get("last_cursor")
        if cursor and os.path.exists(jsonl_path):
            rows_written = sum(1 for _ in _iter_jsonl(jsonl_path))
            mode = "a"
            print(f"[records] resuming {schema} from saved cursor "
                  f"({rows_written} rows already on disk)")
        else:
            cursor = None  # can't resume cleanly; start over
            print(f"[records] no usable cursor for {schema}; restarting")

    complete = False
    with open(jsonl_path, mode, encoding="utf-8") as out:
        while True:
            args: dict[str, Any] = {"schema": schema, "fields": fields,
                                    "page_size": page_size}
            if where:
                args["where"] = where
            if cursor:
                args["cursor"] = cursor
            data = await call(session, "query_schema", args)

            rows = data.get("rows", [])
            for row in rows:
                out.write(json.dumps(row, ensure_ascii=False) + "\n")
                rows_written += 1
                if sample_max and rows_written >= sample_max:
                    break
            out.flush()

            cursor = data.get("next_cursor")
            print(f"[records] {schema}: {rows_written} rows so far")

            if sample_max and rows_written >= sample_max:
                break
            if not cursor:
                complete = True
                break

    sampled = bool(sample_max and rows_written >= sample_max and not complete)
    meta = {
        "schema": schema, "fields": fields, "where": where,
        "rows_written": rows_written, "expected_count": expected,
        "complete": (expected is not None and rows_written == expected)
                    or (expected is None and complete and not sampled),
        "last_cursor": cursor, "sampled": sampled,
    }
    _write_json(meta_path, meta)

    status = "COMPLETE" if meta["complete"] else ("SAMPLED" if sampled
                                                  else "INCOMPLETE")
    exp = f" / expected {expected}" if expected is not None else ""
    print(f"[records] {schema}: {rows_written} rows{exp} -> {status}")
    if not meta["complete"] and not sampled:
        print(f"[records] WARNING: {schema} incomplete — rerun with --resume")
        sys.exit(3)


async def op_entity(session: ClientSession, paths: dict[str, str],
                    entity_key: str) -> None:
    schema = entity_key.split("|", 1)[0]
    if schema in ("nms:delivery", "xtk:workflow"):
        sys.stderr.write(
            json.dumps({"isError": True, "errorType": "EntityKeyError",
                        "message": f"{schema} cannot be fetched by entity key; "
                                   f"use records for its metadata."}) + "\n")
        sys.exit(1)
    data = await call(session, "get_entity", {"entity_key": entity_key})
    out = os.path.join(paths["entities"], f"{_safe_name(entity_key)}.json")
    _write_json(out, data)
    print(f"[entity] {entity_key} -> {out}  ({data.get('chars', '?')} chars)")


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #

def _parse_fields(s: str | None) -> list[str]:
    return [x.strip() for x in (s or "").split(",") if x.strip()]


def _need(args: argparse.Namespace, attr: str) -> None:
    if not getattr(args, attr, None):
        sys.stderr.write(f"ERROR: --{attr.replace('_', '-')} is required "
                         f"for op '{args.op}'.\n")
        sys.exit(2)


async def run(args: argparse.Namespace) -> int:
    paths = _ws_paths(args.out)
    _ensure_dirs(paths)
    try:
        async with open_session() as session:
            if args.op == "connection":
                await op_connection(session, paths)
            elif args.op == "schemas":
                await op_schemas(session, paths,
                                 include_builtin=not args.no_builtin,
                                 namespace=args.namespace)
            elif args.op == "count":
                _need(args, "schema")
                await op_count(session, paths, args.schema, args.where)
            elif args.op == "definition":
                _need(args, "schema")
                await op_definition(session, paths, args.schema, args.form)
            elif args.op == "records":
                _need(args, "schema")
                await op_records(session, paths, args.schema,
                                 _parse_fields(args.fields), args.where,
                                 args.resume, args.sample_max, args.page_size)
            elif args.op == "entity":
                _need(args, "entity_key")
                await op_entity(session, paths, args.entity_key)
    except ToolError as e:
        sys.stderr.write(json.dumps({"isError": True, "errorType": e.error_type,
                                     "message": e.message}) + "\n")
        return 1
    return 0


def main(argv: list[str]) -> int:
    p = argparse.ArgumentParser(
        description="ACCv7 MCP-client extraction (streams tool results to disk).")
    p.add_argument("op", choices=["connection", "schemas", "count",
                                  "definition", "records", "entity"])
    p.add_argument("--out", required=True,
                   help="workspace root, e.g. ./acc7-migration-audit-<instance>")
    p.add_argument("--schema", help="qualified schema name, e.g. cus:order")
    p.add_argument("--fields", help="comma-separated XPath fields for records")
    p.add_argument("--where", help="XPath-style condition")
    p.add_argument("--entity-key", help="'schema|name' for entity op")
    p.add_argument("--form", default="inventory",
                   choices=["inventory", "compiled", "source", "wsdl"],
                   help="definition op: schema form (default inventory)")
    p.add_argument("--no-builtin", action="store_true",
                   help="schemas op: exclude reserved-namespace schemas")
    p.add_argument("--namespace", help="schemas op: filter to one namespace")
    p.add_argument("--resume", action="store_true",
                   help="records op: continue from the saved cursor")
    p.add_argument("--sample-max", type=int,
                   help="records op: stop after N rows (explicit sampling)")
    p.add_argument("--page-size", type=int, default=200,
                   help="records op: rows per page (server-capped)")
    args = p.parse_args(argv)
    return asyncio.run(run(args))


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))