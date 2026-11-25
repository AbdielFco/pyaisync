# pyaisync

One source (`.ai/`) for the AI coding agent configuration of a repository.
`pyaisync` transpiles it into the native files of **Claude Code**, **Cursor** and **OpenCode**.

Status: **Phase 3** (rules, commands, skills, subagents, MCP, hooks, permissions + `init`, `generate`, `check`, `list`, `import`).
Full `import` and plugin packaging are planned (Phase 4).
See [PLAN.md](PLAN.md) for the full design and the upcoming phases.

## Usage (uv)

```bash
uv run pyaisync init                  # create .ai/ (detects existing targets)
uv run pyaisync init --from claude    # init + import CLAUDE.md, .claude/commands and skills
uv run pyaisync generate --dry-run    # preview what would change
uv run pyaisync generate              # write the native files
uv run pyaisync check                 # CI / pre-commit: fails on drift
uv run pyaisync list                  # support matrix
uv run pyaisync import cursor         # bring .cursor/rules and commands into .ai/
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

`generate` and `check` print every hook command that will run. Rules or hooks a target cannot express
(Cursor has no `ask`; OpenCode has no declarative hooks) are skipped **with a warning**, never approximated.
Your own entries in `.claude/settings.json`, `.cursor/hooks.json`, etc. are preserved.

## Safety

- No network access; nothing from the content is ever executed.
- Never overwrites files it did not generate (manifest with hashes in `.ai/.manifest.json`).
- Rejects names containing `..`, absolute paths and symlinks.
- Rejects literal secrets in `mcp.toml` (sensitive-looking keys must use `${VAR}`).

Development: `uv run pytest`, `uv run ruff check .`, `uv run mypy`.
