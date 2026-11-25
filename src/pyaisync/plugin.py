"""Package the `.ai/` source as a Claude Code plugin directory.

Layout (https://code.claude.com/docs/en/plugins-reference): `.claude-plugin/plugin.json` plus
`commands/`, `agents/`, `skills/`, `hooks/hooks.json` and `.mcp.json` at the plugin root.
What a plugin cannot carry (project rules, permissions) is reported, never silently dropped.
"""

from __future__ import annotations

import json
from dataclasses import replace
from typing import Any

from .errors import PyAisyncError
from .model import Model
from .targets import build_plan
from .targets.base import Output, Plan

PLUGIN_MANIFEST = ".pyaisync-plugin.json"  # ownership manifest, kept at the plugin root

_MOVES = (
    (".claude/commands/", "commands/"),
    (".claude/agents/", "agents/"),
    (".claude/skills/", "skills/"),
)


def _json(value: Any) -> bytes:
    return (json.dumps(value, indent=2, ensure_ascii=False) + "\n").encode()


def build_claude_plugin(model: Model) -> Plan:
    meta = model.config.plugin
    if meta is None:
        raise PyAisyncError("add a [plugin] table (with at least 'name') to .ai/config.toml")
    claude_only = replace(model, config=replace(model.config, targets=("claude",)))
    base = build_plan(claude_only, ("claude",))
    warnings = list(base.warnings)
    outputs: list[Output] = []

    for out in base.outputs:
        if out.path == "CLAUDE.md":
            warnings.append(
                "rules are not part of a plugin (a CLAUDE.md in a plugin is not loaded); "
                "put instructions in a skill instead"
            )
            continue
        for src, dst in _MOVES:
            if out.path.startswith(src):
                outputs.append(Output(dst + out.path[len(src) :], out.content, out.executable))
                break

    hooks: dict[str, Any] = {}
    for merge in base.merges:
        if merge.path == ".mcp.json" and isinstance(merge.entries, dict):
            outputs.append(Output(".mcp.json", _json({"mcpServers": merge.entries})))
        elif merge.key_path[:1] == ("hooks",):
            hooks[merge.key_path[1]] = merge.entries
        elif merge.key_path[:1] == ("permissions",) and not any(
            "permissions" in w for w in warnings
        ):
            warnings.append("permissions are not part of a plugin (they live in settings); skipped")
    if hooks:
        outputs.append(Output("hooks/hooks.json", _json({"hooks": dict(sorted(hooks.items()))})))
        warnings.append(
            "hook commands are copied verbatim; scripts are NOT bundled. Reference bundled files "
            'with "${CLAUDE_PLUGIN_ROOT}/..." and add them to the plugin yourself'
        )

    manifest: dict[str, Any] = {"name": meta.name, "version": meta.version}
    if meta.description:
        manifest["description"] = meta.description
    if meta.author:
        manifest["author"] = {"name": meta.author}
    if meta.license:
        manifest["license"] = meta.license
    outputs.append(Output(".claude-plugin/plugin.json", _json(manifest)))
    return Plan(sorted(outputs, key=lambda o: o.path), warnings, [], base.notices)
