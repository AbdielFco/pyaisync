from __future__ import annotations

from typing import Any

from .. import frontmatter
from ..model import Agent, McpServer, Model
from ..vocab import CLAUDE_TOOLS, translate_env
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


class Claude:
    name = "claude"
    supported = frozenset({"rules", "commands", "skills", "subagents", "mcp"})

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
        return Plan(outs, warnings, merges)
