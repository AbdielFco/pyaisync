"""Read and validate .ai/ -> Model."""

from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Any

from . import frontmatter
from .config import AI_DIR, load_config
from .errors import PyAisyncError
from .model import (
    ALL_TARGETS,
    Agent,
    Command,
    Hook,
    McpServer,
    Model,
    PermissionSet,
    Rule,
    Skill,
    validate_name,
)
from .safefs import read_text, read_tree, tree_modes
from .vocab import (
    HOOK_EVENTS,
    PATTERN_EVENTS,
    SECRET_VALUE,
    TOOL_MATCHER_EVENTS,
    TOOLS,
    check_secret,
    parse_rule,
)


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


_MCP_TYPES = {"stdio": "stdio", "http": "http", "remote": "http", "sse": "sse"}


def _str_map(raw: Any, where: str, field: str) -> tuple[tuple[str, str], ...]:
    if raw is None:
        return ()
    if not isinstance(raw, dict) or not all(
        isinstance(k, str) and isinstance(v, str) for k, v in raw.items()
    ):
        raise PyAisyncError(f"{where}: '{field}' must be a table of strings")
    for key, value in raw.items():
        check_secret(where, key, value)
    return tuple(sorted(raw.items()))


def _load_mcp(path: Path) -> list[McpServer]:
    if not path.is_file():
        return []
    try:
        data = tomllib.loads(read_text(path))
    except tomllib.TOMLDecodeError as exc:
        raise PyAisyncError(f"{path}: invalid TOML: {exc}") from exc
    servers = data.get("servers", {})
    if not isinstance(servers, dict):
        raise PyAisyncError(f"{path}: 'servers' must be a table")
    out: list[McpServer] = []
    for name, cfg in sorted(servers.items()):
        where = f"{path} [servers.{name}]"
        validate_name(name, f"{path}: server")
        if not isinstance(cfg, dict):
            raise PyAisyncError(f"{where}: must be a table")
        kind = _MCP_TYPES.get(str(cfg.get("type", "")))
        if kind is None:
            raise PyAisyncError(f"{where}: 'type' must be one of {sorted(_MCP_TYPES)}")
        args = cfg.get("args", [])
        if not isinstance(args, list) or not all(isinstance(a, str) for a in args):
            raise PyAisyncError(f"{where}: 'args' must be a list of strings")
        command = cfg.get("command", "")
        url = cfg.get("url", "")
        if not isinstance(command, str) or not isinstance(url, str):
            raise PyAisyncError(f"{where}: 'command' and 'url' must be strings")
        if kind == "stdio":
            if not command:
                raise PyAisyncError(f"{where}: stdio servers need 'command'")
            if url:
                raise PyAisyncError(f"{where}: stdio servers cannot have 'url'")
        else:
            if not url.startswith(("http://", "https://")):
                raise PyAisyncError(f"{where}: remote servers need an http(s) 'url'")
            if command or args:
                raise PyAisyncError(f"{where}: remote servers cannot have 'command'/'args'")
        out.append(
            McpServer(
                name=name,
                type=kind,
                command=command,
                args=tuple(args),
                env=_str_map(cfg.get("env"), where, "env"),
                url=url,
                headers=_str_map(cfg.get("headers"), where, "headers"),
                targets=_targets(cfg, Path(where)),
            )
        )
    return out


def _read_toml(path: Path) -> dict[str, Any]:
    try:
        return tomllib.loads(read_text(path))
    except tomllib.TOMLDecodeError as exc:
        raise PyAisyncError(f"{path}: invalid TOML: {exc}") from exc


def _load_hooks(path: Path) -> list[Hook]:
    if not path.is_file():
        return []
    raw = _read_toml(path).get("hooks", [])
    if not isinstance(raw, list):
        raise PyAisyncError(f"{path}: 'hooks' must be an array of tables ([[hooks]])")
    out: list[Hook] = []
    for i, cfg in enumerate(raw, 1):
        where = f"{path} [[hooks]] #{i}"
        if not isinstance(cfg, dict):
            raise PyAisyncError(f"{where}: must be a table")
        event = cfg.get("event")
        if event not in HOOK_EVENTS:
            raise PyAisyncError(f"{where}: 'event' must be one of {sorted(HOOK_EVENTS)}")
        command = cfg.get("command")
        if not isinstance(command, str) or not command.strip():
            raise PyAisyncError(f"{where}: 'command' must be a non-empty string")
        if "\n" in command or "\r" in command:
            raise PyAisyncError(f"{where}: 'command' must be a single line")
        if SECRET_VALUE.search(command):
            raise PyAisyncError(f"{where}: 'command' contains what looks like a literal secret")
        matcher = cfg.get("matcher", [])
        if isinstance(matcher, str):
            matcher = [matcher]
        if not isinstance(matcher, list) or not all(isinstance(m, str) for m in matcher):
            raise PyAisyncError(f"{where}: 'matcher' must be a tool name or a list of tool names")
        bad = [m for m in matcher if m not in TOOLS]
        if bad:
            raise PyAisyncError(f"{where}: unknown tools in matcher {bad}; valid: {list(TOOLS)}")
        if matcher and event not in TOOL_MATCHER_EVENTS:
            raise PyAisyncError(
                f"{where}: 'matcher' is only valid for {sorted(TOOL_MATCHER_EVENTS)}"
            )
        pattern = cfg.get("pattern", "")
        if not isinstance(pattern, str):
            raise PyAisyncError(f"{where}: 'pattern' must be a string")
        if pattern and event not in PATTERN_EVENTS:
            raise PyAisyncError(
                f"{where}: 'pattern' is only valid for Cursor-only events {sorted(PATTERN_EVENTS)}"
            )
        fail_closed = cfg.get("fail_closed", False)
        if not isinstance(fail_closed, bool):
            raise PyAisyncError(f"{where}: 'fail_closed' must be true or false")
        timeout = cfg.get("timeout")
        if timeout is not None and (
            isinstance(timeout, bool) or not isinstance(timeout, int) or not 1 <= timeout <= 3600
        ):
            raise PyAisyncError(
                f"{where}: 'timeout' must be an integer between 1 and 3600 (seconds)"
            )
        out.append(
            Hook(
                event=event,
                command=command.strip(),
                matcher=tuple(dict.fromkeys(matcher)),
                timeout=timeout,
                targets=_targets(cfg, Path(where)),
                pattern=pattern,
                fail_closed=fail_closed,
            )
        )
    return out


def _load_permissions(path: Path) -> PermissionSet:
    if not path.is_file():
        return PermissionSet()
    data = _read_toml(path)
    unknown = set(data) - {"allow", "ask", "deny"}
    if unknown:
        raise PyAisyncError(f"{path}: unknown keys {sorted(unknown)}; valid: allow, ask, deny")
    lists: dict[str, tuple[str, ...]] = {}
    for key in ("allow", "ask", "deny"):
        raw = data.get(key, [])
        if not isinstance(raw, list) or not all(isinstance(r, str) for r in raw):
            raise PyAisyncError(f"{path}: '{key}' must be a list of strings")
        for rule in raw:
            parse_rule(rule, f"{path} [{key}]")
        lists[key] = tuple(dict.fromkeys(r.strip() for r in raw))
    for a, b in (("allow", "deny"), ("allow", "ask"), ("ask", "deny")):
        both = set(lists[a]) & set(lists[b])
        if both:
            raise PyAisyncError(f"{path}: rules {sorted(both)} appear in both '{a}' and '{b}'")
    return PermissionSet(**lists)


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

    agents: list[Agent] = []
    for p in _md_files(ai / "agents"):
        meta, body = frontmatter.parse(read_text(p), str(p))
        tools = meta.get("tools")
        if tools is not None:
            if not isinstance(tools, list) or not all(isinstance(t, str) for t in tools):
                raise PyAisyncError(f"{p}: 'tools' must be a list of strings")
            bad = [t for t in tools if t not in TOOLS]
            if bad:
                raise PyAisyncError(f"{p}: unknown tools {bad}; valid: {list(TOOLS)}")
        model_name = meta.get("model", "inherit")
        if not isinstance(model_name, str) or not model_name:
            raise PyAisyncError(f"{p}: 'model' must be a non-empty string")
        agents.append(
            Agent(
                name=validate_name(p.stem, f"{p}: agent"),
                body=body.strip("\n"),
                description=_str(meta, "description", p),
                tools=None if tools is None else tuple(dict.fromkeys(tools)),
                model=model_name,
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
            skills.append(
                Skill(
                    name=name,
                    files=read_tree(d),
                    executable=tree_modes(d),
                    targets=_targets(meta, d),
                )
            )

    return Model(
        config=config,
        rules=tuple(rules),
        commands=tuple(commands),
        skills=tuple(skills),
        agents=tuple(agents),
        mcp_servers=tuple(_load_mcp(ai / "mcp.toml")),
        hooks=tuple(_load_hooks(ai / "hooks.toml")),
        permissions=_load_permissions(ai / "permissions.toml"),
        root=root,
    )
