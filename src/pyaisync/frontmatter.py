"""Minimal Markdown + YAML frontmatter parser (yaml.safe_load only)."""

from __future__ import annotations

from typing import Any

import yaml

from .errors import PyAisyncError

_DELIM = "---"


def parse(text: str, source: str = "<text>") -> tuple[dict[str, Any], str]:
    text = text.replace("\r\n", "\n")
    if not text.startswith(_DELIM + "\n"):
        return {}, text
    end = text.find("\n" + _DELIM, len(_DELIM))
    if end == -1:
        raise PyAisyncError(f"{source}: frontmatter is missing its closing '---'")
    raw = text[len(_DELIM) + 1 : end]
    rest = text[end + len(_DELIM) + 1 :]
    rest = rest.removeprefix("\n")
    try:
        data = yaml.safe_load(raw) if raw.strip() else {}
    except yaml.YAMLError as exc:
        raise PyAisyncError(f"{source}: invalid YAML frontmatter: {exc}") from exc
    if data is None:
        data = {}
    if not isinstance(data, dict):
        raise PyAisyncError(f"{source}: frontmatter must be a YAML mapping")
    return data, rest


def dump(meta: dict[str, Any], body: str) -> str:
    """Serialize deterministically (keys keep the caller's order)."""
    body = body.lstrip("\n")
    if not meta:
        return body if body.endswith("\n") or not body else body + "\n"
    head = yaml.safe_dump(meta, sort_keys=False, allow_unicode=True, width=1000).rstrip("\n")
    out = f"{_DELIM}\n{head}\n{_DELIM}\n\n{body}"
    return out if out.endswith("\n") else out + "\n"
