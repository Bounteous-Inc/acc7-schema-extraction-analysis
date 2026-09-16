
# ACCv7 Migration Client (Claude Code plugin)

Tooling to audit an Adobe Campaign Classic **v7** instance ahead of a migration to v8, and
produce a verification checklist for the testing team. It works against your **ACCv7 MCP
server** — it consumes that server, it is not the server itself.

This is a **Claude Code plugin** (`.claude-plugin/plugin.json`). Test it locally with
`claude --plugin-dir ./this-repo`, or install it into a marketplace/another project with
`/plugin install <owner>/<repo>`. Opening this folder directly as a plain project (`cd` in
and run `claude`) will **not** pick up the skills or `.mcp.json` — they only load through
the plugin mechanism, since `skills/` here lives at the plugin root, not under `.claude/`.

## Pairing with a server

This client is designed to pair with
[`acc7-mcp-server`](https://github.com/Bounteous-Inc/acc7-mcp-server) — a read-only MCP
server exposing `test_connection`, `list_schemas`, `count_records`,
`get_schema_definition`, `query_schema`, `get_entity` over ACCv7's SOAP API. Install and run
that server, then supply its connection details when you enable this plugin (see Setup
below).

You are **not** required to use that specific server: the two skills here only depend on the
tool contracts documented in
`skills/acc7-migration-extraction/references/tool-contracts.md`. Any MCP server (stdio or
http) that implements those same read-only tools will work.

## Requirements

- Python 3.10+ and `pip` on the machine running Claude Code (for the extraction script;
  the extraction skill installs its dependency for you with `pip install "mcp[cli]"` the
  first time it runs).
- Your own running instance of an ACCv7 MCP server (see "Pairing with a server").

## What's in here

```
acc7-migration-client/
├── .claude-plugin/
│   └── plugin.json                         # plugin manifest + userConfig (ACC connection fields)
├── skills/
│   ├── acc7-migration-extraction/          # SKILL 1 — pull v7 into files (runs the client script)
│   │   ├── SKILL.md
│   │   ├── references/
│   │   │   ├── tool-contracts.md           # the MCP server's tool contracts
│   │   │   ├── workspace-contract.md       # shared on-disk layout (extraction <-> analysis)
│   │   │   └── extraction-script.md        # how/why the client script is used
│   │   └── scripts/
│   │       ├── extract_records.py          # the MCP-client extraction script (canonical copy)
│   │       └── README.md                   # every operation, flag, exit code
│   └── acc7-migration-analysis/            # SKILL 2 — read files, write the deliverable
│       ├── SKILL.md
│       └── references/
│           ├── deliverable-template.md     # required shape of the testing deliverable
│           └── workspace-contract.md       # same shared contract (copy)
├── .mcp.json                               # ACCv7 MCP server registration, values from userConfig
└── .gitignore
```

## How the pieces fit

1. **The MCP server** (built separately — see "Pairing with a server" above) exposes
   read-only tools over ACCv7: `test_connection`, `list_schemas`, `count_records`,
   `get_schema_definition`, `query_schema`, `get_entity`. Register it in `.mcp.json`.
2. **`skills/acc7-migration-extraction/scripts/extract_records.py`** is an MCP *client*. It
   calls those tools and streams each result to disk. It exists because an AI model cannot
   pull large tool results into files without them passing through its context window
   (which truncates big tables); a script has no such limit. Transport (`http`/`stdio`) is
   chosen at runtime from `MCP_TRANSPORT`. It can also be run directly by hand or in CI, no
   AI involved — see its own `README.md` for every operation and flag.
3. **The two skills** drive the workflow for Claude Code:
   - `acc7-migration-extraction` runs the client script in the right order and verifies row
     counts against `count_records`.
   - `acc7-migration-analysis` reads that workspace (no MCP calls) and writes
     `analysis/migration-test-insights.md`.

## Setup

1. Install the plugin (`claude --plugin-dir ./this-repo` for local testing, or
   `/plugin install <owner>/<repo>` once published).
2. Install and run [`acc7-mcp-server`](https://github.com/Bounteous-Inc/acc7-mcp-server), or
   point at your own compatible ACCv7 MCP server.
3. When Claude Code prompts you to configure the plugin (or via `/plugin` /
   `/config`), fill in: ACC base URL, ACC login (must be a **native operator** — an Adobe ID
   cannot use the SOAP API), ACC password, and — depending on your server's transport —
   either the MCP server URL (`acc7-http`) or the path to the `acc-mcp-server` binary
   (`acc7-stdio`). The password is marked sensitive and stored in your OS keychain, not in a
   plaintext file.

No `.env` file or manual `.mcp.json` editing needed — `.mcp.json` reads these values via
`${user_config.*}` substitution once you've configured the plugin.

## Run it — via Claude Code (skills)

- "test the ACC connection" → runs `test_connection`
- "extract the v7 instance for migration" → the extraction skill drives the client script
- "produce the migration test insights" → the analysis skill writes the deliverable

## Run it — by hand (no AI)

```bash
pip install "mcp[cli]"
WS=./acc7-migration-audit-<instance>
SCRIPT=skills/acc7-migration-extraction/scripts/extract_records.py
python "$SCRIPT" connection --out "$WS"
python "$SCRIPT" schemas    --out "$WS" --no-builtin
python "$SCRIPT" count      --out "$WS" --schema cus:order
python "$SCRIPT" definition --out "$WS" --schema cus:order
python "$SCRIPT" records    --out "$WS" --schema cus:order \
    --fields @internalName,@label
```

See `skills/acc7-migration-extraction/scripts/README.md` for every operation, flag, the
completeness guarantee, and exit codes.

## Output

Everything lands in `acc7-migration-audit-<instance>/` (gitignored — generated data, often
client-confidential):

- `extraction/` — connection, schema index, counts, definitions, records (`.jsonl` +
  `.meta.json` completeness proofs), entities
- `analysis/migration-test-insights.md` — the deliverable for the testing team

## Notes

- The two skills keep their own copy of `workspace-contract.md` because a skill can't read
  another skill's files — keep them in sync if you edit the layout.
