# Architecture and working context

Context for anyone (human or AI agent) changing `pyaisync`. Read this before touching the writer, a
target adapter or an importer. The user-facing description is in the [README](../README.md); the original
design and its rationale are in [PLAN.md](../PLAN.md); the native formats are in
[formats/README.md](formats/README.md).

## What the project is

A small Python 3.11+ CLI (`uv`-friendly, one runtime dependency: `pyyaml`) that keeps AI coding agent configuration in
one place (`.ai/`) and **transpiles** it into the native files of Claude Code, Cursor and OpenCode. It can also
**import** native configuration back into `.ai/` and package a Claude Code plugin. Same idea as
[Rulesync](https://github.com/dyoshikawa/rulesync), deliberately smaller and auditable: no network, nothing from user
content is ever executed, and files it did not generate are never overwritten.

## Module map (`src/pyaisync/`)

| Module | Responsibility |
|---|---|
| `cli.py` | `argparse` commands: `init`, `generate`, `check`, `import`, `plugin`, `list`. Output and exit codes only; no logic. |
| `config.py` | Reads `.ai/config.toml` (targets, feature flags, optional `[plugin]`). |
| `loader.py` | Reads and validates `.ai/` into a `Model`. Every user error is a `PyAisyncError` naming the file. |
| `model.py` | Frozen dataclasses (`Rule`, `Command`, `Skill`, `Agent`, `McpServer`, `Hook`, `PermissionSet`, `Config`, `Model`). No formatting logic. |
| `vocab.py` | Canonical vocabulary and its per-target translation: tool names, hook events, permission rules, env references, secret checks. |
| `frontmatter.py` | Markdown + YAML frontmatter parser/serializer (`yaml.safe_load` only). |
| `targets/` | **Adapters** (`claude.py`, `cursor.py`, `opencode.py`): `Model -> Plan`. Pure functions, no disk access. `base.py` has `Output`, `JsonMerge`, `Plan`. `__init__.py::build_plan` merges the adapters and adds the shared skills. |
| `writer.py` | **Applies a `Plan` to disk** without destroying anything (manifest + hashes). Also does the shared-JSON merge. |
| `manifest.py` | Ownership manifest (`.ai/.manifest.json`): which files and JSON entries pyaisync owns, as hashes. |
| `safefs.py` | Safe paths (no `..`, absolute paths or symlinks), size limits, atomic writes. **All disk access goes through here.** |
| `importers/` | Native config -> `.ai/`. `__init__.py` (rules, commands, skills, conflict policy), `structured.py` (agents, MCP, hooks, permissions), `common.py`. |
| `plugin.py` | `.ai/` -> Claude Code plugin directory (reuses the Claude adapter, then remaps paths). |

## Data flow

```
.ai/  --loader-->  Model  --adapters-->  Plan(outputs, merges, warnings, notices)  --writer-->  disk + manifest
```

- `Plan.outputs` are whole files (`Output(path, bytes)`).
- `Plan.merges` are `JsonMerge(path, key_path, entries)`: pyaisync owns only the *entries* under `key_path` of a JSON
  file the user may also edit. `entries` is a `dict` (named entries, e.g. MCP servers) or a `list` (array items, e.g.
  hook groups or permission rules).
- `Plan.warnings` are things a target cannot express; `Plan.notices` are the hook commands that will run.
- `check` is `generate` without writing; `--dry-run` likewise. `plugin` builds a different `Plan` and applies it to
  another directory with its own manifest (`.pyaisync-plugin.json`).

## The writer (the part that must not break)

For every file in the plan, compared with disk and the manifest:

| On disk | In manifest | Result |
|---|---|---|
| missing | - | create |
| same content as planned | any | unchanged (and becomes owned) |
| differs | yes, hash matches disk | update |
| differs | yes, hash differs (hand edit) | **conflict** unless `--force` |
| differs | no (foreign file) | **conflict** unless `--adopt` / `--force` |
| exists, no longer planned | yes, hash matches | delete (and prune empty dirs) |
| exists, no longer planned | yes, hand-edited | keep, warn |

**Any conflict aborts before anything is written.** Writes are atomic (temp file + rename). The manifest is written last.

Shared JSON files (`.mcp.json`, `.cursor/mcp.json`, `opencode.json`, `.claude/settings.json`, `.cursor/hooks.json`,
`.cursor/cli.json`) follow the same idea per entry: named entries are tracked by value hash; list items are tracked by
content hash (so a hand-edited item is simply no longer recognised and is treated as the user's). A file pyaisync created
and later emptied is deleted. Files with comments (JSONC) are refused, never rewritten.

### Manifest shape

```json
{
  "version": 1,
  "files": { "CLAUDE.md": "<sha256>" },
  "json": {
    ".mcp.json": {
      "created": true,
      "ops": { "mcpServers": { "key_path": ["mcpServers"], "kind": "dict", "entries": { "docs": "<hash>" } } }
    },
    ".claude/settings.json": {
      "created": false,
      "ops": { "hooks/Stop": { "key_path": ["hooks", "Stop"], "kind": "list", "entries": { "<hash>": "<hash>" } } }
    }
  }
}
```

## Importers

Read-only on native files; write only to `.ai/`. Three rules that must hold for any new importer code:

1. **Never widen access.** An `allow` rule or a tool allowlist that cannot be mapped exactly is skipped (a `deny`/`ask`
   may be widened). Unmappable agents are skipped rather than imported with more tools than they had.
2. **Never write a literal secret into `.ai/`.** Sensitive values become `${VAR}` references, with a warning.
3. **Never re-import what pyaisync generated** (detected through the manifest hashes and the generated marker).

## Design decisions worth remembering

- **Skills are written once**, to `.claude/skills/` (or `.agents/skills/` when Claude is not a target): all three tools read
  it, and one copy avoids duplicates and drift.
- **OpenCode: V2 schema only** (`mcp.servers`, `permissions` as an ordered list where the last match wins, so the generator
  emits allow, then ask, then deny).
- **Hooks are data.** pyaisync never runs them; `generate`/`check` print every command that will run.
- **Unsupported is a warning, never an approximation** (Cursor has no `ask` and no tool allowlist; OpenCode has no
  declarative hooks; Cursor cannot match `glob`/`web` tools, so that hook is skipped for Cursor).
- **Output is deterministic**: sorted iteration, fixed JSON indentation, `\n` endings. `check` depends on it.

## How to extend

**Add a target** (e.g. a new editor): create `targets/<name>.py` with `name`, `supported` and `plan(model) -> Plan`
(pure, no disk access); register it in `targets/__init__.py::REGISTRY`; add the name to `ALL_TARGETS` in `model.py`;
add its translations to `vocab.py`; write an importer branch in `importers/`; document the verified format in
`docs/formats/README.md` **with the source URL and the date**; add tests (generate, idempotence, import round trip).

**Add a feature**: add the dataclass to `model.py`, the name to `FEATURES`, loading and validation to `loader.py`, one
`Plan` contribution per adapter (or an explicit warning where unsupported), an importer path, and tests. Update the support
matrix in `cli.py::cmd_list` (it is derived from each adapter's `supported`).

**Change a native format**: first verify it against the tool's official documentation, update
`docs/formats/README.md`, then the adapter and importer, then the tests. Never rely on memory for formats; they change
often.

## Testing

`uv run pytest` (96 tests), `uv run ruff check . && uv run ruff format --check .`, `uv run mypy` (strict). All three must be
green before committing.

- Tests build a throw-away repo with the `repo` fixture (`tests/conftest.py`) or `tmp_path`, and run the CLI through
  `main(["--root", str(root), ...])`.
- **Never let a test write outside `tmp_path`.** Every path option is resolved against `--root`, not the current directory
  (an early bug wrote plugin files into the working copy because `--out` was resolved against the cwd).
- Security behaviour is tested explicitly: names with `..`, symlinks, JSONC, literal secrets, hand-edited files,
  foreign files, and "nothing is written when there is a conflict".
- Assertions are on exact generated content, plus idempotence (a second `generate` changes nothing) and `check`.

## Conventions

- Everything in the repository (code, comments, docs, messages, commits) is in **English**.
- Errors the user can fix are `PyAisyncError` with the offending file in the message; the CLI prints `error: ...` and exits 2.
  Exit codes: 0 ok, 1 `check` found drift, 2 error or conflict.
- Do not add runtime dependencies without a strong reason (the supply-chain surface is part of the design).
- Commit only when asked; end commit messages with the attribution line the environment requires.
