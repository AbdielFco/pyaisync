from __future__ import annotations

from typing import Any

from .. import frontmatter
from ..model import Agent, Hook, McpServer, Model, PermissionSet
from ..vocab import CLAUDE_TOOLS, HOOK_EVENTS, claude_matcher, parse_rule, translate_env
from .base import HEADER, JsonMerge, Output, Plan, command_meta, rules_document, text

_ENV = "${{name}}"  # Claude Code expands ${VAR} in .mcp.json


def _agent_file(a: Agent, warnings: list[str]) -> Output:
    meta: dict[str, Any] = {"name": a.name}
    if a.description:
        meta["description"] = a.description
    else:
        meta["description"] = a.name
        warnings.append(f"agent '{a.name}': Claude Code requires a description; using the name")
    if a.tools is not None:
        names = dict.fromkeys(n for t in a.tools for n in CLAUDE_TOOLS[t])
        meta["tools"] = ", ".join(names)
    if a.model != "inherit":
        meta["model"] = a.model
        warnings.append(f"agent '{a.name}': model '{a.model}' is passed verbatim to Claude Code")
    return text(f".claude/agents/{a.name}.md", frontmatter.dump(meta, f"{HEADER}\n\n{a.body}"))


def _server(s: McpServer) -> dict[str, Any]:
    def tr(value: str) -> str:
        return translate_env(value, _ENV)

    if s.type == "stdio":
        entry: dict[str, Any] = {"type": "stdio", "command": tr(s.command)}
        if s.args:
            entry["args"] = [tr(a) for a in s.args]
        if s.env:
            entry["env"] = {k: tr(v) for k, v in s.env}
        return entry
    entry = {"type": s.type, "url": tr(s.url)}
    if s.headers:
        entry["headers"] = {k: tr(v) for k, v in s.headers}
    return entry


def _hook_group(h: Hook) -> dict[str, Any]:
    handler: dict[str, Any] = {"type": "command", "command": h.command}
    if h.timeout is not None:
        handler["timeout"] = h.timeout
    group: dict[str, Any] = {}
    if h.matcher:
        group["matcher"] = claude_matcher(h.matcher)
    group["hooks"] = [handler]
    return group


def _permission_rules(perms: PermissionSet, warnings: list[str]) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {"allow": [], "ask": [], "deny": []}
    for effect in ("allow", "ask", "deny"):
        for rule in getattr(perms, effect):
            tool, pattern = parse_rule(rule, "permissions")
            if tool in ("grep", "glob"):
                if pattern is None:
                    out[effect].append(CLAUDE_TOOLS[tool][0])
                else:
                    warnings.append(
                        f"permission '{rule}': Claude Code has no path rule for {tool}; skipped "
                        "(use a 'read(...)' rule)"
                    )
            elif tool == "web":
                if pattern is None:
                    out[effect] += list(CLAUDE_TOOLS["web"])
                else:
                    out[effect].append(f"WebFetch(domain:{pattern})")
            else:
                name = {"read": "Read", "edit": "Edit", "shell": "Bash"}[tool]
                out[effect].append(name if pattern is None else f"{name}({pattern})")
    return {k: list(dict.fromkeys(v)) for k, v in out.items()}


class Claude:
    name = "claude"
    supported = frozenset(
        {"rules", "commands", "skills", "subagents", "mcp", "hooks", "permissions"}
    )

    def plan(self, model: Model) -> Plan:
        m = model.for_target(self.name)
        feats = model.config.features
        outs: list[Output] = []
        warnings: list[str] = []
        merges: list[JsonMerge] = []
        if feats["rules"] and m.rules:
            outs.append(text("CLAUDE.md", rules_document(m.rules)))
        if feats["commands"]:
            for c in sorted(m.commands, key=lambda x: x.name):
                body = f"{HEADER}\n\n{c.body}"
                outs.append(
                    text(f".claude/commands/{c.name}.md", frontmatter.dump(command_meta(c), body))
                )
        if feats["subagents"]:
            for a in sorted(m.agents, key=lambda x: x.name):
                outs.append(_agent_file(a, warnings))
        if feats["mcp"] and m.mcp_servers:
            entries = {s.name: _server(s) for s in sorted(m.mcp_servers, key=lambda x: x.name)}
            merges.append(JsonMerge(".mcp.json", ("mcpServers",), entries))
        notices: list[str] = []
        if feats["hooks"] and m.hooks:
            groups: dict[str, list[Any]] = {}
            for h in m.hooks:
                native = HOOK_EVENTS[h.event][0]
                groups.setdefault(native, []).append(_hook_group(h))
                where = f"[{claude_matcher(h.matcher)}]" if h.matcher else ""
                notices.append(f"claude: {native}{where} runs: {h.command}")
            for native, items in sorted(groups.items()):
                merges.append(JsonMerge(".claude/settings.json", ("hooks", native), items))
        if feats["permissions"] and m.permissions:
            for effect, rules in _permission_rules(m.permissions, warnings).items():
                if rules:
                    merges.append(
                        JsonMerge(".claude/settings.json", ("permissions", effect), list(rules))
                    )
        return Plan(outs, warnings, merges, notices)
