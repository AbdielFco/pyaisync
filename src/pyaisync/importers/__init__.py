"""Import native configuration -> .ai/. Reads native files only; writes only to .ai/."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .. import frontmatter
from ..config import AI_DIR
from ..errors import PyAisyncError
from ..safefs import atomic_write, read_tree, safe_join, tree_modes
from .common import ImportReport, Item, read_native, slug
from .structured import agent_items, import_structured


def _rule_item(name: str, meta: dict[str, Any], body: str) -> Item:
    out: dict[str, Any] = {}
    if isinstance(meta.get("description"), str) and meta["description"]:
        out["description"] = meta["description"]
    globs = meta.get("globs")
    if isinstance(globs, str):
        globs = [g.strip() for g in globs.split(",") if g.strip()]
    if isinstance(globs, list) and globs:
        out["globs"] = [str(g) for g in globs]
    if meta.get("always") is True or meta.get("alwaysApply") is True:
        out["always"] = True
    text = frontmatter.dump(out, body.strip("\n"))
    return Item("rules", name, {f"rules/{name}.md": text.encode()})


def _command_item(name: str, meta: dict[str, Any], body: str) -> Item:
    out = (
        {"description": meta["description"]}
        if isinstance(meta.get("description"), str) and meta["description"]
        else {}
    )
    text = frontmatter.dump(out, body.strip("\n"))
    return Item("commands", name, {f"commands/{name}.md": text.encode()})


def _collect_md(directory: Path, suffix: str, kind: str, rep: ImportReport) -> list[Item]:
    items: list[Item] = []
    if not directory.is_dir() or directory.is_symlink():
        return items
    for p in sorted(directory.iterdir()):
        if p.suffix != suffix or p.is_symlink() or not p.is_file():
            continue
        name = slug(p.stem)
        if name is None:
            rep.warnings.append(f"{p.name}: invalid name, not imported")
            continue
        parsed = read_native(p, rep)
        if parsed is None:
            continue
        meta, body = parsed
        items.append(
            _rule_item(name, meta, body) if kind == "rules" else _command_item(name, meta, body)
        )
    return items


def _collect_skills(directory: Path, rep: ImportReport) -> list[Item]:
    items: list[Item] = []
    if not directory.is_dir() or directory.is_symlink():
        return items
    for d in sorted(directory.iterdir()):
        if d.name.startswith(".") or d.is_symlink() or not (d / "SKILL.md").is_file():
            continue
        name = slug(d.name)
        if name is None:
            rep.warnings.append(f"{d.name}: invalid skill name, not imported")
            continue
        try:
            tree = read_tree(d)
        except PyAisyncError as exc:
            rep.warnings.append(str(exc))
            continue
        modes = tree_modes(d)
        items.append(
            Item(
                "skills",
                name,
                {f"skills/{name}/{r}": b for r, b in tree.items()},
                frozenset(f"skills/{name}/{r}" for r in modes),
            )
        )
    return items


def _root_rule(path: Path, rep: ImportReport) -> list[Item]:
    if not path.is_file() or path.is_symlink():
        return []
    parsed = read_native(path, rep)
    if parsed is None:
        return []
    meta, body = parsed
    meta = {"always": True, "description": meta.get("description", "")}
    return [_rule_item("project", meta, body)]


def _discover(root: Path, target: str, rep: ImportReport) -> list[Item]:
    if target == "claude":
        return (
            _root_rule(root / "CLAUDE.md", rep)
            + _collect_md(root / ".claude/commands", ".md", "commands", rep)
            + _collect_skills(root / ".claude/skills", rep)
        )
    if target == "cursor":
        return (
            _collect_md(root / ".cursor/rules", ".mdc", "rules", rep)
            + _collect_md(root / ".cursor/commands", ".md", "commands", rep)
            + _collect_skills(root / ".cursor/skills", rep)
            + _collect_skills(root / ".agents/skills", rep)
        )
    if target == "opencode":
        return (
            _root_rule(root / "AGENTS.md", rep)
            + _collect_md(root / ".opencode/commands", ".md", "commands", rep)
            + _collect_md(root / ".opencode/command", ".md", "commands", rep)
            + _collect_skills(root / ".opencode/skills", rep)
            + _collect_skills(root / ".agents/skills", rep)
        )
    raise PyAisyncError(f"unknown target: {target}")


def _rename(item: Item, new: str) -> Item:
    files = {}
    for rel, data in item.files.items():
        parts = rel.split("/")
        if item.kind == "skills":
            parts[1] = new
        else:
            parts[1] = f"{new}.md"
        files["/".join(parts)] = data
    renamed = {"/".join([*rel.split("/")[:1], new, *rel.split("/")[2:]]) for rel in item.executable}
    return Item(item.kind, new, files, frozenset(renamed))


def import_target(
    root: Path, target: str, *, on_conflict: str = "skip", write: bool = True
) -> ImportReport:
    if on_conflict not in ("skip", "rename", "overwrite"):
        raise PyAisyncError("--on-conflict must be skip, rename or overwrite")
    if not (root / AI_DIR / "config.toml").is_file():
        raise PyAisyncError(".ai/config.toml not found: run 'pyaisync init' first")
    rep = ImportReport()
    items = _discover(root, target, rep)
    items += agent_items(root, target, rep)
    seen: set[tuple[str, str]] = set()
    for item in items:
        key = (item.kind, item.name)
        if key in seen:  # e.g. a skill duplicated in .cursor/skills and .agents/skills
            continue
        seen.add(key)
        dest_exists = any(safe_join(root, f"{AI_DIR}/{r}").exists() for r in item.files)
        same = dest_exists and all(
            (root / AI_DIR / r).is_file() and (root / AI_DIR / r).read_bytes() == d
            for r, d in item.files.items()
        )
        label = f"{item.kind}/{item.name}"
        if same:
            rep.unchanged.append(label)
            continue
        if dest_exists:
            if on_conflict == "skip":
                rep.skipped.append(
                    f"{label}: already exists in .ai/ (use --on-conflict rename|overwrite)"
                )
                continue
            if on_conflict == "rename":
                new = slug(f"{item.name}-{target}")
                if new is None or any(
                    safe_join(root, f"{AI_DIR}/{r}").exists() for r in _rename(item, new).files
                ):
                    rep.skipped.append(f"{label}: could not rename without a collision")
                    continue
                item = _rename(item, new)
                label = f"{item.kind}/{item.name}"
        for rel, data in item.files.items():
            if write:
                atomic_write(
                    safe_join(root, f"{AI_DIR}/{rel}"), data, executable=rel in item.executable
                )
        rep.written.append(label)
    import_structured(root, target, rep, on_conflict, write)
    return rep
