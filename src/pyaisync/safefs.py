"""Safe file access: no symlinks, stay inside the root, atomic writes."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

from .errors import PyAisyncError

MAX_FILE_BYTES = 2 * 1024 * 1024
MAX_SKILL_FILES = 200


def safe_join(root: Path, rel: str) -> Path:
    """Join `rel` onto `root`; reject absolute paths, '..' and symlinks in any existing segment."""
    if not rel or rel.startswith("/") or "\\" in rel or ".." in Path(rel).parts:
        raise PyAisyncError(f"path not allowed: {rel!r}")
    cur = root
    for part in Path(rel).parts:
        cur = cur / part
        if cur.is_symlink():
            raise PyAisyncError(f"path contains a symlink, which is not allowed: {rel}")
    resolved_root = root.resolve()
    if (
        resolved_root not in cur.resolve(strict=False).parents
        and cur.resolve(strict=False) != resolved_root
    ):
        raise PyAisyncError(f"path outside the root: {rel}")
    return cur


def read_text(path: Path) -> str:
    if path.is_symlink():
        raise PyAisyncError(f"{path}: symlinks are not followed")
    if path.stat().st_size > MAX_FILE_BYTES:
        raise PyAisyncError(f"{path}: file too large")
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise PyAisyncError(f"{path}: is not valid UTF-8") from exc


def read_tree(directory: Path) -> dict[str, bytes]:
    """Read a file tree (for skills). Hidden entries are ignored; symlinks are an error."""
    files: dict[str, bytes] = {}
    for dirpath, dirnames, filenames in os.walk(directory, followlinks=False):
        dirnames[:] = sorted(d for d in dirnames if not d.startswith("."))
        for d in list(dirnames):
            if (Path(dirpath) / d).is_symlink():
                raise PyAisyncError(f"{directory}: symlink not allowed: {d}")
        for fn in sorted(filenames):
            if fn.startswith("."):
                continue
            p = Path(dirpath) / fn
            if p.is_symlink():
                raise PyAisyncError(f"{directory}: symlink not allowed: {fn}")
            if p.stat().st_size > MAX_FILE_BYTES:
                raise PyAisyncError(f"{p}: file too large")
            files[p.relative_to(directory).as_posix()] = p.read_bytes()
            if len(files) > MAX_SKILL_FILES:
                raise PyAisyncError(f"{directory}: too many files (> {MAX_SKILL_FILES})")
    return files


def _default_mode() -> int:
    umask = os.umask(0)
    os.umask(umask)
    return 0o666 & ~umask


def tree_modes(directory: Path) -> frozenset[str]:
    """Relative paths of the files `read_tree` would read that have an executable bit."""
    found: set[str] = set()
    for dirpath, dirnames, filenames in os.walk(directory, followlinks=False):
        dirnames[:] = [d for d in dirnames if not d.startswith(".")]
        for fn in filenames:
            p = Path(dirpath) / fn
            if not fn.startswith(".") and not p.is_symlink() and p.stat().st_mode & 0o111:
                found.add(p.relative_to(directory).as_posix())
    return frozenset(found)


def atomic_write(path: Path, data: bytes, *, executable: bool = False) -> None:
    """Write via a temp file + rename. Mode is the umask default (0644-ish), +x when asked."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".pyaisync-", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
        mode = _default_mode()
        os.chmod(tmp, mode | 0o111 if executable else mode)
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise
