from __future__ import annotations

from ..errors import PyAisyncError
from ..model import Model
from ..vocab import referenced_scripts
from .base import JsonMerge, Output, Plan, Target
from .claude import Claude
from .cursor import Cursor
from .opencode import OpenCode

REGISTRY: dict[str, Target] = {t.name: t for t in (Claude(), Cursor(), OpenCode())}


def build_plan(model: Model, targets: tuple[str, ...]) -> Plan:
    outputs: dict[str, Output] = {}
    merges: dict[tuple[str, tuple[str, ...]], JsonMerge] = {}
    notices: list[str] = []
    warnings: list[str] = []
    for name in targets:
        plan = REGISTRY[name].plan(model)
        warnings += plan.warnings
        for merge in plan.merges:
            key = (merge.path, merge.key_path)
            if key in merges:
                raise PyAisyncError(f"internal conflict: {merge.path} is merged twice at {key[1]}")
            merges[key] = merge
        notices += plan.notices
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
                outputs[path] = Output(path, data, rel in skill.executable)
    if model.config.features["hooks"]:
        missing = {
            f".ai/hooks/{rel}"
            for h in model.hooks
            for rel in referenced_scripts(h.command)
            if not (model.root / ".ai/hooks" / rel).is_file()
        }
        warnings += [f"hook script not found: {m}" for m in sorted(missing)]
    return Plan(
        sorted(outputs.values(), key=lambda o: o.path),
        warnings,
        sorted(merges.values(), key=lambda m: (m.path, m.key_path)),
        notices,
    )


__all__ = ["REGISTRY", "Output", "Plan", "Target", "build_plan"]
