# Verified formats (Phase 0)

Checked on 2026-11-25 against the official documentation. These formats change often:
if a golden test fails after a tool update, start by reviewing this file.

## Claude Code — https://code.claude.com/docs/en/skills , /memory
- Instructions: `CLAUDE.md` (it also reads `AGENTS.md`).
- Skills: `.claude/skills/<n>/SKILL.md` (frontmatter `name`, `description`, ...).
- Commands: `.claude/commands/<n>.md` still works; the docs mark it as the older format that was
  merged into skills. It is kept for compatibility and simplicity (it accepts `description`).

## Cursor — https://cursor.com/docs/rules , /skills
- Rules: `.cursor/rules/*.mdc` with `description`, `globs` (comma-separated patterns) and `alwaysApply`.
  A `.md` file in that folder is ignored. `alwaysApply: true` ignores `globs`/`description`.
- Skills: reads `.agents/skills/`, `.cursor/skills/`, and for compatibility `.claude/skills/` and `.codex/skills/`.
- It also reads `AGENTS.md`.
- Commands: `.cursor/commands/*.md` (plain Markdown). **⚠ The official page was not found in this
  check; this is the known format. Verify before relying on it.**

## OpenCode (V2 docs) — https://opencode.ai/docs/commands , /skills , /instructions
- Instructions: `AGENTS.md` (falls back to `CLAUDE.md`).
- Commands: `.opencode/commands/<n>.md` (the singular `command/` directory is legacy).
  Optional frontmatter: `description`, `agent`, `model`, `subagent`. The body is the template; `$ARGUMENTS`, `$1`, ...
- Skills: `.opencode/skills/`, and for compatibility `.claude/skills/` and `.agents/skills/`.

## Resulting design decision
Skills are an open format and all 3 targets read `.claude/skills/`; Cursor and OpenCode also read
`.agents/skills/`. `pyaisync` writes **a single copy**: `.claude/skills/` when Claude is a target
for that skill, otherwise `.agents/skills/`. This avoids duplicates and drift.

## Still to verify (Phases 2-3)
subagents, mcp, hooks, permissions for the 3 targets (OpenCode V1 and V2 differ in `mcp`).
