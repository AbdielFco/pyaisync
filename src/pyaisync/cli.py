from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import __version__
from .config import AI_DIR
from .errors import PyAisyncError
from .importers import import_target
from .loader import load_model
from .model import ALL_TARGETS, FEATURES
from .safefs import atomic_write
from .targets import REGISTRY, build_plan
from .writer import Report, apply

_TEMPLATES = {
    "rules/project.md": (
        "---\n"
        "description: General project rules\n"
        "always: true\n"
        "---\n"
        "# Example: edit or delete this file\n"
        "- Describe your conventions and the test/lint commands for this project here.\n"
    ),
    "commands/example.md": (
        "---\ndescription: Example command\n---\nSummarize the uncommitted changes. $ARGUMENTS\n"
    ),
}


def detect_targets(root: Path) -> list[str]:
    found = []
    if (root / "CLAUDE.md").exists() or (root / ".claude").exists():
        found.append("claude")
    if (root / ".cursor").exists():
        found.append("cursor")
    if any((root / p).exists() for p in ("opencode.json", "opencode.jsonc", ".opencode")):
        found.append("opencode")
    return found


def _csv(value: str | None, valid: tuple[str, ...], what: str) -> tuple[str, ...] | None:
    if value is None:
        return None
    items = tuple(v.strip() for v in value.split(",") if v.strip())
    bad = [v for v in items if v not in valid]
    if bad:
        raise PyAisyncError(f"unknown {what} {bad}; valid: {list(valid)}")
    return items


def cmd_init(args: argparse.Namespace, root: Path) -> int:
    ai = root / AI_DIR
    if ai.exists():
        raise PyAisyncError(f"{AI_DIR}/ already exists: init never overwrites anything")
    targets = (
        _csv(args.targets, ALL_TARGETS, "targets") or tuple(detect_targets(root)) or ALL_TARGETS
    )
    if args.from_target:
        if args.from_target not in ALL_TARGETS:
            raise PyAisyncError(f"unknown --from: {args.from_target}")
        if args.from_target not in targets:
            targets = (*targets, args.from_target)
    tlist = ", ".join(f'"{t}"' for t in targets)
    feats = "\n".join(f"{f} = true" for f in FEATURES)
    atomic_write(
        ai / "config.toml", f"version = 1\ntargets = [{tlist}]\n\n[features]\n{feats}\n".encode()
    )
    if args.from_target:
        (ai / "skills").mkdir(exist_ok=True)
    else:
        for rel, content in _TEMPLATES.items():
            atomic_write(ai / rel, content.encode())
        (ai / "skills").mkdir(exist_ok=True)
        atomic_write(ai / "skills/.gitkeep", b"")
    print(f"Created {AI_DIR}/ with targets: {', '.join(targets)}")
    if args.from_target:
        _print_import(import_target(root, args.from_target, on_conflict="skip", write=True))
    print("Next step: pyaisync generate --dry-run")
    return 0


def _print_import(rep) -> None:  # type: ignore[no-untyped-def]
    for label in rep.written:
        print(f"  imported    {label}")
    for label in rep.unchanged:
        print(f"  unchanged   {label}")
    for msg in rep.skipped:
        print(f"  skipped     {msg}")
    for msg in rep.warnings:
        print(f"  warning     {msg}", file=sys.stderr)


def cmd_import(args: argparse.Namespace, root: Path) -> int:
    rep = import_target(root, args.target, on_conflict=args.on_conflict, write=not args.dry_run)
    print("(dry run, nothing was written)" if args.dry_run else "Import:")
    _print_import(rep)
    return 0


def _print_report(rep: Report, *, write: bool) -> None:
    verbs = (
        ("created", "created    " if write else "would create"),
        ("updated", "updated    " if write else "would update"),
        ("deleted", "deleted    " if write else "would delete"),
    )
    for attr, label in verbs:
        for p in getattr(rep, attr):
            print(f"  {label} {p}")
    print(f"  unchanged: {len(rep.unchanged)}")
    for w in rep.warnings:
        print(f"  warning: {w}", file=sys.stderr)
    for c in rep.conflicts:
        print(f"  CONFLICT: {c}", file=sys.stderr)


def cmd_generate(args: argparse.Namespace, root: Path, *, check: bool = False) -> int:
    model = load_model(root)
    targets = _csv(args.targets, ALL_TARGETS, "targets") or model.config.targets
    only = _csv(getattr(args, "features", None), FEATURES, "features")
    if only is not None:
        model = type(model)(
            config=type(model.config)(
                targets=model.config.targets,
                features={f: (f in only) and model.config.features[f] for f in FEATURES},
            ),
            rules=model.rules,
            commands=model.commands,
            skills=model.skills,
            root=model.root,
        )
    plan = build_plan(model, targets)
    write = not (check or args.dry_run)
    rep = apply(root, plan, write=write, force=args.force, adopt=args.adopt)
    _print_report(rep, write=write)
    if rep.conflicts:
        print("Nothing was written because of the conflicts above.", file=sys.stderr)
        return 2
    if check:
        if rep.has_changes:
            print(
                "DRIFT: generated files do not match .ai/ (run 'pyaisync generate').",
                file=sys.stderr,
            )
            return 1
        print("OK: no drift.")
    return 0


def cmd_list(args: argparse.Namespace, root: Path) -> int:
    print("target     " + "  ".join(f"{f:9}" for f in FEATURES))
    for name, tgt in REGISTRY.items():
        print(
            f"{name:10} "
            + "  ".join(f"{('yes' if f in tgt.supported else 'no'):9}" for f in FEATURES)
        )
    print("\nNot implemented yet (phases 2-4): subagents, mcp, hooks, permissions, plugins.")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="pyaisync", description="One source (.ai/) for AI coding agent configuration."
    )
    p.add_argument("--version", action="version", version=f"pyaisync {__version__}")
    p.add_argument("--root", default=".", help="repository root (default: .)")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("init", help="create .ai/")
    s.add_argument("--targets")
    s.add_argument(
        "--from", dest="from_target", metavar="TARGET", help="import from claude|cursor|opencode"
    )
    s.set_defaults(fn=cmd_init)

    for name in ("generate", "check"):
        s = sub.add_parser(
            name,
            help="generate native files"
            if name == "generate"
            else "fail if generated files drifted",
        )
        s.add_argument("--targets")
        s.add_argument("--features")
        s.add_argument(
            "--force", action="store_true", help="overwrite managed files that were edited by hand"
        )
        s.add_argument("--adopt", action="store_true", help="take over existing unmanaged files")
        if name == "generate":
            s.add_argument("--dry-run", action="store_true")
            s.set_defaults(fn=lambda a, r: cmd_generate(a, r))
        else:
            s.set_defaults(fn=lambda a, r: cmd_generate(a, r, check=True), dry_run=True)

    s = sub.add_parser("import", help="import a target's native config into .ai/")
    s.add_argument("target", choices=ALL_TARGETS)
    s.add_argument("--dry-run", action="store_true")
    s.add_argument("--on-conflict", default="skip", choices=("skip", "rename", "overwrite"))
    s.set_defaults(fn=cmd_import)

    s = sub.add_parser("list", help="support matrix per target")
    s.set_defaults(fn=cmd_list)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return int(args.fn(args, Path(args.root).resolve()))
    except PyAisyncError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
