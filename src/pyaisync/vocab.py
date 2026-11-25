"""Canonical vocabulary (tool names, env references, secret checks) and per-target translation."""

from __future__ import annotations

import re

from .errors import PyAisyncError

# Canonical tool names usable in agents' `tools = [...]`.
TOOLS = ("read", "grep", "glob", "edit", "shell", "web")

CLAUDE_TOOLS = {
    "read": ("Read",),
    "grep": ("Grep",),
    "glob": ("Glob",),
    "edit": ("Edit", "Write"),
    "shell": ("Bash",),
    "web": ("WebFetch", "WebSearch"),
}
OPENCODE_ACTIONS = {
    "read": ("read",),
    "grep": ("grep",),
    "glob": ("glob",),
    "edit": ("edit",),
    "shell": ("shell",),
    "web": ("webfetch", "websearch"),
}
# Tools that can change state: a Cursor subagent without any of them is `readonly`.
WRITE_TOOLS = frozenset({"edit", "shell"})

_ENV_REF = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")
_SECRET_KEY = re.compile(r"(key|token|secret|password|passwd|auth|credential)", re.IGNORECASE)
_SECRET_VALUE = re.compile(
    r"(sk-[A-Za-z0-9_-]{16,}|gh[pousr]_[A-Za-z0-9]{20,}|xox[abprs]-[A-Za-z0-9-]{10,}|AKIA[0-9A-Z]{16})"
)


def has_env_ref(value: str) -> bool:
    return _ENV_REF.search(value) is not None


def translate_env(value: str, template: str) -> str:
    """Rewrite canonical `${VAR}` references; `template` contains `{name}`, e.g. '${env:{name}}'."""
    return _ENV_REF.sub(lambda m: template.replace("{name}", m.group(1)), value)


def check_secret(where: str, key: str, value: str) -> None:
    """Secrets never live in the source: sensitive-looking keys must reference `${VAR}`."""
    if _SECRET_VALUE.search(value):
        raise PyAisyncError(f"{where}: value of '{key}' looks like a literal secret; use ${{VAR}}")
    if _SECRET_KEY.search(key) and value and not has_env_ref(value):
        raise PyAisyncError(
            f"{where}: '{key}' looks sensitive but holds a literal value; reference "
            "an environment variable with ${VAR} instead"
        )
