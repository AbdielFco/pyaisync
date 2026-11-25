from __future__ import annotations

from typing import Any

from .. import frontmatter
from ..model import Agent, Hook, McpServer, Model, PermissionSet, Rule
from ..vocab import HOOK_EVENTS, WRITE_TOOLS, cursor_matcher, parse_rule, translate_env
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


def _permission_rules(perms: PermissionSet, warnings: list[str]) -> dict[str, list[str]]:
    """Cursor CLI permission tokens (.cursor/cli.json): only allow/deny exist."""
    out: dict[str, list[str]] = {"allow": [], "deny": []}
    if perms.ask:
        warnings.append("permissions: Cursor has no 'ask' rules; they are skipped")
    for effect in ("allow", "deny"):
        for rule in getattr(perms, effect):
            tool, pattern = parse_rule(rule, "permissions")
            if tool in ("grep", "glob"):
                warnings.append(f"permission '{rule}': Cursor has no {tool} permission; skipped")
            elif tool == "shell":
                base = (pattern or "*").strip()
                head, _, rest = base.partition(" ")
                out[effect].append(f"Shell({head}:{rest})" if rest else f"Shell({head})")
            elif tool == "web":
                out[effect].append(f"WebFetch({pattern or '*'})")
            else:
                name = {"read": "Read", "edit": "Write"}[tool]
                out[effect].append(f"{name}({pattern or '**'})")
    return {k: list(dict.fromkeys(v)) for k, v in out.items()}


def _hook_item(h: Hook, matcher: str | None) -> dict[str, Any]:
    item: dict[str, Any] = {"command": h.command}
    if h.timeout is not None:
        item["timeout"] = h.timeout
    if matcher:
        item["matcher"] = matcher
    if h.fail_closed:
        item["failClosed"] = True
    return item


class Cursor:
    name = "cursor"
    supported = frozenset(
        {"rules", "commands", "skills", "subagents", "mcp", "hooks", "permissions"}
    )

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
        notices: list[str] = []
        if feats["hooks"] and m.hooks:
            groups: dict[str, list[Any]] = {}
            for h in m.hooks:
                native = HOOK_EVENTS[h.event][1]
                if native is None:
                    warnings.append(
                        f"hook {h.event} ({h.command}): Cursor has no such event; skipped for Cursor"
                    )
                    continue
                matcher = cursor_matcher(h.matcher) if h.matcher else (h.pattern or None)
                if h.matcher and matcher is None:
                    warnings.append(
                        f"hook {h.event} {list(h.matcher)}: Cursor cannot match glob/web tools; skipped"
                    )
                    continue
                groups.setdefault(native, []).append(_hook_item(h, matcher))
                where = f"[{matcher}]" if matcher else ""
                notices.append(f"cursor: {native}{where} runs: {h.command}")
            if groups:
                merges.append(JsonMerge(".cursor/hooks.json", (), {"version": 1}))
                for native, items in sorted(groups.items()):
                    merges.append(JsonMerge(".cursor/hooks.json", ("hooks", native), items))
        if feats["permissions"] and m.permissions:
            warnings.append(
                "permissions: Cursor applies them to the Cursor CLI only (.cursor/cli.json)"
            )
            for effect, rules in _permission_rules(m.permissions, warnings).items():
                if rules:
                    merges.append(JsonMerge(".cursor/cli.json", ("permissions", effect), rules))
        return Plan(outs, warnings, merges, notices)
