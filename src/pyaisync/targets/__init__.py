from __future__ import annotations

from ..errors import PyAisyncError
from ..model import Model
from .base import Output, Plan, Target
from .claude import Claude
from .cursor import Cursor
from .opencode import OpenCode

REGISTRY: dict[str, Target] = {t.name: t for t in (Claude(), Cursor(), OpenCode())}


def build_plan(model: Model, targets: tuple[str, ...]) -> Plan:
    outputs: dict[str, Output] = {}
    warnings: list[str] = []
    for name in targets:
        plan = REGISTRY[name].plan(model)
        warnings += plan.warnings
        for out in plan.outputs:
            prev = outputs.get(out.path)
            if prev is not None and prev.content != out.content:
                raise PyAisyncError(
                    f"internal conflict: {out.path} is generated with different content"
                )
            outputs[out.path] = out
    if model.config.features["skills"]:
        for skill in sorted(model.skills, key=lambda s: s.name):
            applicable = [t for t in targets if "*" in skill.targets or t in skill.targets]
            if not applicable:
                continue
            # Open format: all 3 targets read .claude/skills; Cursor and OpenCode
            # also read .agents/skills. A single copy, no duplicates.
            base = ".claude/skills" if "claude" in applicable else ".agents/skills"
            for rel, data in sorted(skill.files.items()):
                path = f"{base}/{skill.name}/{rel}"
                outputs[path] = Output(path, data)
    return Plan(sorted(outputs.values(), key=lambda o: o.path), warnings)


__all__ = ["REGISTRY", "Output", "Plan", "Target", "build_plan"]
