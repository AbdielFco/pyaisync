# pyaisync — Implementation plan

> Status: **All phases (0-4) implemented**: rules, commands, skills, subagents, mcp, hooks, permissions;
> `init` / `generate` / `check` / `list` / `import` (all features) / `plugin claude`. Verified formats live in `docs/formats/README.md`.
> Date: 2026-11-25

## 1. Goal

Keep the AI agent configuration of a repository **in one place** (`.ai/`) and **transpile** it
into the native format of each tool, as a Python package installable and runnable with `uv`.

Same problem and same idea as [Rulesync](https://github.com/dyoshikawa/rulesync)
(single source → native files), but:

- Written in Python, no Node, runnable with `uv run` / `uvx`.
- **Small and auditable**: few dependencies, no network, never executes commands.
- Only **3 targets** at first: **Claude Code, Cursor, OpenCode**.

### The problem it solves
Every tool wants its own file today (`CLAUDE.md`, `.cursor/rules/*.mdc`, `AGENTS.md`,
`opencode.json`, `.mcp.json`, ...). Maintaining them by hand makes them drift apart.
`pyaisync` generates all of them from one source and can verify in CI that nothing drifted.

## 2. Design principles

1. **Simple before complete.** Each feature per target is a small, testable adapter.
2. **One source, derived output.** Generated files are not edited by hand.
3. **Non-destructive.** It only touches files it generated itself (manifest with hashes).
4. **Honest about the unsupported.** If a target does not support a feature, it warns; it never
   degrades silently.
5. **Deterministic.** Same input → same output, byte for byte (required for `--check`).
6. **No dangerous side effects.** No network. It never runs hooks or MCP servers: it only writes them as data.
7. **Few dependencies.** `tomllib` (stdlib, Python ≥ 3.11) + `pyyaml`. Nothing else at runtime.

## 3. Scope

### 3.1 Features (the model covers them all; adapters ship in phases)

| Feature | Description |
|---|---|
| rules | Project instructions (always / by glob / by description) |
| commands | Slash-style commands with a prompt and arguments |
| subagents | Specialized agents (prompt, tools, model) |
| skills | Folders with `SKILL.md` (+ auxiliary files) |
| mcp | MCP servers (local stdio / remote http-sse) |
| hooks | Agent events → commands |
| permissions | allow / ask / deny for tools and commands |
| plugins | Packaging of the above for distribution |

### 3.2 Target matrix (destination of each feature)

Verification status: ✅ confirmed in official docs · ⚠️ known format, not confirmed ·
❓ **not verified** (to be verified before the phase that needs it).

| Feature | Claude Code | Cursor | OpenCode |
|---|---|---|---|
| rules | `CLAUDE.md` ✅ | `.cursor/rules/*.mdc` with `description/globs/alwaysApply` ✅ | `AGENTS.md` ✅ |
| commands | `.claude/commands/*.md` ✅ | `.cursor/commands/*.md` ⚠️ | `.opencode/commands/*.md` ✅ |
| subagents | `.claude/agents/*.md` ✅ | `.cursor/agents/*.md` ✅ (no tool allowlist; `readonly` only) | `.opencode/agents/*.md` ✅ (V2) |
| skills | `.claude/skills/<n>/SKILL.md` ✅ (read by all 3) | `.agents/skills/` or `.cursor/skills/` ✅ | `.agents/skills/` or `.opencode/skills/` ✅ |
| mcp | `.mcp.json` (`mcpServers`) ✅ | `.cursor/mcp.json` (`mcpServers`) ✅ | `mcp.servers` in `opencode.json` ✅ (V2; Streamable HTTP only) |
| hooks | `.claude/settings.json` (`hooks`) ✅ | `.cursor/hooks.json` ✅ | none declarative (JS/TS plugins) — skipped with a warning ✅ |
| permissions | `.claude/settings.json` (`permissions`) ✅ | `.cursor/cli.json` (Cursor **CLI** only; no `ask`) ✅ | top-level `permissions` list in `opencode.json` (V2) ✅ |
| plugins | `.claude-plugin/plugin.json` + components at the plugin root ✅ (packaged by `pyaisync plugin claude`) | not packaged (no equivalent in scope) | not packaged |

Note: the OpenCode docs differ between V1 and V2 (e.g. flat `mcp` vs `mcp.servers`).
Decision: **V2 only** (see §13).

### 3.3 Out of scope (non-goals)

- More than 3 targets in the first version (others are added later as adapters).
- Installing or running agents, MCP servers or hooks.
- Downloading rules/skills from remote repos (Rulesync's `fetch`): it is the main risk vector of
  that tool and is deliberately excluded.
- User-level global configuration (`~/.claude`, `~/.config/opencode`): project level only.
- Automatic bidirectional sync. There is only a one-off `import` (basic in Phase 1, full in Phase 4).
- GUI, daemon mode, watch mode.

## 4. How it works

```
 .ai/ (source)
   │  loader: reads Markdown+frontmatter and TOML, validates
   ▼
 Internal model (dataclasses)       Rule · Command · Agent · Skill · McpServer · Hook · Permission
   │  for each enabled target: adapter
   ▼
 File plan (in memory)              list of (path, content, kind: file | JSON-merge)
   │  writer: compares with disk and manifest
   ▼
 Write        or      Diff (--dry-run)      or      Check (--check)
   │
   ▼
 .ai/.manifest.json  (generated files + hash + managed JSON keys)
```

### 4.1 `generate` flow
1. Load and validate `.ai/` (clear errors with file name).
2. For each target enabled in `config.toml`, the adapter produces the file plan.
3. Features a target does not support produce a **warning** (not an error), listed in the summary.
4. The writer compares the plan with disk and manifest:
   - New file → create.
   - Manifest file whose on-disk hash **matches** the recorded one → update/delete if gone from the source.
   - Manifest file **edited by hand** (different hash) → **not overwritten**; error with instructions (`--force` to overwrite).
   - Existing file **not** in the manifest → **not touched**; error (`--adopt` to take it over).
   - If there is any conflict, **nothing is written**.
5. Writes atomically (temp file + rename) and updates the manifest.

### 4.2 JSON merge (`opencode.json`, `.mcp.json`, `.cursor/mcp.json`; later `settings.json`) — implemented in Phase 2
These files mix our keys with the user's. Rules:
- Two kinds of ownership, both recorded in the manifest (`json.<file>.ops`): **named entries** in an object (MCP servers, keyed by name with a hash of the value) and **list items** in an array (hook groups, permission rules; identified by the hash of the item's content).
- List items are matched by content: an item pyaisync added is removed when it is no longer generated, but one you edited by hand is no longer recognized and is treated as yours (the regenerated item is appended next to it).
- The rest of the JSON is preserved (keys and values; the file is re-serialized with 2-space indentation **only when an entry changes**).
- A same-named entry that pyaisync does not own → conflict (`--adopt`); an owned entry edited by hand → conflict (`--force`).
- When the last owned entry is removed, emptied parent objects are pruned and a file that pyaisync created itself is deleted.
- Files with comments (JSONC) are refused, never rewritten.

### 4.3 `--check`
Computes the plan in memory and compares it with disk. Non-zero exit code on differences or
hand-edited managed files. Meant for pre-commit and CI.

## 5. Source format `.ai/`

```
.ai/
  config.toml
  rules/*.md
  commands/*.md
  agents/*.md                   # subagents
  skills/<name>/SKILL.md        # + auxiliary files (copied as-is)
  mcp.toml                      # MCP servers
  hooks.toml                    # hooks
  permissions.toml              # permissions
  .manifest.json                # generated, committed
```

**Decision:** Markdown + YAML frontmatter for narrative content (rules, commands, agents, skills);
TOML for structured data (config, mcp, hooks, permissions). Rationale: narrative reads and edits
better in Markdown; structured data is less error-prone in TOML than in YAML (no type ambiguity)
and `tomllib` is in the stdlib.

### Examples

`.ai/config.toml`
```toml
version = 1
targets = ["claude", "cursor", "opencode"]

[features]            # all on by default; can be limited
rules = true
commands = true
skills = true
```

`.ai/rules/python.md`
```markdown
---
description: Python conventions of the project
globs: ["**/*.py"]       # optional
always: false            # true => always applied (root rule)
targets: ["*"]           # optional, all targets by default
---
- Use `uv run pytest` for tests and `uv run ruff check .` for lint.
```

`.ai/commands/review.md`
```markdown
---
description: Review the current diff
---
Review the uncommitted changes. Point out bugs and missing tests. $ARGUMENTS
```

`.ai/agents/reviewer.md`
```markdown
---
description: Read-only code reviewer
tools: [read, grep, glob]   # canonical pyaisync names, translated by each adapter
model: inherit
---
You are a strict reviewer. Do not modify files.
```

`.ai/mcp.toml`
```toml
[servers.docs]
type = "http"           # stdio | http | sse ("remote" is accepted as an alias of http)
url = "https://example.com/mcp"

[servers.local-tool]
type = "stdio"
command = "uvx"
args = ["my-mcp-server"]
env = { API_KEY = "${API_KEY}" }   # variable references only, never values
```

`.ai/hooks.toml`
```toml
[[hooks]]
event = "pre_tool_use"      # canonical pyaisync events (see docs/formats)
matcher = ["shell"]         # canonical tools; only for pre_tool_use / post_tool_use
command = "./scripts/guard.sh"   # one line, relative to the repository root
timeout = 30                # seconds, optional
```

`.ai/permissions.toml`
```toml
allow = ["shell(uv run pytest*)"]
deny  = ["read(.env*)", "shell(rm -rf*)"]
ask   = ["shell(git push*)"]
```

### Canonical vocabulary (agent tools, hook events, permission rules)
`pyaisync` defines its own names for tools (`read`, `grep`, `glob`, `edit`, `shell`, `web`) and, later, hook events
(`pre_tool_use`, `post_tool_use`, `session_start`, ...). Each adapter translates them. A name with
no equivalent in a target → warning, never silence. Secrets never go in the source: only `${VAR}`;
the validator rejects values that look like literal keys.

## 6. Internal model (`model.py`)

Immutable dataclasses with no formatting logic:
`Rule(name, body, description, globs, always, targets)`, `Command`, `Skill(name, files)`,
and later `Agent`, `McpServer(name, type, command|url, args, env)`, `Hook(event, matcher, command)`,
`PermissionSet(allow, ask, deny)`. The loader validates with explicit checks (no framework),
with messages that name the file.

## 7. Adapters (`targets/`)

Minimal interface:

```python
class Target(Protocol):
    name: str
    supported: frozenset[str]

    def plan(self, model: Model) -> Plan: ...
```
`Output` is a `File(path, content)` (and, in Phase 2, a `JsonMerge(path, key_path, value, owner_id)`).
One adapter = one file, pure functions (input → output), testable without touching disk.

### Per-target decisions
- **Claude:** rules → `CLAUDE.md` (concatenated sections); the rest, one file per element.
- **Cursor:** each rule → one `.mdc` with `alwaysApply` / `globs` / `description`. A `.md` without
  frontmatter is ignored by Cursor, so `.mdc` is always emitted.
- **OpenCode:** rules → `AGENTS.md`; MCP/permissions → merged into `opencode.json` (Phase 2).
  Hooks: OpenCode has no declarative hooks → **unsupported** in the MVP (warning).
- **Shared instructions body:** Claude reads `CLAUDE.md` and OpenCode reads `AGENTS.md`; the rules
  content is the same, so one body is built and written where each tool reads it.
- **Skills (shared):** skills are an open format and all 3 targets read `.claude/skills/`; Cursor and
  OpenCode also read `.agents/skills/`. A single copy is written (`.claude/skills/` when Claude is a
  target of the skill, otherwise `.agents/skills/`) instead of one per target.

## 8. Security

| Risk | Mitigation |
|---|---|
| Overwriting manual work | Manifest + hashes; never touches unmanaged files |
| Path traversal / symlinks in names | Validated names (`[a-z0-9._-]`), paths resolved and checked under the root; symlinks are never followed |
| Secrets in the repo | Only `${VAR}` in the source; the loader rejects literal values under sensitive-looking keys (`key`, `token`, `secret`, `password`, `auth`...) and known token formats |
| Malicious hooks/MCP | Only written as data; `generate` and `check` print every hook command that will run on the machine ("review them"); hook commands must be a single line and cannot contain literal tokens |
| Supply chain | Runtime: `pyyaml` only; pinned version; `uv.lock` committed |
| Non-deterministic output | Stable ordering, fixed JSON indentation, `\n` line endings |
| Code making network calls | None in the package |

## 9. CLI

```
pyaisync init                 # create .ai/ (see §9.1)
   --from claude|cursor|opencode  # init + import the existing native config
   --targets claude,cursor  # targets to enable in config.toml
pyaisync generate             # generate native files
   --targets claude,cursor  # limit targets
   --features rules,commands  # limit features
   --dry-run                # show the diff, write nothing
   --force / --adopt        # see §4.1
pyaisync check                # fails (non-zero) on drift; for CI / pre-commit
pyaisync list                 # what each target supports (the real matrix)
pyaisync plugin claude        # package .ai/ as a Claude Code plugin directory (see §9.3)
   --out DIR                # relative to --root; default dist/<plugin.name>
   --dry-run / --force / --adopt
pyaisync import <target>      # bring native config into .ai/ (see §9.2). Basic in Phase 1
   --dry-run                # show what would be imported and what would not
   --on-conflict skip|rename|overwrite   # default: skip (with a warning)
```

`generate` output: a list of created/updated/deleted files, an unchanged count and a
"warnings" section (unsupported features, generated hooks and MCP).

### 9.1 `pyaisync init`

Creates the minimal structure to get started. **Never overwrites**: if `.ai/` already exists it
aborts with a message (use `init` only in repos without `.ai/`).

Files it creates (`init` without `--from`):
```
.ai/
  config.toml                 # chosen targets, enabled features, version = 1
  rules/project.md            # example root rule (always: true)
  commands/example.md         # example command
  skills/.gitkeep
```
(`mcp.toml`, `hooks.toml` and `permissions.toml` are added in the phases that implement them.)
- `--targets` sets the targets; if omitted it **detects** which already exist in the repo
  (`CLAUDE.md`, `.cursor/`, `opencode.json`) and uses them (all three if none is found).
- It does not touch `.gitignore` or native files, and generates nothing (that is `generate`).
- `init --from <target>`: runs `init` and then `import <target>`, to migrate an existing
  configuration in a single step.
- When finished it prints the next steps (`pyaisync generate --dry-run`).

### 9.2 `pyaisync import <target>`

Converts a target's **native** configuration into the canonical format in `.ai/`.
It is what lets you adopt `pyaisync` in a repo that already has configuration without rewriting it by hand.

**Principles**
- **Read-only on native files; writes only to `.ai/`.** It never modifies or deletes the source.
- **Never executes or evaluates** imported content (hooks, MCP commands: data only).
- **Everything that cannot be mapped is reported**, never silently dropped.
- Same safety validations as `generate` (names, paths, symlinks).

**What it reads for each target**

| Target | Reads | Produces in `.ai/` |
|---|---|---|
| Claude Code | `CLAUDE.md`, `.claude/commands/*.md`, `.claude/skills/*` | rules, commands, skills |
| Cursor | `.cursor/rules/*.mdc` (`description`, `globs`, `alwaysApply`), `.cursor/commands/*.md`, `.cursor/skills/*`, `.agents/skills/*` | rules (`always`/`globs`/`description`), commands, skills |
| OpenCode | `AGENTS.md`, `.opencode/commands/*.md` (and legacy `command/`), `.opencode/skills/*`, `.agents/skills/*` | rules, commands, skills |
| Claude Code | + `.claude/agents`, `.mcp.json`, `.claude/settings.json` (`hooks`, `permissions`) | agents, `mcp.toml`, `hooks.toml`, `permissions.toml` |
| Cursor | + `.cursor/agents`, `.cursor/mcp.json`, `.cursor/hooks.json`, `.cursor/cli.json` | same |
| OpenCode | + `.opencode/agents`, `opencode.json` (`mcp.servers`, `permissions`) | same |

**How it converts**
1. Discovers the target's files (without following symlinks) and parses them (YAML frontmatter, JSON/JSONC).
2. Translates native fields to the canonical vocabulary (§5); e.g. `alwaysApply: true` → `always: true`.
3. A monolithic file (`CLAUDE.md`/`AGENTS.md`) is imported as **a single root rule**
   (`always: true`); it does not try to split it into smaller rules.
4. Files already generated by `pyaisync` (they carry its marker) are skipped.
5. Prints an **import report**: imported / unchanged / skipped and why.

**Conflicts**
- If an element already exists in `.ai/` (same name), or two targets bring different content
  (e.g. `CLAUDE.md` ≠ `AGENTS.md`): by default `skip` + warning; `--on-conflict rename`
  saves the new one with a `-<target>` suffix; `overwrite` only when explicitly requested.
- Identical content is reported as unchanged.

**After importing** (recommended flow)
```
pyaisync import claude --dry-run     # review
pyaisync import claude               # writes .ai/
pyaisync generate --dry-run          # the diff should be almost empty
```
Quality bar: `import` followed by `generate` must reproduce the original configuration
**without semantic loss** for the supported features (cosmetic formatting diffs are accepted and documented).

**Known limits**
- It does not import user-level global configuration (`~/.claude`, etc.).
- Hooks/permissions/agents with constructs that have no canonical equivalent are reported as skipped; anything that could widen access (an allow rule, a tool allowlist) is skipped rather than approximated. See `docs/formats` ("Import mapping").
- There is no continuous sync: `import` is a one-off migration, not a way of working.

### 9.3 `pyaisync plugin claude`

Builds a Claude Code plugin directory from `.ai/` (requires a `[plugin]` table in `config.toml`:
`name` (kebab-case, not reserved), and optionally `version`, `description`, `author`, `license`).

```
dist/<name>/
  .claude-plugin/plugin.json
  commands/  agents/  skills/<n>/
  hooks/hooks.json            # {"hooks": {...}}
  .mcp.json                   # {"mcpServers": {...}}
  .pyaisync-plugin.json       # ownership manifest (same hash rules as .ai/.manifest.json)
```
- Same non-destructive rules as `generate`: it only touches files it created, refuses hand-edited or foreign files
  (`--force` / `--adopt`), removes components that disappeared from `.ai/`, and never writes over the repository itself.
- Not packageable, and reported as such: project **rules** (a `CLAUDE.md` in a plugin is not loaded) and **permissions**.
- Hook commands are copied verbatim and scripts are **not** bundled; the command warns about it.

## 10. Package layout and use with uv

```
pyaisync/
  pyproject.toml          # requires-python >=3.11, deps: pyyaml; [project.scripts] pyaisync
  uv.lock
  src/pyaisync/
    __init__.py
    cli.py                # argparse (stdlib): init, generate, check, list, import
    config.py             # config.toml
    model.py              # dataclasses
    loader.py             # reads and validates .ai/
    frontmatter.py        # Markdown+YAML parser
    safefs.py             # safe paths, no symlinks, atomic writes
    writer.py             # plan → disk, manifest, conflicts
    manifest.py
    importers/            # inverse of targets/ (common.py, structured.py: agents/mcp/hooks/permissions)
    plugin.py             # Claude Code plugin packaging
    targets/{__init__,base,claude,cursor,opencode}.py
  tests/
    test_generate.py  test_init_import.py  test_security.py  test_phase2.py  test_phase3.py
    test_import_structured.py  test_plugin.py
  docs/formats/README.md  # verified native formats
  PLAN.md
  README.md
```

Usage:
```bash
uv run pyaisync generate                      # inside the package repo
uvx --from /path/to/pyaisync pyaisync check   # from another project
uv add --dev pyaisync                         # if published or referenced by git/path
```
Dev tools: `pytest`, `ruff`, `mypy` (dev-dependencies only). The CLI uses `argparse` to avoid extra dependencies.

## 11. Testing

- **Generated output:** assertions on the exact files each target produces from a fixture `.ai/`.
- **Idempotence:** running `generate` twice → the second run changes nothing.
- **Non-destructive:** hand-edited managed file → error; foreign file → untouched.
- **Security:** names with `..`, absolute paths, symlinks → rejected.
- **Import:** round trip import → generate → check; conflicts; dry run; symlinks skipped.
- **Format contracts:** each fixture comes from the official documentation cited in
  `docs/formats/README.md` (with URL and consultation date).
- Phase 2+: JSON merge preserves user keys; golden files per target.

## 12. Phases and acceptance criteria

**Progress (2026-11-25):** Phases 0-4 ✅ (96 tests, ruff and strict mypy green).
**Documented deviation:** skills are written to one shared path (`.claude/skills/` or
`.agents/skills/`) instead of one per target, because all 3 targets read those paths (see `docs/formats`).

| Phase | Content | Acceptance criteria |
|---|---|---|
| **0. Verification** | Read the official docs of the 3 targets; write `docs/formats/*.md` with exact format, paths and version; close the ❓ in §3.2; decide the OpenCode version | §3.2 matrix without ❓; reference fixtures per target |
| **1. Core + rules/commands/skills** | loader, model, writer, manifest, `init` (§9.1), `generate`, `check`, `list`, `--dry-run`, **basic `import` (rules, commands, skills)** (§9.2) | All 3 targets generate and import rules, commands and skills; `init --from <target>` works; import→generate round trip without loss; idempotence and non-destructive tests green |
| **2. mcp + subagents** | JSON merge, `mcp.toml`, `agents/` | `opencode.json` and `.mcp.json` keep manual keys; golden output per target |
| **3. hooks + permissions** | `hooks.toml`, `permissions.toml`, canonical vocabulary, "unsupported" warnings | Hooks only where declarative ones exist; prominent summary of generated commands |
| **4. full import + plugins** | `import` of agents, mcp, hooks and permissions (report of what cannot be mapped); Claude plugin packaging | Import→generate round trip in fixtures for every supported feature |

Each phase leaves the package usable. Between phases a short security review (the points in §8) is done before continuing.

## 13. Decisions (status)

1. **Package name:** `pyaisync`. Other projects named *agentsync* / *rulesync* exist on PyPI/npm/crates;
   `pyaisync` was **free on PyPI** (checked 2026-11-25: 404); reserve it on first publish.
2. **Location:** independent git repo at `~/Desktop/ABDIEL/ISMAEL/pyaisync/`, inside `ISMAEL/`
   but outside `concept-network-scraping`.
3. **Source format:** Markdown+YAML for narrative and TOML for structured data (§5).
4. **OpenCode version to support:** **V2 only** (decided in Phase 2). The V1 schema is not generated.
5. **Minimum Python:** 3.11 (because of `tomllib`).
6. **License:** MIT.
7. **Scope:** all phases done. Future work (not planned): more targets, Cursor/OpenCode plugin packaging, JSONC merge.

## 14. Risks

- **Formats change fast** (Rulesync has 43 major versions in 17 months). Mitigation: few targets,
  versioned format docs in the repo, tests that fail visibly when a format is updated.
- **The three tools are not equivalent** (e.g. hooks in OpenCode). Mitigation: explicit
  "unsupported + warning" policy; `pyaisync list` shows the real matrix.
- **Temptation to grow** towards 60 targets. Mitigation: the `Target` interface is small,
  but the policy is to add a target only if someone uses it and can maintain its fixtures.
