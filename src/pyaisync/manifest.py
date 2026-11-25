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


def op_id(key_path: tuple[str, ...]) -> str:
    return "/".join(key_path)


@dataclass
class JsonOp:
    """What pyaisync owns at one location of a shared JSON file.

    kind "dict": `entries` maps entry name -> hash of its value (e.g. MCP servers).
    kind "list": `entries` maps item hash -> item hash (e.g. hook groups, permission rules).
    """

    key_path: tuple[str, ...]
    kind: str
    entries: dict[str, str] = field(default_factory=dict)


@dataclass
class JsonFileState:
    created: bool = False  # the file did not exist before pyaisync wrote it
    ops: dict[str, JsonOp] = field(default_factory=dict)


@dataclass
class Manifest:
    files: dict[str, str] = field(default_factory=dict)
    json: dict[str, JsonFileState] = field(default_factory=dict)


def load(root: Path) -> Manifest:
    path = root / MANIFEST
    if not path.is_file():
        return Manifest()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        files = {str(k): str(v) for k, v in data["files"].items()}
        shared: dict[str, JsonFileState] = {}
        for rel, st in data.get("json", {}).items():
            ops = {
                str(oid): JsonOp(
                    key_path=tuple(str(k) for k in op["key_path"]),
                    kind=str(op["kind"]),
                    entries={str(k): str(v) for k, v in op["entries"].items()},
                )
                for oid, op in st["ops"].items()
            }
            shared[str(rel)] = JsonFileState(created=bool(st.get("created", False)), ops=ops)
        return Manifest(files, shared)
    except (ValueError, KeyError, AttributeError, TypeError) as exc:
        raise PyAisyncError(f"{path}: corrupt manifest: {exc}") from exc


def dumps(manifest: Manifest) -> bytes:
    data: dict[str, Any] = {"version": 1, "files": dict(sorted(manifest.files.items()))}
    if manifest.json:
        data["json"] = {
            rel: {
                "created": st.created,
                "ops": {
                    oid: {
                        "key_path": list(op.key_path),
                        "kind": op.kind,
                        "entries": dict(sorted(op.entries.items())),
                    }
                    for oid, op in sorted(st.ops.items())
                },
            }
            for rel, st in sorted(manifest.json.items())
        }
    return (json.dumps(data, indent=2) + "\n").encode()
