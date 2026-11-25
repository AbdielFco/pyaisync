from __future__ import annotations

import re
import tomllib
from pathlib import Path

from .errors import PyAisyncError
from .model import ALL_TARGETS, FEATURES, Config, PluginMeta
from .safefs import atomic_write

AI_DIR = ".ai"


def load_config(root: Path) -> Config:
    path = root / AI_DIR / "config.toml"
    if not path.is_file():
        raise PyAisyncError(f"{AI_DIR}/config.toml not found (did you run 'pyaisync init'?)")
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as exc:
        raise PyAisyncError(f"{path}: invalid TOML: {exc}") from exc
    if data.get("version") != 1:
        raise PyAisyncError(f"{path}: 'version' must be 1")
    targets = tuple(data.get("targets", ALL_TARGETS))
    bad = [t for t in targets if t not in ALL_TARGETS]
    if bad:
        raise PyAisyncError(f"{path}: unknown targets {bad}; valid: {list(ALL_TARGETS)}")
    feats = {f: True for f in FEATURES}
    for key, val in data.get("features", {}).items():
        if key not in FEATURES:
            raise PyAisyncError(f"{path}: unknown feature '{key}'; valid: {list(FEATURES)}")
        if not isinstance(val, bool):
            raise PyAisyncError(f"{path}: features.{key} must be true or false")
        feats[key] = val
    return Config(targets=targets, features=feats, plugin=_plugin(data.get("plugin"), path))


_RESERVED_PREFIXES = ("claude-", "anthropic-", "anthropics-", "cc-plugin-")
_RESERVED_NAMES = {"claude", "anthropic", "anthropics", "claude-code", "claude-mods"}


def _plugin(raw: object, path: Path) -> PluginMeta | None:
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise PyAisyncError(f"{path}: [plugin] must be a table")
    unknown = set(raw) - {"name", "version", "description", "author", "license"}
    if unknown:
        raise PyAisyncError(f"{path}: unknown [plugin] keys {sorted(unknown)}")
    for key, val in raw.items():
        if not isinstance(val, str):
            raise PyAisyncError(f"{path}: plugin.{key} must be a string")
    name = raw.get("name", "")
    if not re.fullmatch(r"[a-z0-9]+(-[a-z0-9]+)*", name):
        raise PyAisyncError(f"{path}: plugin.name must be kebab-case (a-z, 0-9, '-')")
    if name in _RESERVED_NAMES or name.startswith(_RESERVED_PREFIXES):
        raise PyAisyncError(f"{path}: plugin.name '{name}' is reserved by Claude Code")
    return PluginMeta(
        name=name,
        version=raw.get("version", "0.1.0"),
        description=raw.get("description", ""),
        author=raw.get("author", ""),
        license=raw.get("license", ""),
    )


_VERSION_LINE = re.compile(r"^version\s*=.*$", re.MULTILINE)
_TARGETS_KEY = re.compile(r"^targets\s*=\s*\[.*?\]", re.MULTILINE | re.DOTALL)
_FIRST_TABLE = re.compile(r"^\[", re.MULTILINE)


def set_targets(root: Path, targets: tuple[str, ...]) -> None:
    """Rewrite only the top-level `targets` list of `.ai/config.toml`, keeping comments and tables."""
    bad = [t for t in targets if t not in ALL_TARGETS]
    if bad or not targets:
        raise PyAisyncError(f"invalid targets {list(targets)}; valid: {list(ALL_TARGETS)}")
    path = root / AI_DIR / "config.toml"
    if not path.is_file():
        raise PyAisyncError(f"{AI_DIR}/config.toml not found (did you run 'pyaisync init'?)")
    text = path.read_text(encoding="utf-8")
    line = "targets = [" + ", ".join(f'"{t}"' for t in targets) + "]"
    table = _FIRST_TABLE.search(text)
    head, tail = (text[: table.start()], text[table.start() :]) if table else (text, "")
    if _TARGETS_KEY.search(head):
        head = _TARGETS_KEY.sub(lambda _m: line, head, count=1)
    elif _VERSION_LINE.search(head):
        head = _VERSION_LINE.sub(lambda m: m.group(0) + "\n" + line, head, count=1)
    else:
        head = line + "\n" + head
    new_text = head + tail
    try:
        ok = tomllib.loads(new_text).get("targets") == list(targets)
    except tomllib.TOMLDecodeError:
        ok = False
    if not ok:  # never write something we could not verify
        raise PyAisyncError(f"could not update 'targets' in {path}; edit it by hand")
    atomic_write(path, new_text.encode("utf-8"))
