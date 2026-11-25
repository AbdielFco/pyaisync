from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .config import AI_DIR
from .errors import PyAisyncError

MANIFEST = f"{AI_DIR}/.manifest.json"


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def value_hash(value: Any) -> str:
    """Hash of a JSON value, independent of key order and formatting."""
    return sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode())


@dataclass
class JsonState:
    """Entries pyaisync owns inside a shared JSON file (e.g. `.mcp.json`)."""

    key_path: tuple[str, ...]
    created: bool = False  # the file did not exist before pyaisync wrote it
    entries: dict[str, str] = field(default_factory=dict)  # entry name -> value hash


@dataclass
class Manifest:
    files: dict[str, str] = field(default_factory=dict)
    json: dict[str, JsonState] = field(default_factory=dict)


def load(root: Path) -> Manifest:
    path = root / MANIFEST
    if not path.is_file():
        return Manifest()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        files = {str(k): str(v) for k, v in data["files"].items()}
        shared = {
            str(rel): JsonState(
                key_path=tuple(str(k) for k in st["key_path"]),
                created=bool(st.get("created", False)),
                entries={str(k): str(v) for k, v in st["entries"].items()},
            )
            for rel, st in data.get("json", {}).items()
        }
        return Manifest(files, shared)
    except (ValueError, KeyError, AttributeError, TypeError) as exc:
        raise PyAisyncError(f"{path}: corrupt manifest: {exc}") from exc


def dumps(manifest: Manifest) -> bytes:
    data: dict[str, Any] = {"version": 1, "files": dict(sorted(manifest.files.items()))}
    if manifest.json:
        data["json"] = {
            rel: {
                "key_path": list(st.key_path),
                "created": st.created,
                "entries": dict(sorted(st.entries.items())),
            }
            for rel, st in sorted(manifest.json.items())
        }
    return (json.dumps(data, indent=2) + "\n").encode()
