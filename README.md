# pyaisync

One source (`.ai/`) for the AI coding agent configuration of a repository.
`pyaisync` transpiles it into the native files of **Claude Code**, **Cursor** and **OpenCode**.

## Installation

**Requirements:** Python 3.11 or newer and [`uv`](https://docs.astral.sh/uv/). `pyaisync` is not on PyPI yet, so it is
installed from GitHub. Its only runtime dependency is `PyYAML`.

### Add it to your project (recommended)

Install it as a **development dependency** of the Python project whose AI configuration you want to manage:

```bash
uv add --dev git+https://github.com/AbdielFco/pyaisync
uv run pyaisync --version
```

This records it in your `pyproject.toml` (`dependency-groups.dev` plus a `tool.uv.sources` entry) and **pins the exact
commit in `uv.lock`**, so everyone on the team and your CI get the same version after `uv sync`. Commit both files.
To pin a release instead of the latest commit, add a tag or commit: `uv add --dev git+https://github.com/AbdielFco/pyaisync --rev <tag-or-commit>`.
To update: `uv lock --upgrade-package pyaisync && uv sync`.

Then use it as `uv run pyaisync ...` (see the quick start below).

<details>
<summary>Other ways to install</summary>

- **Without installing anything:** `uvx --from git+https://github.com/AbdielFco/pyaisync pyaisync --help`
- **As a global command:** `uv tool install git+https://github.com/AbdielFco/pyaisync` (then run `pyaisync ...` directly;
  update with `uv tool upgrade pyaisync`, remove with `uv tool uninstall pyaisync`).
- **With pip / a requirements file:** `pip install git+https://github.com/AbdielFco/pyaisync.git`, or add
  `pyaisync @ git+https://github.com/AbdielFco/pyaisync.git` to `requirements-dev.txt`.
- **From a clone (to work on pyaisync itself):** `git clone https://github.com/AbdielFco/pyaisync.git && cd pyaisync && uv sync`.

</details>

## Getting started

`pyaisync` has one source of truth, the `.ai/` folder. You edit `.ai/`; `generate` writes the files each tool reads
(`CLAUDE.md`, `.cursor/`, `AGENTS.md`, `.mcp.json`, ...). Never edit those generated files by hand.

```mermaid
flowchart TD
    A{"Does the repo already have AI config?<br/>(CLAUDE.md, .cursor/, opencode.json, .mcp.json, hooks...)"}
    A -- "No: new project" --> N1["uv run pyaisync init"]
    N1 --> N2["edit the examples in .ai/<br/>and add what you need"]
    A -- "Yes" --> E1["uv run pyaisync init --from claude<br/>(or cursor / opencode)"]
    E1 --> E2["import the other tools you use:<br/>uv run pyaisync import cursor"]
    E2 --> E3["read the warnings: they list<br/>everything that was NOT imported"]
    N2 --> G1
    E3 --> G1["uv run pyaisync generate --dry-run<br/>(preview)"]
    G1 --> G2["uv run pyaisync generate<br/>(first time on existing files: add --adopt)"]
    G2 --> G3["commit .ai/ and the generated files"]
    G3 --> C["CI / pre-commit: uv run pyaisync check<br/>fails if generated files drifted"]
    G3 --> D["later changes: edit .ai/ -> generate"]
```

### A. New project

```bash
uv run pyaisync init                 # creates .ai/ with an example rule and command
# edit .ai/rules/*.md, add .ai/commands/*.md, .ai/agents/*.md, .ai/mcp.toml, ...
uv run pyaisync generate --dry-run   # preview
uv run pyaisync generate             # writes CLAUDE.md, .cursor/, AGENTS.md, ...
```

### B. A repo that already has configuration

```bash
uv run pyaisync init --from claude   # creates .ai/ and imports CLAUDE.md, .claude/*, .mcp.json, settings hooks/permissions
uv run pyaisync import cursor        # repeat for every other tool you use (cursor, opencode)
uv run pyaisync generate --dry-run   # shows what will be created and which of your entries get replaced
uv run pyaisync generate --adopt     # first time only: take over your existing files (it refuses without --adopt)
```

`import` never changes your files; it only writes to `.ai/`, and it **tells you everything it could not carry over**.
After the first `generate --adopt`, the generated files are owned by pyaisync: edit `.ai/` from then on. Your own
entries in shared JSON files (`.mcp.json`, `.claude/settings.json`, `opencode.json`, ...) are never touched.

### What goes where in `.ai/`

| You want to define | Put it in | It becomes |
|---|---|---|
| Project instructions | `.ai/rules/*.md` | `CLAUDE.md`, `AGENTS.md`, `.cursor/rules/*.mdc` |
| Prompt shortcuts | `.ai/commands/*.md` | `.claude/commands/`, `.cursor/commands/`, `.opencode/commands/` |
| Subagents | `.ai/agents/*.md` | `.claude/agents/`, `.cursor/agents/`, `.opencode/agents/` |
| Skills (with their files) | `.ai/skills/<name>/SKILL.md` | `.claude/skills/<name>/` (all three tools read it) |
| MCP servers | `.ai/mcp.toml` | `.mcp.json`, `.cursor/mcp.json`, `opencode.json` |
| Hooks (when and what runs) | `.ai/hooks.toml` | `.claude/settings.json`, `.cursor/hooks.json` |
| **Hook scripts** (`gates.py`, `format.sh`, ...) | `.ai/hooks/` | **not copied**: hooks run them in place, see below |
| Permissions | `.ai/permissions.toml` | `.claude/settings.json`, `.cursor/cli.json`, `opencode.json` |

### Hook scripts

A hook is a **command**; `.ai/hooks.toml` says when it runs, and the script it runs lives in `.ai/hooks/`:

```toml
# .ai/hooks.toml
[[hooks]]
event = "before_shell_execution"      # Cursor-only event: use `pattern` (a regex) to filter
pattern = "git push|rm "
command = "python3 .ai/hooks/gates.py"
fail_closed = true                    # Cursor: block the action if the hook itself fails
timeout = 10
```

The script stays in `.ai/hooks/gates.py` and every tool runs it from there: Cursor from the project root, and for
Claude Code `pyaisync` writes `"${CLAUDE_PROJECT_DIR}"/.ai/hooks/gates.py` so it works from any directory.
Nothing is copied, so there is no second version to drift.

When you **import** hooks, the scripts their commands run (from `.cursor/hooks/`, `.claude/hooks/`, `.opencode/` or `hooks/`)
are **copied** into `.ai/hooks/` (permissions kept) and the commands are rewritten to point at the copies. Your original files
are left alone; delete them when you are satisfied. If a script needs other files from its folder (helpers, config),
`import` lists them: copy those to `.ai/hooks/` yourself.

### Day to day

```bash
# edit anything in .ai/, then:
uv run pyaisync generate
git add -A && git commit
```

The plan this project implements is [PLAN.md](PLAN.md).

## How it works

```mermaid
flowchart LR
    subgraph SRC[".ai/ (single source, committed)"]
        direction TB
        S1["config.toml"]
        S2["rules, commands, agents<br/>(Markdown + frontmatter)"]
        S3["skills/ (SKILL.md)"]
        S4["mcp, hooks, permissions<br/>(TOML)"]
    end

    SRC -->|"loader: parse + validate"| M["Model<br/>(frozen dataclasses)"]
    M --> A{"adapter per target"}
    A --> C["Claude Code"]
    A --> U["Cursor"]
    A --> O["OpenCode"]
    C --> P["Plan<br/>files + JSON merges<br/>+ warnings + hook notices"]
    U --> P
    O --> P
    P --> W{"writer<br/>(manifest + hashes)"}
    W -->|"generate"| D["native files on disk"]
    W -->|"generate --dry-run / check"| R["diff / drift report<br/>(exit 1 on drift)"]

    N["native config<br/>(CLAUDE.md, .mcp.json, ...)"] -->|"import"| SRC
    SRC -->|"plugin claude"| G["dist/&lt;name&gt;/<br/>Claude Code plugin"]
```

What the writer does with each file it is about to write (any conflict aborts **before** anything is written):

```mermaid
flowchart TD
    F["planned file or JSON entry"] --> X{"exists on disk?"}
    X -- no --> CR["create"]
    X -- yes --> Q{"same content?"}
    Q -- yes --> UN["unchanged<br/>(becomes owned)"]
    Q -- no --> MG{"in the manifest?"}
    MG -- "no (not ours)" --> AD{"--adopt or --force?"}
    AD -- yes --> UP["update"]
    AD -- no --> CF["CONFLICT<br/>nothing is written"]
    MG -- yes --> HE{"disk still matches<br/>the recorded hash?"}
    HE -- yes --> UP
    HE -- "no (hand edited)" --> FO{"--force?"}
    FO -- yes --> UP
    FO -- no --> CF
```

Typical workflow: `init` (or `init --from <tool>` to migrate an existing setup) -> edit `.ai/` -> `generate` ->
commit both; `check` in CI or a pre-commit hook fails when the generated files drift from `.ai/`.

## Usage

The examples below use `uv run pyaisync`, which works when it is a dependency of your project. If you installed it
as a global command (`uv tool install`), drop the `uv run` prefix.

```bash
uv run pyaisync init                  # create .ai/ (detects existing targets)
uv run pyaisync init --from claude    # init + import CLAUDE.md, .claude/commands and skills
uv run pyaisync generate --dry-run    # preview what would change
uv run pyaisync generate              # write the native files
uv run pyaisync check                 # CI / pre-commit: fails on drift
uv run pyaisync list                  # support matrix
uv run pyaisync import cursor         # bring Cursor's native config into .ai/
uv run pyaisync plugin claude         # package .ai/ as a Claude Code plugin (dist/<name>)
```

From another project: `uvx --from /path/to/pyaisync pyaisync check`.

## What it generates

| Source `.ai/` | Claude Code | Cursor | OpenCode |
|---|---|---|---|
| `rules/*.md` | `CLAUDE.md` | `.cursor/rules/<n>.mdc` | `AGENTS.md` |
| `commands/*.md` | `.claude/commands/<n>.md` | `.cursor/commands/<n>.md` | `.opencode/commands/<n>.md` |
| `skills/<n>/` | `.claude/skills/<n>/` (all 3 read it) | ← same | ← same |
| `agents/*.md` | `.claude/agents/<n>.md` | `.cursor/agents/<n>.md` | `.opencode/agents/<n>.md` |
| `mcp.toml` | `.mcp.json` (`mcpServers`) | `.cursor/mcp.json` (`mcpServers`) | `opencode.json` (`mcp.servers`) |
| `hooks.toml` | `.claude/settings.json` (`hooks`) | `.cursor/hooks.json` | not supported (warning) |
| `permissions.toml` | `.claude/settings.json` (`permissions`) | `.cursor/cli.json` (CLI only, no `ask`) | `opencode.json` (`permissions`) |

If Claude is not among the targets, skills go to `.agents/skills/` (read by Cursor and OpenCode).

## Subagents and MCP

```markdown
<!-- .ai/agents/reviewer.md -->
---
description: Read-only code reviewer
tools: [read, grep, glob]      # read, grep, glob, edit, shell, web (omit = inherit all)
---
You are a strict reviewer. Do not modify files.
```

```toml
# .ai/mcp.toml
[servers.docs]
type = "http"                  # stdio | http | sse
url = "https://example.com/mcp"
headers = { Authorization = "Bearer ${DOCS_TOKEN}" }   # secrets only as ${VAR}
```

MCP entries are **merged** into `.mcp.json`, `.cursor/mcp.json` and `opencode.json`: your own entries
are kept, and only the ones pyaisync generated are updated or removed. Files with comments (JSONC)
are refused instead of rewritten. OpenCode support targets the **V2** config schema.

## Hooks and permissions

```toml
# .ai/hooks.toml  — hooks run COMMANDS on your machine; review them like code
[[hooks]]
event = "pre_tool_use"
matcher = ["shell"]                # read, grep, glob, edit, shell, web
command = "./scripts/guard.sh"     # one line, relative to the repository root

# .ai/permissions.toml
allow = ["shell(uv run pytest*)"]
ask   = ["shell(git push*)"]
deny  = ["read(.env*)", "shell(rm -rf*)"]
```

Besides the events both tools share (`session_start`, `session_end`, `pre_tool_use`, `post_tool_use`,
`post_tool_use_failure`, `user_prompt_submit`, `stop`, `subagent_start`, `subagent_stop`, `pre_compact`), there are
Claude-only (`notification`) and Cursor-only events (`before_shell_execution`, `after_shell_execution`, `before_read_file`,
`after_file_edit`, `before_mcp_execution`, `after_mcp_execution`, `after_agent_response`, `after_agent_thought`).
An event that only one tool has is skipped, with a warning, for the other.

`generate` and `check` print every hook command that will run. Rules or hooks a target cannot express
(Cursor has no `ask`; OpenCode has no declarative hooks) are skipped **with a warning**, never approximated.
Your own entries in `.claude/settings.json`, `.cursor/hooks.json`, etc. are preserved.

## Import

`import <claude|cursor|opencode>` reads the native files and writes only to `.ai/`: rules, commands, skills,
**subagents, MCP servers, hooks and permissions**. It reports everything it cannot map, never widens access
(an allow rule or tool allowlist that cannot be mapped exactly is skipped) and **never copies a literal secret**:
sensitive values become `${VAR}` references and the warning tells you which variable to set. Entries pyaisync generated
itself are not re-imported. Conflicts: `--on-conflict skip|rename|overwrite`.

## Claude Code plugin

```toml
# .ai/config.toml
[plugin]
name = "my-tools"          # kebab-case; names starting with claude-/anthropic- are reserved
version = "1.0.0"
description = "Team tools"
```

`pyaisync plugin claude` writes `dist/my-tools/` (`.claude-plugin/plugin.json`, `commands/`, `agents/`, `skills/`,
`hooks/hooks.json`, `.mcp.json`). Rules and permissions cannot be part of a plugin and hook scripts are not bundled;
both are reported. Same non-destructive rules as `generate`.

## Scope and limitations

### In scope
- **Targets:** Claude Code, Cursor and OpenCode (V2 config schema), at **project level** only.
- **Features:** rules, commands, skills, subagents, MCP servers, hooks, permissions; `import` of all of them; a
  Claude Code plugin package.
- **Guarantees:** deterministic output, no network access, nothing from your content is ever executed, and files it did
  not generate are never overwritten.

### Not in scope
- More targets (Windsurf, Copilot, Gemini, ...). Adding one means writing an adapter and an importer.
- User-level/global configuration (`~/.claude`, `~/.cursor`, `~/.config/opencode`).
- Downloading rules or skills from remote repositories.
- Running agents, hooks or MCP servers, or two-way/continuous sync (`import` is a one-off migration).
- Cursor and OpenCode plugin packaging. OpenCode plugins are JS/TS code, not configuration; Cursor's plugin format
  was not verified. `plugin` exists for Claude Code only.

### Differences between targets
Features a target cannot express are skipped **with a warning**, never approximated:

| Feature | Limitation |
|---|---|
| Hooks | OpenCode has no declarative hooks (skipped). Cursor cannot match `glob`/`web` tools (that hook is skipped for Cursor). Events that exist in one tool only are skipped for the other. `fail_closed` and `pattern` are Cursor-only (`fail_closed` is warned about for Claude). Cursor's `loop_limit`, prompt hooks and Tab hooks are not imported. |
| Permissions | Cursor's apply to the **Cursor CLI only** (`.cursor/cli.json`) and have no `ask`. Claude cannot express path rules for `grep`/`glob`. |
| Subagents | Cursor has no tool allowlist: only `readonly` is derived from `tools`. A non-`inherit` `model` is passed through verbatim and warned about, because model names differ per tool. |
| MCP | OpenCode V2 offers only Streamable HTTP, so `sse` servers are skipped for it. |
| Rules | Claude and OpenCode read a single instructions file, so scoped rules (`globs`/`description`) become labelled sections instead of being conditionally applied. |
| Plugin | Cannot carry project rules (a `CLAUDE.md` in a plugin is not loaded) or permissions. Hook scripts are not bundled. |

### Known technical limits
- **JSONC is refused.** `opencode.json`, `.mcp.json`, `settings.json` and similar files that contain comments are not
  merged (that would destroy the comments). Remove the comments or manage those files by hand.
- **Hook scripts' neighbours.** `import` copies the scripts a hook command names, not the other files they may read.
- **JSON formatting.** A shared JSON file is rewritten with 2-space indentation, only when one of its entries changes.
- **List items are matched by content.** If you edit by hand a hook or permission that pyaisync added, it stops being
  recognised as generated: it is treated as yours and the regenerated item is added next to it.
- **OpenCode V1** config (`mcp.<name>`, `permission`, `tools`) is neither generated nor imported.
- **Import is best effort and conservative.** It skips what it cannot map exactly (regex hook matchers, non-command
  hooks, `disallowedTools`, order-dependent OpenCode rules, WebSocket MCP servers) and reports it. It does not split a
  large `CLAUDE.md`/`AGENTS.md`: it becomes one always-on rule.
- **Formats move fast.** The native formats were checked against the official docs on 2026-11-25. The one entry not
  confirmed in the docs is Cursor's `.cursor/commands/*.md`; see `docs/formats/README.md` before relying on it.
- **Python 3.11+** is required (`tomllib`).

## Safety

- No network access; nothing from the content is ever executed.
- Never overwrites files it did not generate (manifest with hashes in `.ai/.manifest.json`).
- Rejects names containing `..`, absolute paths and symlinks.
- Rejects literal secrets in `mcp.toml` (sensitive-looking keys must use `${VAR}`).

Development: `uv run pytest`, `uv run ruff check .`, `uv run mypy`. Contributors and AI agents: see
[AGENTS.md](AGENTS.md) and [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).
