from __future__ import annotations

import tomllib
from pathlib import Path

from .errors import PyAisyncError
from .model import ALL_TARGETS, FEATURES, Config

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
    return Config(targets=targets, features=feats)
