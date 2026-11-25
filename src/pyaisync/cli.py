from __future__ import annotations

import argparse
import sys
from dataclasses import replace
from pathlib import Path

from . import __version__
from .config import AI_DIR, load_config, set_targets
from .errors import PyAisyncError
from .importers import import_target
from .loader import load_model
from .model import ALL_TARGETS, FEATURES
from .plugin import PLUGIN_MANIFEST, build_claude_plugin
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
    "mcp.toml": (
        "# MCP servers. Use ${VAR} for secrets; never write literal keys here.\n"
        "#\n"
        "# [servers.docs]\n"
        '# type = "http"          # stdio | http | sse\n'
        '# url = "https://example.com/mcp"\n'
        '# headers = { Authorization = "Bearer ${DOCS_TOKEN}" }\n'
        "#\n"
        "# [servers.local-tool]\n"
        '# type = "stdio"\n'
        '# command = "uvx"\n'
        '# args = ["my-mcp-server"]\n'
        '# env = { API_KEY = "${MY_API_KEY}" }\n'
    ),
    "hooks.toml": (
        "# Hooks run COMMANDS on your machine. Review them like code.\n"
        "# Events: session_start, session_end, pre_tool_use, post_tool_use,\n"
        "#         user_prompt_submit, stop, subagent_start, subagent_stop, pre_compact\n"
        "# OpenCode has no declarative hooks (they are skipped for it).\n"
        "#\n"
        "# [[hooks]]\n"
        '# event = "pre_tool_use"\n'
        '# matcher = ["shell"]              # tools: read, grep, glob, edit, shell, web\n'
        '# command = "./scripts/guard.sh"   # relative to the repository root\n'
        "# timeout = 30                     # seconds, optional\n"
    ),
    "permissions.toml": (
        "# Rules are 'tool' or 'tool(pattern)'; tools: read, grep, glob, edit, shell, web.\n"
        "#\n"
        '# allow = ["shell(uv run pytest*)"]\n'
        '# ask   = ["shell(git push*)"]\n'
        '# deny  = ["read(.env*)", "shell(rm -rf*)"]\n'
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
    existing = detect_targets(root)
    # A repo that already has AI configuration should import it, not start from the examples.
    examples = not args.from_target and not existing
    (ai / "skills").mkdir(exist_ok=True)
    if examples:
        for rel, content in _TEMPLATES.items():
            atomic_write(ai / rel, content.encode())
        atomic_write(ai / "skills/.gitkeep", b"")
    print(f"Created {AI_DIR}/ with targets: {', '.join(targets)}")
    if len(targets) < len(ALL_TARGETS):
        print(
            f'  (chosen from the tools found in this repo; to add others, edit "targets" in '
            f'{AI_DIR}/config.toml, e.g. targets = ["claude", "cursor", "opencode"])'
        )
    if args.from_target:
        _print_import(import_target(root, args.from_target, on_conflict="skip", write=True))
    others = [t for t in existing if t != args.from_target]
    print()
    if args.from_target:
        print("Next steps:")
        print(
            f"  1. Review {AI_DIR}/ and the warnings above (anything not imported stays in your files)."
        )
        for t in others:
            print(f"  -  Also found {t} configuration: pyaisync import {t}")
        print("  2. pyaisync generate --dry-run    # preview what will be created or replaced")
        print(
            "  3. pyaisync generate --adopt      # first time only: take over your existing files"
        )
        print("  4. pyaisync check                 # later, in CI: fails if generated files drift")
    elif existing:
        print(f"Existing configuration found for: {', '.join(existing)}. Bring it in first:")
        for t in existing:
            print(f"  pyaisync import {t}")
        print("Then: pyaisync generate --dry-run, and pyaisync generate --adopt (first time only).")
    else:
        print("Next steps:")
        print(
            f"  1. Edit the examples in {AI_DIR}/ (rules/, commands/); add agents/, skills/, mcp.toml, ..."
        )
        print("  2. pyaisync generate --dry-run    # preview")
        print("  3. pyaisync generate              # write CLAUDE.md, .cursor/, AGENTS.md, ...")
        print("  4. pyaisync check                 # later, in CI: fails if generated files drift")
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
    if rep.notices:
        print("  hooks (these run commands on your machine; review them):")
        for n in rep.notices:
            print(f"    {n}")
    for w in rep.warnings:
        print(f"  warning: {w}", file=sys.stderr)
    for c in rep.conflicts:
        print(f"  CONFLICT: {c}", file=sys.stderr)


def _targets_line(targets: tuple[str, ...]) -> str:
    line = f"Targets: {', '.join(targets)}"
    missing = [t for t in ALL_TARGETS if t not in targets]
    if missing:
        line += (
            f'  (not enabled: {", ".join(missing)} - add them to "targets" in '
            f"{AI_DIR}/config.toml, or pass --targets)"
        )
    return line


def cmd_generate(args: argparse.Namespace, root: Path, *, check: bool = False) -> int:
    model = load_model(root)
    targets = _csv(args.targets, ALL_TARGETS, "targets") or model.config.targets
    only = _csv(getattr(args, "features", None), FEATURES, "features")
    if only is not None:
        feats = {f: (f in only) and model.config.features[f] for f in FEATURES}
        model = replace(model, config=replace(model.config, features=feats))
    plan = build_plan(model, targets)
    print(_targets_line(targets))
    write = not (check or args.dry_run)
    rep = apply(root, plan, write=write, force=args.force, adopt=args.adopt)
    _print_report(rep, write=write and not rep.conflicts)
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


def cmd_plugin(args: argparse.Namespace, root: Path) -> int:
    model = load_model(root)
    plan = build_claude_plugin(model)
    meta = model.config.plugin
    assert meta is not None
    out = Path(args.out) if args.out else Path("dist") / meta.name
    out = (out if out.is_absolute() else root / out).resolve()  # relative to --root
    if out == root or root.is_relative_to(out):
        raise PyAisyncError(f"refusing to write the plugin over the repository itself: {out}")
    write = not args.dry_run
    rep = apply(
        out, plan, write=write, force=args.force, adopt=args.adopt, manifest_rel=PLUGIN_MANIFEST
    )
    print(f"Plugin '{meta.name}' -> {out}")
    _print_report(rep, write=write and not rep.conflicts)
    if rep.conflicts:
        print("Nothing was written because of the conflicts above.", file=sys.stderr)
        return 2
    return 0


def cmd_target(args: argparse.Namespace, root: Path) -> int:
    current = load_config(root).targets
    wanted = tuple(dict.fromkeys(args.names))
    if args.action == "add":
        new = (*current, *[t for t in wanted if t not in current])
        already = [t for t in wanted if t in current]
    else:
        new = tuple(t for t in current if t not in wanted)
        already = [t for t in wanted if t not in current]
        if not new:
            raise PyAisyncError("refusing to disable every target; at least one must stay enabled")
    if already:
        state = "already enabled" if args.action == "add" else "was not enabled"
        print(f"  note: {', '.join(already)} {state}")
    if new != current:
        set_targets(root, new)
    changed = [t for t in wanted if t not in already]
    verb = "Enabled" if args.action == "add" else "Disabled"
    print(f"{verb}: {', '.join(changed) or 'nothing to change'}.  Targets: {', '.join(new)}")
    if args.action == "add":
        found = [t for t in changed if t in detect_targets(root)]
        for t in found:
            print(
                f"  Found existing {t} configuration in this repo: pyaisync import {t}  (to bring it into {AI_DIR}/)"
            )
        print("Next: pyaisync generate --dry-run   (then pyaisync generate)")
    elif changed:
        print(
            "Next: pyaisync generate   (removes the files pyaisync generated for it; your own files stay)"
        )
    return 0


def cmd_list(args: argparse.Namespace, root: Path) -> int:
    try:
        enabled: tuple[str, ...] | None = load_config(root).targets
    except PyAisyncError:
        enabled = None  # no .ai/ here: just show what each tool supports
    header = "target     " + ("enabled  " if enabled is not None else "")
    print(header + "  ".join(f"{f:11}" for f in FEATURES))
    for name, tgt in REGISTRY.items():
        mark = "" if enabled is None else f"{('yes' if name in enabled else 'no'):9}"
        print(
            f"{name:10} {mark}"
            + "  ".join(f"{('yes' if f in tgt.supported else 'no'):11}" for f in FEATURES)
        )
    if enabled is not None:
        print("\nEnable or disable a tool: pyaisync target add|remove <claude|cursor|opencode>")
    print("Plugins: `pyaisync plugin claude` packages .ai/ as a Claude Code plugin.")
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

    s = sub.add_parser("plugin", help="package .ai/ as a Claude Code plugin directory")
    s.add_argument("target", choices=("claude",))
    s.add_argument(
        "--out", help="output directory, relative to --root (default: dist/<plugin name>)"
    )
    s.add_argument("--dry-run", action="store_true")
    s.add_argument("--force", action="store_true", help="overwrite managed files edited by hand")
    s.add_argument("--adopt", action="store_true", help="take over existing unmanaged files")
    s.set_defaults(fn=cmd_plugin)

    s = sub.add_parser("target", help="enable or disable a tool in .ai/config.toml")
    s.add_argument("action", choices=("add", "remove"))
    s.add_argument("names", nargs="+", choices=ALL_TARGETS, metavar="TARGET")
    s.set_defaults(fn=cmd_target)

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
