"""Apply a Plan to disk without destroying anything (manifest + hashes)."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from . import manifest as mf
from .safefs import atomic_write, safe_join
from .targets.base import Plan


@dataclass
class Report:
    created: list[str] = field(default_factory=list)
    updated: list[str] = field(default_factory=list)
    unchanged: list[str] = field(default_factory=list)
    deleted: list[str] = field(default_factory=list)
    conflicts: list[str] = field(default_factory=list)  # messages
    warnings: list[str] = field(default_factory=list)

    @property
    def has_changes(self) -> bool:
        return bool(self.created or self.updated or self.deleted)


def _prune_empty_dirs(root: Path, path: Path) -> None:
    parent = path.parent
    root = root.resolve()
    while parent.resolve() != root and parent.is_dir() and not any(parent.iterdir()):
        parent.rmdir()
        parent = parent.parent


def apply(
    root: Path,
    plan: Plan,
    *,
    write: bool,
    force: bool = False,
    adopt: bool = False,
) -> Report:
    report = Report(warnings=list(plan.warnings))
    old = mf.load(root)
    desired = {o.path: o.content for o in plan.outputs}
    new_manifest: dict[str, str] = {}

    for rel, data in desired.items():
        target = safe_join(root, rel)
        want = mf.sha256(data)
        new_manifest[rel] = want
        if not target.exists():
            report.created.append(rel)
            continue
        have = mf.sha256(target.read_bytes())
        if have == want:
            report.unchanged.append(rel)
        elif rel in old:
            if have == old[rel] or force:
                report.updated.append(rel)
            else:
                report.conflicts.append(
                    f"{rel}: managed by pyaisync but edited by hand (use --force to overwrite)"
                )
        elif adopt or force:
            report.updated.append(rel)
        else:
            report.conflicts.append(
                f"{rel}: already exists and is not managed by pyaisync (use --adopt to take it over)"
            )

    for rel, recorded in old.items():
        if rel in desired:
            continue
        target = safe_join(root, rel)
        if not target.exists():
            continue
        if mf.sha256(target.read_bytes()) == recorded or force:
            report.deleted.append(rel)
        else:
            report.warnings.append(
                f"{rel}: is no longer generated but was edited by hand; it will not be deleted"
            )
            new_manifest[rel] = recorded

    if not write or report.conflicts:
        return report

    for rel in report.created + report.updated:
        atomic_write(safe_join(root, rel), desired[rel])
    for rel in report.deleted:
        p = safe_join(root, rel)
        os.unlink(p)
        _prune_empty_dirs(root, p)
    if new_manifest or old:
        atomic_write(root / mf.MANIFEST, mf.dumps(new_manifest))
    return report
