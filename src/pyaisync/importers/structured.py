"""Import of subagents, MCP servers, hooks and permissions.

Principles (same as the rest of `import`): read-only on native files, never execute anything,
and report everything that cannot be mapped. Two extra rules apply because these features
carry security meaning:

* never *widen* access: a tool allowlist or an allow rule that cannot be mapped exactly is
  skipped, never loosened;
* never write literal secrets into `.ai/`: sensitive values become `${VAR}` references.
"""

from __future__ import annotations

import json
import re
from dataclasses import replace
from pathlib import Path
from typing import Any

from .. import frontmatter
from .. import manifest as mf
from ..errors import PyAisyncError
from ..loader import _load_hooks, _load_mcp, _load_permissions
from ..model import Hook, McpServer, validate_name
from ..safefs import MAX_FILE_BYTES, atomic_write, read_text
from ..vocab import (
    CURSOR_TOOLS,
    HOOK_EVENTS,
    OPENCODE_ACTIONS,
    PATTERN_EVENTS,
    SECRET_VALUE,
    TOOL_MATCHER_EVENTS,
    check_secret,
    parse_rule,
)
from .common import ImportReport, Item, read_native, slug

CLAUDE_TOOLS_REV = {
    "Read": "read",
    "Grep": "grep",
    "Glob": "glob",
    "Edit": "edit",
    "Write": "edit",
    "Bash": "shell",
    "WebFetch": "web",
    "WebSearch": "web",
}
CURSOR_TOOLS_REV = {v: k for k, v in CURSOR_TOOLS.items()}
CLAUDE_EVENTS_REV = {v[0]: k for k, v in HOOK_EVENTS.items() if v[0] is not None}
CURSOR_EVENTS_REV = {v[1]: k for k, v in HOOK_EVENTS.items() if v[1] is not None}
_ENV_PATTERNS = {
    "claude": re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}"),
    "cursor": re.compile(r"\$\{env:([A-Za-z_][A-Za-z0-9_]*)\}"),
    "opencode": re.compile(r"\{env:([A-Za-z_][A-Za-z0-9_]*)\}"),
}
_AGENT_UNIMPORTED = (
    "disallowedTools",
    "permissionMode",
    "maxTurns",
    "autoCompactWindow",
    "skills",
    "mcpServers",
    "hooks",
    "memory",
    "background",
    "omitClaudeMd",
    "effort",
    "is_background",
)
_RANK = {"allow": 0, "ask": 1, "deny": 2}

# native list items that were converted: (file, key path) -> hashes. See manifest.JsonOp.claimed.
Claims = dict[tuple[str, tuple[str, ...]], set[str]]


# ---------------------------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------------------------


def _load_json(path: Path, rep: ImportReport) -> dict[str, Any] | None:
    if not path.is_file() or path.is_symlink():
        return None
    try:
        data = json.loads(read_text(path))
    except (ValueError, PyAisyncError) as exc:
        rep.warnings.append(
            f"{path.name}: cannot be parsed as plain JSON ({exc}); comments are not supported; skipped"
        )
        return None
    if not isinstance(data, dict):
        rep.warnings.append(f"{path.name}: top-level value is not an object; skipped")
        return None
    return data


def _dig(data: dict[str, Any], *keys: str) -> Any:
    cur: Any = data
    for key in keys:
        if not isinstance(cur, dict) or key not in cur:
            return None
        cur = cur[key]
    return cur


class _Owned:
    """Entries pyaisync itself generated (recorded in the manifest): never re-imported."""

    def __init__(self, root: Path) -> None:
        self.manifest = mf.load(root)

    def list_hashes(self, rel: str, key_path: tuple[str, ...]) -> set[str]:
        st = self.manifest.json.get(rel)
        op = st.ops.get(mf.op_id(key_path)) if st else None
        return set(op.entries) if op else set()

    def dict_hashes(self, rel: str, key_path: tuple[str, ...]) -> dict[str, str]:
        st = self.manifest.json.get(rel)
        op = st.ops.get(mf.op_id(key_path)) if st else None
        return dict(op.entries) if op else {}


def _env_in(value: str, target: str) -> str:
    """Native env reference -> canonical `${VAR}`."""
    return _ENV_PATTERNS[target].sub(lambda m: "${" + m.group(1) + "}", value)


def _safe_pairs(
    pairs: Any, target: str, where: str, rep: ImportReport
) -> tuple[tuple[tuple[str, str], ...], bool]:
    """Translate env refs; replace literal secrets with `${VAR}` references. (pairs, ok)."""
    if not isinstance(pairs, dict):
        return (), pairs is None
    out: list[tuple[str, str]] = []
    for key, raw in pairs.items():
        if not isinstance(key, str) or not isinstance(raw, str):
            rep.warnings.append(f"{where}: non-string value for '{key}'; server skipped")
            return (), False
        value = _env_in(raw, target)
        try:
            check_secret(where, key, value)
        except PyAisyncError:
            var = re.sub(r"[^A-Za-z0-9]+", "_", key).strip("_").upper() or "SECRET"
            prefix = next((p for p in ("Bearer ", "Basic ") if value.startswith(p)), "")
            value = f"{prefix}${{{var}}}"
            rep.warnings.append(
                f"{where}: literal secret in '{key}' was NOT imported; it now reads "
                f"${{{var}}} - set that environment variable"
            )
        out.append((key, value))
    return tuple(sorted(out)), True


def _has_literal_secret(*values: str) -> bool:
    return any(SECRET_VALUE.search(v) for v in values)


# ---------------------------------------------------------------------------------------------
# subagents
# ---------------------------------------------------------------------------------------------

_AGENT_DIRS = {
    "claude": ".claude/agents",
    "cursor": ".cursor/agents",
    "opencode": ".opencode/agents",
}


def _opencode_agent_tools(perms: Any) -> tuple[str, ...] | None | str:
    """Reverse of pyaisync's `deny *` + allow rules. Returns tools, None (inherit) or an error."""
    if perms is None:
        return None
    if not isinstance(perms, list) or not perms:
        return "unrecognized permissions"
    first, *rest = perms
    if first != {"action": "*", "resource": "*", "effect": "deny"}:
        return "permissions are not a simple allowlist"
    actions: set[str] = set()
    for rule in rest:
        if not (
            isinstance(rule, dict)
            and rule.get("effect") == "allow"
            and rule.get("resource") == "*"
            and isinstance(rule.get("action"), str)
        ):
            return "permissions are not a simple allowlist"
        actions.add(rule["action"])
    tools = [t for t, acts in OPENCODE_ACTIONS.items() if actions >= set(acts)]
    covered = {a for t in tools for a in OPENCODE_ACTIONS[t]}
    if actions - covered:
        return f"actions {sorted(actions - covered)} have no canonical equivalent"
    return tuple(tools)


def agent_items(root: Path, target: str, rep: ImportReport) -> list[Item]:
    directory = root / _AGENT_DIRS[target]
    if not directory.is_dir() or directory.is_symlink():
        return []
    items: list[Item] = []
    for p in sorted(directory.glob("*.md")):
        if p.is_symlink() or not p.is_file():
            continue
        name = slug(p.stem)
        if name is None:
            rep.warnings.append(f"{p.name}: invalid agent name, not imported")
            continue
        parsed = read_native(p, rep)
        if parsed is None:
            continue
        meta, body = parsed
        label = f"agents/{name}"
        ignored = [f for f in _AGENT_UNIMPORTED if f in meta]
        tools: tuple[str, ...] | None = None
        if target == "claude":
            if "disallowedTools" in meta:
                rep.skipped.append(
                    f"{label}: uses 'disallowedTools', which cannot be mapped without widening access"
                )
                continue
            raw = meta.get("tools")
            if raw is not None:
                names = (
                    [t.strip() for t in raw.split(",")]
                    if isinstance(raw, str)
                    else [str(t) for t in raw]
                    if isinstance(raw, list)
                    else []
                )
                mapped = [CLAUDE_TOOLS_REV[n] for n in names if n in CLAUDE_TOOLS_REV]
                dropped = [n for n in names if n and n not in CLAUDE_TOOLS_REV]
                if not mapped:
                    rep.skipped.append(f"{label}: none of its tools {names} map to canonical tools")
                    continue
                if dropped:
                    rep.warnings.append(f"{label}: tools {dropped} have no canonical equivalent")
                tools = tuple(dict.fromkeys(mapped))
        elif target == "cursor":
            if meta.get("readonly") is True:
                tools = ("read", "grep", "glob", "web")
                rep.warnings.append(f"{label}: 'readonly' imported as tools {list(tools)}")
        else:
            if meta.get("mode", "primary") != "subagent":
                rep.skipped.append(
                    f"{label}: mode is not 'subagent' (primary agents are not imported)"
                )
                continue
            result = _opencode_agent_tools(meta.get("permissions"))
            if isinstance(result, str):
                rep.skipped.append(f"{label}: {result}; not imported")
                continue
            tools = result
        if ignored:
            rep.warnings.append(f"{label}: fields not imported: {ignored}")
        out: dict[str, Any] = {}
        if isinstance(meta.get("description"), str) and meta["description"]:
            out["description"] = meta["description"]
        if tools is not None:
            out["tools"] = list(tools)
        model = meta.get("model")
        if isinstance(model, str) and model and model != "inherit":
            out["model"] = model
        text = frontmatter.dump(out, body.strip("\n"))
        items.append(Item("agents", name, {f"agents/{name}.md": text.encode()}))
    return items


# ---------------------------------------------------------------------------------------------
# MCP servers
# ---------------------------------------------------------------------------------------------


def _server_from_entry(name: str, entry: Any, target: str, rep: ImportReport) -> McpServer | None:
    where = f"mcp/{name}"
    if not isinstance(entry, dict):
        rep.warnings.append(f"{where}: not an object; skipped")
        return None
    try:
        validate_name(name, "server")
    except PyAisyncError:
        new = slug(name)
        if new is None:
            rep.warnings.append(f"{where}: invalid server name; skipped")
            return None
        rep.warnings.append(f"{where}: renamed to '{new}'")
        name = new
    kind: str
    command = ""
    args: list[str] = []
    url = ""
    env_raw: Any = None
    headers_raw: Any = None
    if target == "opencode":
        t = entry.get("type")
        if t == "local":
            cmd = entry.get("command")
            if not (isinstance(cmd, list) and cmd and all(isinstance(c, str) for c in cmd)):
                rep.warnings.append(f"{where}: 'command' must be a non-empty array; skipped")
                return None
            kind, command, args = "stdio", cmd[0], list(cmd[1:])
            env_raw = entry.get("environment")
        elif t == "remote":
            kind, url = "http", str(entry.get("url", ""))
            headers_raw = entry.get("headers")
        else:
            rep.warnings.append(f"{where}: unsupported type {t!r}; skipped")
            return None
    else:
        t = entry.get("type")
        if t in ("ws", "websocket"):
            rep.warnings.append(f"{where}: WebSocket servers are not supported; skipped")
            return None
        if target == "cursor":
            t = "stdio" if "command" in entry else ("http" if "url" in entry else t)
        elif t is None:
            t = "stdio"
        if t == "streamable-http":
            t = "http"
        if t not in ("stdio", "http", "sse"):
            rep.warnings.append(f"{where}: unsupported type {t!r}; skipped")
            return None
        kind = t
        if kind == "stdio":
            command = str(entry.get("command", ""))
            a = entry.get("args", [])
            args = [str(x) for x in a] if isinstance(a, list) else []
            env_raw = entry.get("env")
        else:
            url = str(entry.get("url", ""))
            headers_raw = entry.get("headers")
    command, url = _env_in(command, target), _env_in(url, target)
    args = [_env_in(a, target) for a in args]
    if _has_literal_secret(command, url, *args):
        rep.warnings.append(f"{where}: contains a literal secret in its command/args/url; skipped")
        return None
    env, ok1 = _safe_pairs(env_raw, target, where, rep)
    headers, ok2 = _safe_pairs(headers_raw, target, where, rep)
    if not (ok1 and ok2):
        return None
    if (
        kind == "stdio"
        and not command
        or kind != "stdio"
        and not url.startswith(("http://", "https://"))
    ):
        rep.warnings.append(f"{where}: missing command/url; skipped")
        return None
    return McpServer(
        name=name,
        type=kind,
        command=command,
        args=tuple(args),
        env=env,
        url=url,
        headers=headers,
    )


def servers_from(root: Path, target: str, rep: ImportReport, owned: _Owned) -> list[McpServer]:
    kp: tuple[str, ...]
    if target == "claude":
        rel, kp = ".mcp.json", ("mcpServers",)
    elif target == "cursor":
        rel, kp = ".cursor/mcp.json", ("mcpServers",)
    else:
        rel, kp = "opencode.json", ("mcp", "servers")
    data = _load_json(root / rel, rep)
    if data is None:
        return []
    servers = _dig(data, *kp)
    if servers is None:
        if target == "opencode" and isinstance(data.get("mcp"), dict) and data["mcp"]:
            rep.warnings.append(
                "opencode.json: 'mcp' has no 'servers' key; OpenCode V1 config is not supported"
            )
        return []
    if not isinstance(servers, dict):
        rep.warnings.append(f"{rel}: '{'.'.join(kp)}' is not an object; skipped")
        return []
    mine = owned.dict_hashes(rel, kp)
    out: list[McpServer] = []
    for name, entry in sorted(servers.items()):
        if name in mine and mine[name] == mf.value_hash(entry):
            continue  # generated by pyaisync itself
        server = _server_from_entry(str(name), entry, target, rep)
        if server is not None:
            out.append(server)
    return out


# ---------------------------------------------------------------------------------------------
# hooks
# ---------------------------------------------------------------------------------------------


def _hook_from(
    event: str,
    command: Any,
    matcher_tools: tuple[str, ...],
    timeout: Any,
    label: str,
    rep: ImportReport,
    pattern: str = "",
    fail_closed: bool = False,
) -> Hook | None:
    if not isinstance(command, str) or not command.strip() or "\n" in command or "\r" in command:
        rep.warnings.append(f"{label}: command is empty or multi-line; skipped")
        return None
    if SECRET_VALUE.search(command):
        rep.warnings.append(f"{label}: command contains a literal secret; skipped")
        return None
    if matcher_tools and event not in TOOL_MATCHER_EVENTS:
        rep.warnings.append(f"{label}: matcher on a non-tool event cannot be mapped; skipped")
        return None
    t = (
        timeout
        if isinstance(timeout, int) and not isinstance(timeout, bool) and 1 <= timeout <= 3600
        else None
    )
    return Hook(
        event=event,
        command=command.strip(),
        matcher=matcher_tools,
        timeout=t,
        pattern=pattern,
        fail_closed=fail_closed,
    )


def _matcher_tools(
    raw: Any, table: dict[str, str], seps: str, label: str, rep: ImportReport
) -> tuple[str, ...] | None:
    """Tools of a native matcher, () for 'match everything', None when it cannot be mapped."""
    if raw in (None, "", "*"):
        return ()
    if not isinstance(raw, str):
        rep.warnings.append(f"{label}: unrecognized matcher; skipped")
        return None
    tokens = [t.strip() for t in re.split(f"[{re.escape(seps)}]", raw) if t.strip()]
    if not tokens or any(t not in table for t in tokens):
        rep.warnings.append(
            f"{label}: matcher {raw!r} cannot be mapped to canonical tools; skipped"
        )
        return None
    return tuple(dict.fromkeys(table[t] for t in tokens))


def hooks_from(
    root: Path, target: str, rep: ImportReport, owned: _Owned, claims: Claims
) -> list[Hook]:
    if target == "opencode":
        return []
    rel = ".claude/settings.json" if target == "claude" else ".cursor/hooks.json"
    data = _load_json(root / rel, rep)
    hooks = _dig(data, "hooks") if data else None
    if not isinstance(hooks, dict):
        return []
    events_rev = CLAUDE_EVENTS_REV if target == "claude" else CURSOR_EVENTS_REV
    out: list[Hook] = []
    for native, items in sorted(hooks.items()):
        label = f"hooks/{native}"
        if native not in events_rev:
            rep.warnings.append(f"{label}: event has no canonical equivalent; skipped")
            continue
        event = events_rev[native]
        mine = owned.list_hashes(rel, ("hooks", native))
        if not isinstance(items, list):
            continue
        for item in items:
            if not isinstance(item, dict) or mf.value_hash(item) in mine:
                continue
            if target == "claude":
                matcher = _matcher_tools(item.get("matcher"), CLAUDE_TOOLS_REV, "|,", label, rep)
                if matcher is None:
                    continue
                handlers = item.get("hooks", [])
                converted = 0
                for handler in handlers:
                    if not isinstance(handler, dict) or handler.get("type", "command") != "command":
                        rep.warnings.append(
                            f"{label}: only command hooks are imported; skipped one"
                        )
                        continue
                    hook = _hook_from(
                        event, handler.get("command"), matcher, handler.get("timeout"), label, rep
                    )
                    if hook:
                        out.append(hook)
                        converted += 1
                if handlers and converted == len(handlers):  # the whole group was converted
                    claims.setdefault((rel, ("hooks", native)), set()).add(mf.value_hash(item))
            else:
                if item.get("type", "command") != "command":
                    rep.warnings.append(f"{label}: prompt hooks are not imported; skipped")
                    continue
                pattern = ""
                if event in PATTERN_EVENTS:
                    raw_matcher = item.get("matcher")
                    cursor_tools: tuple[str, ...] | None = ()
                    if isinstance(raw_matcher, str) and raw_matcher not in ("", "*"):
                        pattern = raw_matcher
                else:
                    cursor_tools = _matcher_tools(
                        item.get("matcher"), CURSOR_TOOLS_REV, "|", label, rep
                    )
                    if cursor_tools is None:
                        continue
                if item.get("loop_limit") is not None:
                    rep.warnings.append(f"{label}: 'loop_limit' is not imported")
                hook = _hook_from(
                    event,
                    item.get("command"),
                    cursor_tools or (),
                    item.get("timeout"),
                    label,
                    rep,
                    pattern=pattern,
                    fail_closed=item.get("failClosed") is True,
                )
                if hook:
                    out.append(hook)
                    claims.setdefault((rel, ("hooks", native)), set()).add(mf.value_hash(item))
    return out


# --- hook scripts: copy what a hook runs into .ai/hooks/ so the source is self-contained -------------

_SCRIPT_RE = re.compile(
    r"""(?<![\w./-])(?:"?\$\{?CLAUDE_PROJECT_DIR\}?"?/|\./)?"""
    r"""((?:\.cursor|\.claude|\.opencode|hooks)/[^\s"\';|&<>]+)"""
)


def adopt_scripts(root: Path, hooks: list[Hook], rep: ImportReport, write: bool) -> list[Hook]:
    """Copy scripts that hook commands run from tool folders (or `hooks/`) into `.ai/hooks/`.

    The command is rewritten to point at the copy. The original files are never touched.
    """
    ai_hooks = root / ".ai" / "hooks"
    taken: dict[str, str] = {}  # script name -> source it was copied from
    siblings: dict[str, set[str]] = {}
    resolved_root = root.resolve()

    def adopt(match: re.Match[str]) -> str:
        rel = match.group(1)
        src = root / rel
        if src.is_symlink() or not src.is_file():
            return match.group(0)
        try:
            src.resolve().relative_to(resolved_root)
        except ValueError:
            return match.group(0)
        if src.stat().st_size > MAX_FILE_BYTES:
            rep.warnings.append(f"hooks/scripts: {rel} is too large; not copied")
            return match.group(0)
        name = src.name
        data = src.read_bytes()
        dest = ai_hooks / name
        label = f"hooks/scripts/{name}"
        if taken.get(name, rel) != rel:
            rep.warnings.append(
                f"{label}: another script named {name} was already copied; {rel} kept"
            )
            return match.group(0)
        if name not in taken:
            if dest.exists() and dest.read_bytes() != data:
                rep.warnings.append(
                    f"{label}: .ai/hooks/{name} already exists with other content; {rel} kept"
                )
                return match.group(0)
            if dest.exists():
                rep.unchanged.append(label)
            else:
                if write:
                    atomic_write(dest, data, executable=bool(src.stat().st_mode & 0o111))
                rep.written.append(label)
            taken[name] = rel
            siblings.setdefault(str(src.parent.relative_to(root)), set()).add(name)
        return f".ai/hooks/{name}"

    out: list[Hook] = []
    for h in hooks:
        out.append(replace(h, command=_SCRIPT_RE.sub(adopt, h.command)))
    for folder, copied in sorted(siblings.items()):
        others = sorted(
            p.name for p in (root / folder).iterdir() if p.is_file() and p.name not in copied
        )
        others = [o for o in others if o != "hooks.json"]
        if others:
            rep.warnings.append(
                f"hooks/scripts: {folder}/ also holds {others[:5]}; if the scripts need those "
                "files (helpers, config), copy them to .ai/hooks/ yourself"
            )
    return out


# ---------------------------------------------------------------------------------------------
# permissions
# ---------------------------------------------------------------------------------------------

_NATIVE_RULE = re.compile(r"^([A-Za-z_]+)(?:\((.*)\))?$", re.DOTALL)


def _canon(tool: str, pattern: str | None) -> str:
    return tool if pattern in (None, "") else f"{tool}({pattern})"


def _claude_rule(raw: str, effect: str, rep: ImportReport) -> str | None:
    m = _NATIVE_RULE.fullmatch(raw.strip())
    if not m:
        rep.warnings.append(f"permissions: unparsable rule {raw!r}; skipped")
        return None
    name, pattern = m.group(1), m.group(2)
    if name in ("Read", "Edit", "Write", "Bash"):
        tool = {"Read": "read", "Edit": "edit", "Write": "edit", "Bash": "shell"}[name]
        return _canon(tool, pattern)
    if name in ("Grep", "Glob") and pattern is None:
        return name.lower()
    if name in ("WebFetch", "WebSearch"):
        if pattern and pattern.startswith("domain:") and name == "WebFetch":
            return _canon("web", pattern[len("domain:") :])
        if pattern is None and effect != "allow":
            return "web"  # widening is fine for ask/deny
    rep.warnings.append(f"permissions: rule {raw!r} cannot be mapped exactly; skipped")
    return None


def _cursor_rule(raw: str, effect: str, rep: ImportReport) -> str | None:
    m = _NATIVE_RULE.fullmatch(raw.strip())
    if not m or m.group(2) is None:
        rep.warnings.append(f"permissions: unparsable rule {raw!r}; skipped")
        return None
    name, pattern = m.group(1), m.group(2)
    if name == "Shell":
        if pattern in ("*", ""):
            return "shell"
        head, colon, rest = pattern.partition(":")
        if colon:
            return f"shell({head} {rest})"
        # Cursor's Shell(base) matches any arguments: never narrow a deny, never widen an allow.
        return f"shell({pattern})" if effect == "allow" else f"shell({pattern}*)"
    if name in ("Read", "Write"):
        tool = "read" if name == "Read" else "edit"
        return tool if pattern == "**" else f"{tool}({pattern})"
    if name == "WebFetch":
        return (
            "web"
            if pattern == "*" and effect != "allow"
            else _canon("web", pattern)
            if pattern != "*"
            else None
        )
    rep.warnings.append(f"permissions: rule {raw!r} cannot be mapped exactly; skipped")
    return None


def _opencode_rules(items: list[Any], rep: ImportReport) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {"allow": [], "ask": [], "deny": []}
    ranks = [_RANK.get(str(i.get("effect")), -1) for i in items if isinstance(i, dict)]
    if -1 in ranks or ranks != sorted(ranks):
        rep.warnings.append(
            "opencode.json permissions: order-dependent rules (not allow, then ask, then deny) "
            "cannot be mapped; skipped"
        )
        return out
    web: dict[tuple[str, str], set[str]] = {}
    for rule in items:
        if not isinstance(rule, dict):
            continue
        action, res, effect = (
            str(rule.get("action")),
            str(rule.get("resource", "*")),
            str(rule["effect"]),
        )
        if action in ("read", "grep", "glob", "edit", "shell"):
            out[effect].append(_canon(action, None if res == "*" else res))
        elif action in ("webfetch", "websearch"):
            web.setdefault((effect, res), set()).add(action)
        else:
            rep.warnings.append(
                f"permissions: action {action!r} has no canonical equivalent; skipped"
            )
    for (effect, res), acts in web.items():
        if acts == {"webfetch", "websearch"} or effect != "allow":
            out[effect].append(_canon("web", None if res == "*" else res))
        else:
            rep.warnings.append(f"permissions: lone {sorted(acts)} allow cannot be mapped; skipped")
    return out


def permissions_from(
    root: Path, target: str, rep: ImportReport, owned: _Owned, claims: Claims
) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {"allow": [], "ask": [], "deny": []}
    if target == "opencode":
        data = _load_json(root / "opencode.json", rep)
        items = data.get("permissions") if data else None
        if isinstance(items, list):
            mine = owned.list_hashes("opencode.json", ("permissions",))
            fresh = [i for i in items if mf.value_hash(i) not in mine]
            return _opencode_rules(fresh, rep)
        return out
    rel = ".claude/settings.json" if target == "claude" else ".cursor/cli.json"
    data = _load_json(root / rel, rep)
    perms = _dig(data, "permissions") if data else None
    if not isinstance(perms, dict):
        return out
    convert = _claude_rule if target == "claude" else _cursor_rule
    for effect in ("allow", "ask", "deny"):
        raw_list = perms.get(effect, [])
        if not isinstance(raw_list, list):
            continue
        mine = owned.list_hashes(rel, ("permissions", effect))
        for raw in raw_list:
            if not isinstance(raw, str) or mf.value_hash(raw) in mine:
                continue
            rule = convert(raw, effect, rep)
            if rule is not None:
                parse_rule(rule, "import")
                out[effect].append(rule)
                claims.setdefault((rel, ("permissions", effect)), set()).add(mf.value_hash(raw))
    return out


# ---------------------------------------------------------------------------------------------
# writing the aggregate files (.ai/mcp.toml, hooks.toml, permissions.toml)
# ---------------------------------------------------------------------------------------------


def _q(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def _key(key: str) -> str:
    return key if re.fullmatch(r"[A-Za-z0-9_-]+", key) else _q(key)


def _arr(items: Any) -> str:
    return "[" + ", ".join(_q(i) for i in items) + "]"


def _inline(pairs: tuple[tuple[str, str], ...]) -> str:
    return "{ " + ", ".join(f"{_key(k)} = {_q(v)}" for k, v in pairs) + " }"


def dump_mcp(servers: list[McpServer]) -> str:
    out = ["# MCP servers. Use ${VAR} for secrets; never write literal keys here.", ""]
    for s in sorted(servers, key=lambda x: x.name):
        out.append(f"[servers.{_q(s.name)}]")
        out.append(f"type = {_q(s.type)}")
        if s.command:
            out.append(f"command = {_q(s.command)}")
        if s.args:
            out.append(f"args = {_arr(s.args)}")
        if s.env:
            out.append(f"env = {_inline(s.env)}")
        if s.url:
            out.append(f"url = {_q(s.url)}")
        if s.headers:
            out.append(f"headers = {_inline(s.headers)}")
        if s.targets != ("*",):
            out.append(f"targets = {_arr(s.targets)}")
        out.append("")
    return "\n".join(out)


def dump_hooks(hooks: list[Hook]) -> str:
    out = ["# Hooks run COMMANDS on your machine. Review them like code.", ""]
    for h in hooks:
        out.append("[[hooks]]")
        out.append(f"event = {_q(h.event)}")
        if h.matcher:
            out.append(f"matcher = {_arr(h.matcher)}")
        if h.pattern:
            out.append(f"pattern = {_q(h.pattern)}")
        out.append(f"command = {_q(h.command)}")
        if h.fail_closed:
            out.append("fail_closed = true")
        if h.timeout is not None:
            out.append(f"timeout = {h.timeout}")
        if h.targets != ("*",):
            out.append(f"targets = {_arr(h.targets)}")
        out.append("")
    return "\n".join(out)


def dump_permissions(perms: dict[str, list[str]]) -> str:
    out = ["# Rules are 'tool' or 'tool(pattern)'; tools: read, grep, glob, edit, shell, web.", ""]
    for effect in ("allow", "ask", "deny"):
        if perms[effect]:
            out.append(f"{effect} = {_arr(perms[effect])}")
    return "\n".join(out) + "\n"


def _rename_server(s: McpServer, target: str) -> McpServer:
    new = slug(f"{s.name}-{target}") or s.name
    return McpServer(new, s.type, s.command, s.args, s.env, s.url, s.headers, s.targets)


def import_structured(
    root: Path, target: str, rep: ImportReport, on_conflict: str, write: bool
) -> None:
    ai = root / ".ai"
    owned = _Owned(root)

    # --- MCP
    servers = servers_from(root, target, rep, owned)
    if servers:
        existing = {s.name: s for s in _load_mcp(ai / "mcp.toml")}
        changed = False
        for s in servers:
            cur = existing.get(s.name)
            if cur == s:
                rep.unchanged.append(f"mcp/{s.name}")
                continue
            if cur is not None:
                if on_conflict == "skip":
                    rep.skipped.append(
                        f"mcp/{s.name}: already exists in .ai/ (use --on-conflict rename|overwrite)"
                    )
                    continue
                if on_conflict == "rename":
                    s = _rename_server(s, target)
                    if s.name in existing:
                        rep.skipped.append(f"mcp/{s.name}: could not rename without a collision")
                        continue
            existing[s.name] = s
            rep.written.append(f"mcp/{s.name}")
            changed = True
        if changed and write:
            atomic_write(ai / "mcp.toml", dump_mcp(list(existing.values())).encode())

    # --- hooks
    claims: Claims = {}
    hooks = adopt_scripts(root, hooks_from(root, target, rep, owned, claims), rep, write)
    if hooks:
        existing_hooks = _load_hooks(ai / "hooks.toml")
        merged = list(existing_hooks)
        for h in hooks:
            label = f"hooks/{h.event}: {h.command}"
            if h in merged:
                rep.unchanged.append(label)
            else:
                merged.append(h)
                rep.written.append(label)
        if len(merged) != len(existing_hooks) and write:
            atomic_write(ai / "hooks.toml", dump_hooks(merged).encode())

    # --- permissions
    new_perms = permissions_from(root, target, rep, owned, claims)
    if any(new_perms.values()):
        have = _load_permissions(ai / "permissions.toml")
        merged_p = {"allow": list(have.allow), "ask": list(have.ask), "deny": list(have.deny)}
        changed = False
        for effect in ("allow", "ask", "deny"):
            for rule in new_perms[effect]:
                label = f"permissions/{effect}: {rule}"
                if rule in merged_p[effect]:
                    rep.unchanged.append(label)
                    continue
                clash = [e for e in merged_p if e != effect and rule in merged_p[e]]
                if clash:
                    rep.skipped.append(f"{label}: already present under '{clash[0]}'")
                    continue
                merged_p[effect].append(rule)
                rep.written.append(label)
                changed = True
        if changed and write:
            atomic_write(ai / "permissions.toml", dump_permissions(merged_p).encode())

    # --- remember which native list items were converted, so `generate --adopt` replaces them
    if claims and write:
        manifest = mf.load(root)
        for (rel, key_path), hashes in claims.items():
            state = manifest.json.setdefault(rel, mf.JsonFileState(created=False))
            op = state.ops.setdefault(mf.op_id(key_path), mf.JsonOp(key_path, "list"))
            for digest in hashes:
                if digest not in op.entries:
                    op.claimed[digest] = digest
        atomic_write(root / mf.MANIFEST, mf.dumps(manifest))
