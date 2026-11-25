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
    notices: list[str] = field(default_factory=list)  # hooks: commands that will run

    @property
    def has_changes(self) -> bool:
        return bool(self.created or self.updated or self.deleted)


def _prune_empty_dirs(root: Path, path: Path) -> None:
    parent = path.parent
    root = root.resolve()
    while parent.resolve() != root and parent.is_dir() and not any(parent.iterdir()):
        parent.rmdir()
        parent = parent.parent


def _parent(data: dict[str, Any], key_path: tuple[str, ...], rel: str, make: bool) -> Any:
    """Walk to the object that holds `key_path[-1]`."""
    cur: Any = data
    for key in key_path[:-1]:
        nxt = cur.get(key, _MISSING)
        if nxt is _MISSING:
            if not make:
                return _MISSING
            nxt = cur[key] = {}
        if not isinstance(nxt, dict):
            raise PyAisyncError(f"{rel}: '{'.'.join(key_path)}' is not reachable (not an object)")
        cur = nxt
    return cur


def _prune_empty_json(data: dict[str, Any], key_path: tuple[str, ...]) -> None:
    """Remove empty containers left behind along `key_path` (never touches non-empty ones)."""
    for depth in range(len(key_path), 0, -1):
        parent = _parent(data, key_path[:depth], "", make=False)
        key = key_path[depth - 1]
        if parent is _MISSING:
            continue
        node = parent.get(key)
        if isinstance(node, (dict, list)) and not node:
            del parent[key]
        else:
            break


def _dict_op(
    work: dict[str, Any],
    rel: str,
    merge: JsonMerge,
    old: mf.JsonOp | None,
    report: Report,
    *,
    force: bool,
    adopt: bool,
) -> tuple[mf.JsonOp, bool]:
    kp, desired = merge.key_path, merge.entries
    assert isinstance(desired, dict)
    if kp:
        parent = _parent(work, kp, rel, make=bool(desired))
        cont: Any = _MISSING
        if parent is not _MISSING:
            cont = parent.get(kp[-1], _MISSING)
            if cont is _MISSING and desired:
                cont = parent[kp[-1]] = {}
            if cont is not _MISSING and not isinstance(cont, dict):
                raise PyAisyncError(f"{rel}: '{'.'.join(kp)}' is not a JSON object")
    else:
        cont = work
    owned = dict(old.entries) if old else {}
    new_entries: dict[str, str] = {}
    changed = False

    def label(name: str) -> str:
        return f"{rel}#{'.'.join((*kp, name))}"

    for name in sorted(desired):
        value = desired[name]
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
        if name in desired or cont is _MISSING or name not in cont:
            continue
        if mf.value_hash(cont[name]) == recorded or force:
            report.deleted.append(label(name))
            del cont[name]
            changed = True
        else:
            report.warnings.append(
                f"{label(name)}: is no longer generated but was edited by hand; "
                "it will not be deleted"
            )
            new_entries[name] = recorded
    return mf.JsonOp(kp, "dict", new_entries), changed


def _item_label(rel: str, kp: tuple[str, ...], item: Any) -> str:
    text = json.dumps(item, ensure_ascii=False, sort_keys=True)
    return f"{rel}#{'.'.join(kp)}: {text if len(text) <= 70 else text[:67] + '...'}"


def _list_op(
    work: dict[str, Any],
    rel: str,
    merge: JsonMerge,
    old: mf.JsonOp | None,
    report: Report,
) -> tuple[mf.JsonOp, bool]:
    kp, desired = merge.key_path, merge.entries
    assert isinstance(desired, list) and kp
    parent = _parent(work, kp, rel, make=bool(desired))
    cur_list: Any = _MISSING
    if parent is not _MISSING:
        cur_list = parent.get(kp[-1], _MISSING)
        if cur_list is _MISSING and desired:
            cur_list = parent[kp[-1]] = []
        if cur_list is not _MISSING and not isinstance(cur_list, list):
            raise PyAisyncError(f"{rel}: '{'.'.join(kp)}' is not a JSON array")
    owned = set(old.entries) if old else set()
    want = {mf.value_hash(item): item for item in desired}
    changed = False
    if cur_list is not _MISSING:
        present = {mf.value_hash(item) for item in cur_list}
        for h in list(owned):
            if h in want or h not in present:
                continue
            idx = next(i for i, it in enumerate(cur_list) if mf.value_hash(it) == h)
            report.deleted.append(_item_label(rel, kp, cur_list[idx]))
            del cur_list[idx]
            changed = True
        present = {mf.value_hash(item) for item in cur_list}
        for h, item in want.items():
            if h in present:
                report.unchanged.append(_item_label(rel, kp, item))
            else:
                report.created.append(_item_label(rel, kp, item))
                cur_list.append(copy.deepcopy(item))
                changed = True
    return mf.JsonOp(kp, "list", {h: h for h in want}), changed


def _plan_json_file(
    root: Path,
    rel: str,
    merges: list[JsonMerge],
    old: mf.JsonFileState | None,
    report: Report,
    *,
    force: bool,
    adopt: bool,
) -> tuple[bytes | None, bool, mf.JsonFileState | None]:
    """Returns (new file bytes | None, delete_file, new manifest state | None)."""
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
    old_ops = old.ops if old else {}
    wanted = {mf.op_id(m.key_path): m for m in merges}
    for oid, prev in old_ops.items():  # ops that were merged before but no longer are
        wanted.setdefault(oid, JsonMerge(rel, prev.key_path, [] if prev.kind == "list" else {}))
    new_ops: dict[str, mf.JsonOp] = {}
    changed = False
    for oid, merge in sorted(wanted.items()):
        if isinstance(merge.entries, list):
            op, did = _list_op(work, rel, merge, old_ops.get(oid), report)
        else:
            op, did = _dict_op(work, rel, merge, old_ops.get(oid), report, force=force, adopt=adopt)
        changed = changed or did
        if op.entries:
            new_ops[oid] = op
    state = mf.JsonFileState(old.created if old else not existed, new_ops) if new_ops else None
    if not changed:
        return None, False, state
    for merge in wanted.values():
        _prune_empty_json(work, merge.key_path)
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
    manifest_rel: str = mf.MANIFEST,
) -> Report:
    report = Report(warnings=list(plan.warnings), notices=list(plan.notices))
    old = mf.load(root, manifest_rel)
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
    by_file: dict[str, list[JsonMerge]] = {}
    for merge in plan.merges:
        by_file.setdefault(merge.path, []).append(merge)
    for rel in old.json:
        by_file.setdefault(rel, [])
    json_writes: list[tuple[str, bytes | None, bool]] = []
    new_json: dict[str, mf.JsonFileState] = {}
    for rel, file_merges in sorted(by_file.items()):
        content, delete, state = _plan_json_file(
            root, rel, file_merges, old.json.get(rel), report, force=force, adopt=adopt
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
        atomic_write(root / manifest_rel, mf.dumps(mf.Manifest(new_files, new_json)))
    return report
