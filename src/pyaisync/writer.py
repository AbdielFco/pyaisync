"""Apply a Plan to disk without destroying anything (manifest + hashes)."""

from __future__ import annotations

import copy
import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import manifest as mf
from .errors import PyAisyncError
from .safefs import atomic_write, safe_join
from .targets.base import JsonMerge, Plan

_MISSING = object()


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


def _container(data: dict[str, Any], key_path: tuple[str, ...], rel: str, make: bool) -> Any:
    cur: Any = data
    for key in key_path:
        nxt = cur.get(key, _MISSING)
        if nxt is _MISSING:
            if not make:
                return _MISSING
            nxt = cur[key] = {}
        if not isinstance(nxt, dict):
            raise PyAisyncError(f"{rel}: '{'.'.join(key_path)}' is not a JSON object")
        cur = nxt
    return cur


def _prune_empty_json(data: dict[str, Any], key_path: tuple[str, ...]) -> None:
    for depth in range(len(key_path), 0, -1):
        parent = _container(data, key_path[: depth - 1], "", make=False)
        node = parent.get(key_path[depth - 1]) if parent is not _MISSING else None
        if isinstance(node, dict) and not node:
            del parent[key_path[depth - 1]]
        else:
            break


def _plan_merge(
    root: Path,
    merge: JsonMerge,
    old: mf.JsonState | None,
    report: Report,
    *,
    force: bool,
    adopt: bool,
) -> tuple[bytes | None, bool, mf.JsonState | None]:
    """Returns (new file bytes | None, delete_file, new manifest state | None)."""
    rel, kp = merge.path, merge.key_path
    target = safe_join(root, rel)
    existed = target.exists()
    data: dict[str, Any] = {}
    if existed:
        try:
            loaded = json.loads(target.read_text(encoding="utf-8"))
        except (ValueError, UnicodeDecodeError) as exc:
            raise PyAisyncError(
                f"{rel}: cannot merge into a file that is not plain JSON ({exc}); "
                "comments are not supported"
            ) from exc
        if not isinstance(loaded, dict):
            raise PyAisyncError(f"{rel}: top-level JSON value must be an object")
        data = loaded
    work = copy.deepcopy(data)
    cont = _container(work, kp, rel, make=bool(merge.entries))
    owned = dict(old.entries) if old else {}
    new_entries: dict[str, str] = {}
    changed = False

    def label(name: str) -> str:
        return f"{rel}#{'.'.join(kp)}.{name}"

    for name in sorted(merge.entries):
        value = merge.entries[name]
        want = mf.value_hash(value)
        new_entries[name] = want
        cur = cont.get(name, _MISSING) if cont is not _MISSING else _MISSING
        if cur is _MISSING:
            report.created.append(label(name))
        elif mf.value_hash(cur) == want:
            report.unchanged.append(label(name))
            continue
        elif name in owned:
            if mf.value_hash(cur) == owned[name] or force:
                report.updated.append(label(name))
            else:
                report.conflicts.append(
                    f"{label(name)}: managed by pyaisync but edited by hand "
                    "(use --force to overwrite)"
                )
                continue
        elif adopt or force:
            report.updated.append(label(name))
        else:
            report.conflicts.append(
                f"{label(name)}: already exists and is not managed by pyaisync "
                "(use --adopt to take it over)"
            )
            continue
        cont[name] = value
        changed = True

    for name, recorded in owned.items():
        if name in merge.entries or cont is _MISSING or name not in cont:
            continue
        if mf.value_hash(cont[name]) == recorded or force:
            report.deleted.append(label(name))
            del cont[name]
            changed = True
        else:
            report.warnings.append(
                f"{label(name)}: is no longer generated but was edited by hand; it will not be deleted"
            )
            new_entries[name] = recorded

    state = (
        mf.JsonState(kp, created=(old.created if old else not existed), entries=new_entries)
        if new_entries
        else None
    )
    if not changed:
        return None, False, state
    _prune_empty_json(work, kp)
    if not work and (not existed or (old is not None and old.created)):
        return None, True, state
    return (json.dumps(work, indent=2, ensure_ascii=False) + "\n").encode(), False, state


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
    new_files: dict[str, str] = {}

    for rel, data in desired.items():
        target = safe_join(root, rel)
        want = mf.sha256(data)
        new_files[rel] = want
        if not target.exists():
            report.created.append(rel)
            continue
        have = mf.sha256(target.read_bytes())
        if have == want:
            report.unchanged.append(rel)
        elif rel in old.files:
            if have == old.files[rel] or force:
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

    for rel, recorded in old.files.items():
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
            new_files[rel] = recorded

    # Shared JSON files: also visit files that were merged before but no longer are.
    merges = {m.path: m for m in plan.merges}
    for rel, prev in old.json.items():
        merges.setdefault(rel, JsonMerge(rel, prev.key_path, {}))
    json_writes: list[tuple[str, bytes | None, bool]] = []
    new_json: dict[str, mf.JsonState] = {}
    for rel, merge in sorted(merges.items()):
        content, delete, state = _plan_merge(
            root, merge, old.json.get(rel), report, force=force, adopt=adopt
        )
        if content is not None or delete:
            json_writes.append((rel, content, delete))
        if state is not None:
            new_json[rel] = state

    if not write or report.conflicts:
        return report

    for rel in report.created + report.updated:
        if rel in desired:
            atomic_write(safe_join(root, rel), desired[rel])
    for rel in report.deleted:
        if rel in old.files and rel not in desired:
            p = safe_join(root, rel)
            os.unlink(p)
            _prune_empty_dirs(root, p)
    for rel, content, delete in json_writes:
        p = safe_join(root, rel)
        if delete:
            if p.exists():
                os.unlink(p)
                _prune_empty_dirs(root, p)
        elif content is not None:
            atomic_write(p, content)
    if new_files or new_json or old.files or old.json:
        atomic_write(root / mf.MANIFEST, mf.dumps(mf.Manifest(new_files, new_json)))
    return report
