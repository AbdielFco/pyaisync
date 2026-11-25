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

## Phase 3 — hooks and permissions (verified 2026-11-25)

### Claude Code — https://code.claude.com/docs/en/hooks , /permissions
- Hooks live in `.claude/settings.json` under `hooks`: `{ "<Event>": [ { "matcher": "...", "hooks": [ { "type": "command",
  "command": "...", "timeout": <seconds> } ] } ] }`. A matcher made only of letters, digits, `_`, `-`, spaces, `,` and `|` is an
  exact tool name (or a `|` list); an omitted matcher matches everything. Hook commands run in the session's current directory,
  so prefer paths that do not depend on it.
- Permissions live in `.claude/settings.json` under `permissions.allow|ask|deny` as rule strings: `Bash(npm run *)`,
  `Read(./.env)`, `Edit(path)` (covers all file-editing tools), `WebFetch(domain:example.com)`. `Read` deny rules are applied
  best-effort to Grep/Glob.

### Cursor — https://cursor.com/docs/hooks , /cli/reference/permissions
- Hooks live in `.cursor/hooks.json`: `{ "version": 1, "hooks": { "<event>": [ { "command", "timeout", "matcher" } ] } }`.
  Project hooks run from the project root. For `preToolUse`/`postToolUse` the matcher is a regex over the tool type
  (`Shell`, `Read`, `Write`, `Grep`, `Delete`, `Task`, `MCP:<tool>`). There is no `Glob` or web tool name to match.
- Permissions exist **only for the Cursor CLI**, in `.cursor/cli.json` under `permissions.allow|deny` (no `ask`):
  `Shell(commandBase)` / `Shell(base:args)`, `Read(glob)`, `Write(glob)`, `WebFetch(domain)`, `Mcp(server:tool)`.
  Deny rules win over allow.

### OpenCode (V2 docs) — https://opencode.ai/docs/permissions , /plugins
- Permissions: top-level `permissions` array of `{action, resource, effect}` in `opencode.json`; **the last matching rule wins**;
  an unmatched action asks. `pyaisync` emits allow, then ask, then deny, so deny always wins.
- Hooks: OpenCode has no declarative hooks (it uses JS/TS plugins). `pyaisync` skips them with a warning.

## Canonical hook events (source `.ai/hooks.toml`)

| canonical | Claude Code | Cursor |
|---|---|---|
| session_start | SessionStart | sessionStart |
| session_end | SessionEnd | sessionEnd |
| pre_tool_use | PreToolUse | preToolUse |
| post_tool_use | PostToolUse | postToolUse |
| user_prompt_submit | UserPromptSubmit | beforeSubmitPrompt |
| stop | Stop | stop |
| subagent_start | SubagentStart | subagentStart |
| subagent_stop | SubagentStop | subagentStop |
| pre_compact | PreCompact | preCompact |

`matcher = ["shell", "edit"]` is only accepted for `pre_tool_use` / `post_tool_use`. A tool Cursor cannot match
(`glob`, `web`) makes the hook be **skipped for Cursor with a warning** rather than silently widened to every tool.

## Canonical permission rules (source `.ai/permissions.toml`)
`allow` / `ask` / `deny` lists of `tool` or `tool(pattern)`, with tool in `read grep glob edit shell web`.

| canonical | Claude Code | Cursor CLI | OpenCode |
|---|---|---|---|
| `read(p)` | `Read(p)` | `Read(p)` | `read` |
| `edit(p)` | `Edit(p)` | `Write(p)` | `edit` |
| `shell(a b*)` | `Bash(a b*)` | `Shell(a:b*)` | `shell` |
| `web(d)` | `WebFetch(domain:d)` | `WebFetch(d)` | `webfetch` + `websearch` |
| `grep` / `glob` (bare) | `Grep` / `Glob` | skipped (warning) | `grep` / `glob` |

Rules with no equivalent are skipped with a warning, never approximated.
