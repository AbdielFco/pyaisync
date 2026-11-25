# pyaisync

One source (`.ai/`) for the AI coding agent configuration of a repository.
`pyaisync` transpiles it into the native files of **Claude Code**, **Cursor** and **OpenCode**.

Status: **Phase 1** (rules, commands, skills + `init`, `generate`, `check`, `list`, `import`).
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

If Claude is not among the targets, skills go to `.agents/skills/` (read by Cursor and OpenCode).

## Safety

- No network access; nothing from the content is ever executed.
- Never overwrites files it did not generate (manifest with hashes in `.ai/.manifest.json`).
- Rejects names containing `..`, absolute paths and symlinks.

Development: `uv run pytest`, `uv run ruff check .`, `uv run mypy`.
