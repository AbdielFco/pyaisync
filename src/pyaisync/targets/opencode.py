from __future__ import annotations

from typing import Any

from .. import frontmatter
from ..model import Agent, McpServer, Model, PermissionSet
from ..vocab import OPENCODE_ACTIONS, parse_rule, translate_env
from .base import HEADER, JsonMerge, Output, Plan, command_meta, rules_document, text

_ENV = "{env:{name}}"  # OpenCode substitution syntax


def _agent_file(a: Agent, warnings: list[str]) -> Output:
    meta: dict[str, Any] = {}
    if a.description:
        meta["description"] = a.description
    meta["mode"] = "subagent"
    if a.model != "inherit":
        meta["model"] = a.model
        warnings.append(
            f"agent '{a.name}': model '{a.model}' is passed verbatim to OpenCode "
            "(expects provider/model)"
        )
    if a.tools is not None:
        # The last matching rule wins: deny everything, then allow the listed tools.
        rules: list[dict[str, str]] = [{"action": "*", "resource": "*", "effect": "deny"}]
        for tool in a.tools:
            for action in OPENCODE_ACTIONS[tool]:
                rules.append({"action": action, "resource": "*", "effect": "allow"})
        meta["permissions"] = rules
    return text(f".opencode/agents/{a.name}.md", frontmatter.dump(meta, f"{HEADER}\n\n{a.body}"))


def _server(s: McpServer) -> dict[str, Any] | None:
    def tr(value: str) -> str:
        return translate_env(value, _ENV)

    if s.type == "stdio":
        entry: dict[str, Any] = {
            "type": "local",
            "command": [tr(s.command), *[tr(a) for a in s.args]],
        }
        if s.env:
            entry["environment"] = {k: tr(v) for k, v in s.env}
        return entry
    if s.type == "sse":
        return None  # OpenCode V2 only offers Streamable HTTP
    entry = {"type": "remote", "url": tr(s.url)}
    if s.headers:
        entry["headers"] = {k: tr(v) for k, v in s.headers}
    return entry


def _permission_items(perms: PermissionSet) -> list[dict[str, str]]:
    """Ordered rules; the last matching rule wins, so deny comes last."""
    items: list[dict[str, str]] = []
    for effect in ("allow", "ask", "deny"):
        for rule in getattr(perms, effect):
            tool, pattern = parse_rule(rule, "permissions")
            for action in OPENCODE_ACTIONS[tool]:
                items.append({"action": action, "resource": pattern or "*", "effect": effect})
    return items


class OpenCode:
    name = "opencode"
    supported = frozenset({"rules", "commands", "skills", "subagents", "mcp", "permissions"})

    def plan(self, model: Model) -> Plan:
        m = model.for_target(self.name)
        feats = model.config.features
        outs: list[Output] = []
        warnings: list[str] = []
        merges: list[JsonMerge] = []
        if feats["rules"] and m.rules:
            outs.append(text("AGENTS.md", rules_document(m.rules)))
        if feats["commands"]:
            for c in sorted(m.commands, key=lambda x: x.name):
                outs.append(
                    text(
                        f".opencode/commands/{c.name}.md",
                        frontmatter.dump(command_meta(c), f"{HEADER}\n\n{c.body}"),
                    )
                )
        if feats["subagents"]:
            for a in sorted(m.agents, key=lambda x: x.name):
                outs.append(_agent_file(a, warnings))
        if feats["mcp"]:
            entries: dict[str, Any] = {}
            for s in sorted(m.mcp_servers, key=lambda x: x.name):
                entry = _server(s)
                if entry is None:
                    warnings.append(
                        f"mcp server '{s.name}': SSE is not supported by OpenCode; skipped"
                    )
                else:
                    entries[s.name] = entry
            if entries:
                merges.append(JsonMerge("opencode.json", ("mcp", "servers"), entries))
        if feats["hooks"]:
            for h in m.hooks:
                warnings.append(
                    f"hook {h.event} ({h.command}): OpenCode has no declarative hooks; skipped"
                )
        if feats["permissions"] and m.permissions:
            merges.append(
                JsonMerge("opencode.json", ("permissions",), _permission_items(m.permissions))
            )
        return Plan(outs, warnings, merges)
