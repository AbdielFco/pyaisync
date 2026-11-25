"""Read and validate .ai/ -> Model."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from . import frontmatter
from .config import AI_DIR, load_config
from .errors import PyAisyncError
from .model import ALL_TARGETS, Command, Model, Rule, Skill, validate_name
from .safefs import read_text, read_tree


def _targets(meta: dict[str, Any], src: Path) -> tuple[str, ...]:
    raw = meta.get("targets", ["*"])
    if not isinstance(raw, list) or not all(isinstance(t, str) for t in raw):
        raise PyAisyncError(f"{src}: 'targets' must be a list of strings")
    bad = [t for t in raw if t != "*" and t not in ALL_TARGETS]
    if bad:
        raise PyAisyncError(f"{src}: unknown targets {bad}")
    return tuple(raw)


def _str(meta: dict[str, Any], key: str, src: Path) -> str:
    val = meta.get(key, "")
    if not isinstance(val, str):
        raise PyAisyncError(f"{src}: '{key}' must be a string")
    return val


def _md_files(directory: Path) -> list[Path]:
    if not directory.is_dir():
        return []
    return sorted(
        p for p in directory.iterdir() if p.suffix == ".md" and not p.name.startswith(".")
    )


def load_model(root: Path) -> Model:
    config = load_config(root)
    ai = root / AI_DIR

    rules: list[Rule] = []
    for p in _md_files(ai / "rules"):
        meta, body = frontmatter.parse(read_text(p), str(p))
        globs = meta.get("globs", [])
        if isinstance(globs, str):
            globs = [globs]
        if not isinstance(globs, list) or not all(isinstance(g, str) for g in globs):
            raise PyAisyncError(f"{p}: 'globs' must be a list of strings")
        always = meta.get("always", False)
        if not isinstance(always, bool):
            raise PyAisyncError(f"{p}: 'always' must be true or false")
        rules.append(
            Rule(
                name=validate_name(p.stem, f"{p}: rule"),
                body=body.strip("\n"),
                description=_str(meta, "description", p),
                globs=tuple(globs),
                always=always,
                targets=_targets(meta, p),
            )
        )

    commands: list[Command] = []
    for p in _md_files(ai / "commands"):
        meta, body = frontmatter.parse(read_text(p), str(p))
        commands.append(
            Command(
                name=validate_name(p.stem, f"{p}: command"),
                body=body.strip("\n"),
                description=_str(meta, "description", p),
                targets=_targets(meta, p),
            )
        )

    skills: list[Skill] = []
    sdir = ai / "skills"
    if sdir.is_dir():
        for d in sorted(sdir.iterdir()):
            if d.name.startswith(".") or not d.is_dir():
                continue
            if d.is_symlink():
                raise PyAisyncError(f"{d}: symlink not allowed")
            name = validate_name(d.name, f"{d}: skill")
            if not (d / "SKILL.md").is_file():
                raise PyAisyncError(f"{d}: missing SKILL.md")
            meta, _ = frontmatter.parse(read_text(d / "SKILL.md"), str(d / "SKILL.md"))
            skills.append(Skill(name=name, files=read_tree(d), targets=_targets(meta, d)))

    return Model(
        config=config,
        rules=tuple(rules),
        commands=tuple(commands),
        skills=tuple(skills),
        root=root,
    )
