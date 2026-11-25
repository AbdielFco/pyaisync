from __future__ import annotations

import hashlib
import json
from pathlib import Path

from .config import AI_DIR
from .errors import PyAisyncError

MANIFEST = f"{AI_DIR}/.manifest.json"


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def load(root: Path) -> dict[str, str]:
    path = root / MANIFEST
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        files = data["files"]
        assert isinstance(files, dict)
        return {str(k): str(v) for k, v in files.items()}
    except (ValueError, KeyError, AssertionError) as exc:
        raise PyAisyncError(f"{path}: corrupt manifest: {exc}") from exc


def dumps(files: dict[str, str]) -> bytes:
    return (
        json.dumps({"version": 1, "files": dict(sorted(files.items()))}, indent=2) + "\n"
    ).encode()
