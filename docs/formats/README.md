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

## Phase 2 — subagents and MCP (verified 2026-11-25)

### Claude Code — https://code.claude.com/docs/en/sub-agents , /mcp
- Subagents: `.claude/agents/<n>.md`; frontmatter `name` and `description` (both required), `tools`
  (comma-separated string or YAML list, inherits everything when omitted), `model` (`sonnet`, `opus`,
  `haiku`, a full model ID, or `inherit`). Tool names: `Read`, `Grep`, `Glob`, `Edit`, `Write`, `Bash`,
  `WebFetch`, `WebSearch`.
- MCP: project-scoped `.mcp.json` with a top-level `mcpServers` object. stdio entries: `type`, `command`,
  `args`, `env`; remote: `type` (`http` | `sse`), `url`, `headers`. An entry with a `url` but no `type` is read
  as stdio and is a configuration error, so `type` is always emitted. `${VAR}` is expanded.
  Claude Code asks the user to approve project-scoped servers on first use.

### Cursor — https://cursor.com/docs/subagents , /mcp
- Subagents: `.cursor/agents/<n>.md`; frontmatter `name`, `description`, `model` (`inherit` or a model ID),
  `readonly` (bool), `is_background` (bool). There is **no tool allowlist**: `pyaisync` derives `readonly: true`
  only when the canonical `tools` contain neither `edit` nor `shell`, and warns otherwise.
  `.cursor/agents` takes precedence over `.claude/agents` when names collide.
- MCP: `.cursor/mcp.json` with a top-level `mcpServers` object. stdio: `command`, `args`, `env`; remote:
  `url`, `headers`. Interpolation syntax is `${env:NAME}`.

### OpenCode (V2 docs) — https://opencode.ai/docs/agents , /mcp-servers
- Subagents: `.opencode/agents/<n>.md`; frontmatter `description`, `mode` (`subagent`), `model`
  (`provider/model[#variant]`), `permissions` (ordered list of `{action, resource, effect}`; **the last matching
  rule wins**). The Markdown body is the system prompt. `pyaisync` maps `tools` to a leading
  `{action: "*", resource: "*", effect: deny}` followed by `allow` rules for the listed tools.
- MCP: `mcp.servers.<name>` in `opencode.json`. Local: `type: "local"`, `command` (array: executable +
  args), `environment`. Remote: `type: "remote"`, `url`, `headers`. Only Streamable HTTP is offered, so
  `sse` servers are skipped with a warning. Substitution syntax is `{env:NAME}`.
- **Decision:** `pyaisync` supports the **V2** schema only (`mcp.servers`, `permissions` list). The V1
  schema (servers directly under `mcp`, `tools` map) is not generated.
- Config files with comments (`.jsonc`) cannot be merged; `pyaisync` refuses instead of rewriting them.

## Canonical vocabulary (source `.ai/agents/*.md`)
`tools: [read, grep, glob, edit, shell, web]`

| canonical | Claude Code | OpenCode actions |
|---|---|---|
| read | Read | read |
| grep | Grep | grep |
| glob | Glob | glob |
| edit | Edit, Write | edit |
| shell | Bash | shell |
| web | WebFetch, WebSearch | webfetch, websearch |

Env references in `.ai/mcp.toml` are written as `${VAR}` and translated per target
(`${VAR}` Claude, `${env:VAR}` Cursor, `{env:VAR}` OpenCode).

## Still to verify (Phase 3)
hooks and permissions for the 3 targets.
