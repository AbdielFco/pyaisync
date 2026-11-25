from __future__ import annotations

from typing import Any

from .. import frontmatter
from ..model import Model, Rule
from .base import HEADER, Output, Plan, text


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


class Cursor:
    name = "cursor"
    supported = frozenset({"rules", "commands", "skills"})

    def plan(self, model: Model) -> Plan:
        m = model.for_target(self.name)
        feats = model.config.features
        outs: list[Output] = []
        warnings: list[str] = []
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
        return Plan(outs, warnings)
