from __future__ import annotations

from .. import frontmatter
from ..model import Model
from .base import HEADER, Output, Plan, command_meta, rules_document, text


class Claude:
    name = "claude"
    supported = frozenset({"rules", "commands", "skills"})

    def plan(self, model: Model) -> Plan:
        m = model.for_target(self.name)
        feats = model.config.features
        outs: list[Output] = []
        if feats["rules"] and m.rules:
            outs.append(text("CLAUDE.md", rules_document(m.rules)))
        if feats["commands"]:
            for c in sorted(m.commands, key=lambda x: x.name):
                body = f"{HEADER}\n\n{c.body}"
                outs.append(
                    text(f".claude/commands/{c.name}.md", frontmatter.dump(command_meta(c), body))
                )
        return Plan(outs, [])
