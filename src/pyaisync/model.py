from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from .errors import PyAisyncError

NAME_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
FEATURES = ("rules", "commands", "skills")  # Phase 1
ALL_TARGETS = ("claude", "cursor", "opencode")


def validate_name(name: str, what: str) -> str:
    if not NAME_RE.fullmatch(name) or ".." in name:
        raise PyAisyncError(
            f"{what} '{name}': invalid name (use lowercase letters, digits, '.', '_' or '-')"
        )
    return name


@dataclass(frozen=True)
class Rule:
    name: str
    body: str
    description: str = ""
    globs: tuple[str, ...] = ()
    always: bool = False
    targets: tuple[str, ...] = ("*",)


@dataclass(frozen=True)
class Command:
    name: str
    body: str
    description: str = ""
    targets: tuple[str, ...] = ("*",)


@dataclass(frozen=True)
class Skill:
    name: str
    files: dict[str, bytes]  # relative posix path -> content
    targets: tuple[str, ...] = ("*",)


@dataclass(frozen=True)
class Config:
    targets: tuple[str, ...] = ALL_TARGETS
    features: dict[str, bool] = field(default_factory=lambda: {f: True for f in FEATURES})


@dataclass(frozen=True)
class Model:
    config: Config
    rules: tuple[Rule, ...] = ()
    commands: tuple[Command, ...] = ()
    skills: tuple[Skill, ...] = ()
    root: Path = Path(".")

    def for_target(self, target: str) -> Model:
        def ok(t: tuple[str, ...]) -> bool:
            return "*" in t or target in t

        return Model(
            config=self.config,
            rules=tuple(r for r in self.rules if ok(r.targets)),
            commands=tuple(c for c in self.commands if ok(c.targets)),
            skills=tuple(s for s in self.skills if ok(s.targets)),
            root=self.root,
        )
