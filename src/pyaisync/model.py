from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from .errors import PyAisyncError

NAME_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
FEATURES = ("rules", "commands", "skills", "subagents", "mcp", "hooks", "permissions")
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
class Agent:
    name: str
    body: str
    description: str = ""
    tools: tuple[str, ...] | None = None  # None = inherit everything
    model: str = "inherit"
    targets: tuple[str, ...] = ("*",)


@dataclass(frozen=True)
class McpServer:
    name: str
    type: str  # "stdio" | "http" | "sse"
    command: str = ""
    args: tuple[str, ...] = ()
    env: tuple[tuple[str, str], ...] = ()
    url: str = ""
    headers: tuple[tuple[str, str], ...] = ()
    targets: tuple[str, ...] = ("*",)


@dataclass(frozen=True)
class Hook:
    event: str  # canonical event name (vocab.HOOK_EVENTS)
    command: str
    matcher: tuple[str, ...] = ()  # canonical tools; only for tool events
    timeout: int | None = None  # seconds
    targets: tuple[str, ...] = ("*",)


@dataclass(frozen=True)
class PermissionSet:
    allow: tuple[str, ...] = ()
    ask: tuple[str, ...] = ()
    deny: tuple[str, ...] = ()  # canonical rules: "tool" or "tool(pattern)"

    def __bool__(self) -> bool:
        return bool(self.allow or self.ask or self.deny)


@dataclass(frozen=True)
class Skill:
    name: str
    files: dict[str, bytes]  # relative posix path -> content
    targets: tuple[str, ...] = ("*",)


@dataclass(frozen=True)
class PluginMeta:
    name: str
    version: str = "0.1.0"
    description: str = ""
    author: str = ""
    license: str = ""


@dataclass(frozen=True)
class Config:
    targets: tuple[str, ...] = ALL_TARGETS
    plugin: PluginMeta | None = None
    features: dict[str, bool] = field(default_factory=lambda: {f: True for f in FEATURES})


@dataclass(frozen=True)
class Model:
    config: Config
    rules: tuple[Rule, ...] = ()
    commands: tuple[Command, ...] = ()
    skills: tuple[Skill, ...] = ()
    agents: tuple[Agent, ...] = ()
    mcp_servers: tuple[McpServer, ...] = ()
    hooks: tuple[Hook, ...] = ()
    permissions: PermissionSet = PermissionSet()
    root: Path = Path(".")

    def for_target(self, target: str) -> Model:
        def ok(t: tuple[str, ...]) -> bool:
            return "*" in t or target in t

        return Model(
            config=self.config,
            rules=tuple(r for r in self.rules if ok(r.targets)),
            commands=tuple(c for c in self.commands if ok(c.targets)),
            skills=tuple(s for s in self.skills if ok(s.targets)),
            agents=tuple(a for a in self.agents if ok(a.targets)),
            mcp_servers=tuple(m for m in self.mcp_servers if ok(m.targets)),
            hooks=tuple(h for h in self.hooks if ok(h.targets)),
            permissions=self.permissions,
            root=self.root,
        )
