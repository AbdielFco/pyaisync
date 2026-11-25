from __future__ import annotations

from typing import Any

from .. import frontmatter
from ..model import Agent, McpServer, Model, Rule
from ..vocab import WRITE_TOOLS, translate_env
from .base import HEADER, JsonMerge, Output, Plan, text

_ENV = "${env:{name}}"  # Cursor interpolation syntax


def _rule_meta(r: Rule) -> dict[str, Any]:
    # Cursor's table: alwaysApply=true ignores globs/description.
    if r.always:
        return {"alwaysApply": True}
    meta: dict[str, Any] = {}
    if r.description:
        meta["description"] = r.description
    if r.globs:
        meta["globs"] = ",".join(r.globs)
    meta["alwaysApply"] = False
    return meta


def _agent_file(a: Agent, warnings: list[str]) -> Output:
    meta: dict[str, Any] = {"name": a.name}
    if a.description:
        meta["description"] = a.description
    if a.model != "inherit":
        meta["model"] = a.model
        warnings.append(f"agent '{a.name}': model '{a.model}' is passed verbatim to Cursor")
    if a.tools is not None:
        # Cursor subagents have no tool allowlist; the closest concept is `readonly`.
        if not WRITE_TOOLS & set(a.tools):
            meta["readonly"] = True
        warnings.append(
            f"agent '{a.name}': Cursor has no tool allowlist; only 'readonly' is derived from tools"
        )
    return text(f".cursor/agents/{a.name}.md", frontmatter.dump(meta, f"{HEADER}\n\n{a.body}"))


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
    entry = {"url": tr(s.url)}  # Cursor detects HTTP vs SSE from the endpoint
    if s.headers:
        entry["headers"] = {k: tr(v) for k, v in s.headers}
    return entry


class Cursor:
    name = "cursor"
    supported = frozenset({"rules", "commands", "skills", "subagents", "mcp"})

    def plan(self, model: Model) -> Plan:
        m = model.for_target(self.name)
        feats = model.config.features
        outs: list[Output] = []
        warnings: list[str] = []
        merges: list[JsonMerge] = []
        if feats["rules"]:
            for r in sorted(m.rules, key=lambda x: x.name):
                outs.append(
                    text(
                        f".cursor/rules/{r.name}.mdc",
                        frontmatter.dump(_rule_meta(r), f"{HEADER}\n\n{r.body}"),
                    )
                )
        if feats["commands"]:
            for c in sorted(m.commands, key=lambda x: x.name):
                # Cursor commands are plain Markdown (no frontmatter).
                body = c.body if not c.description else f"{c.description}\n\n{c.body}"
                outs.append(text(f".cursor/commands/{c.name}.md", f"{HEADER}\n\n{body}\n"))
        if feats["subagents"]:
            for a in sorted(m.agents, key=lambda x: x.name):
                outs.append(_agent_file(a, warnings))
        if feats["mcp"] and m.mcp_servers:
            entries = {s.name: _server(s) for s in sorted(m.mcp_servers, key=lambda x: x.name)}
            merges.append(JsonMerge(".cursor/mcp.json", ("mcpServers",), entries))
        return Plan(outs, warnings, merges)
