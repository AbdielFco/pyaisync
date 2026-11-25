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
SECRET_VALUE = re.compile(
    r"(sk-[A-Za-z0-9_-]{16,}|gh[pousr]_[A-Za-z0-9]{20,}|xox[abprs]-[A-Za-z0-9-]{10,}|AKIA[0-9A-Z]{16})"
)


def has_env_ref(value: str) -> bool:
    return _ENV_REF.search(value) is not None


def translate_env(value: str, template: str) -> str:
    """Rewrite canonical `${VAR}` references; `template` contains `{name}`, e.g. '${env:{name}}'."""
    return _ENV_REF.sub(lambda m: template.replace("{name}", m.group(1)), value)


def check_secret(where: str, key: str, value: str) -> None:
    """Secrets never live in the source: sensitive-looking keys must reference `${VAR}`."""
    if SECRET_VALUE.search(value):
        raise PyAisyncError(f"{where}: value of '{key}' looks like a literal secret; use ${{VAR}}")
    if _SECRET_KEY.search(key) and value and not has_env_ref(value):
        raise PyAisyncError(
            f"{where}: '{key}' looks sensitive but holds a literal value; reference "
            "an environment variable with ${VAR} instead"
        )


# ---- hooks ----------------------------------------------------------------------------------

# canonical event -> (Claude Code event, Cursor event). OpenCode has no declarative hooks.
HOOK_EVENTS = {
    "session_start": ("SessionStart", "sessionStart"),
    "session_end": ("SessionEnd", "sessionEnd"),
    "pre_tool_use": ("PreToolUse", "preToolUse"),
    "post_tool_use": ("PostToolUse", "postToolUse"),
    "user_prompt_submit": ("UserPromptSubmit", "beforeSubmitPrompt"),
    "stop": ("Stop", "stop"),
    "subagent_start": ("SubagentStart", "subagentStart"),
    "subagent_stop": ("SubagentStop", "subagentStop"),
    "pre_compact": ("PreCompact", "preCompact"),
}
TOOL_MATCHER_EVENTS = frozenset({"pre_tool_use", "post_tool_use"})
# Cursor tool names usable in preToolUse/postToolUse matchers (glob and web have no equivalent).
CURSOR_TOOLS = {"read": "Read", "grep": "Grep", "edit": "Write", "shell": "Shell"}


def claude_matcher(tools: tuple[str, ...]) -> str:
    return "|".join(dict.fromkeys(n for t in tools for n in CLAUDE_TOOLS[t]))


def cursor_matcher(tools: tuple[str, ...]) -> str | None:
    """Matcher for Cursor, or None when some tool has no Cursor equivalent."""
    if any(t not in CURSOR_TOOLS for t in tools):
        return None
    return "|".join(dict.fromkeys(CURSOR_TOOLS[t] for t in tools))


# ---- permissions ------------------------------------------------------------------------------

_RULE = re.compile(r"^([a-z]+)(?:\((.*)\))?$", re.DOTALL)


def parse_rule(rule: str, where: str) -> tuple[str, str | None]:
    """Parse a canonical rule `tool` or `tool(pattern)` into (tool, pattern | None)."""
    m = _RULE.fullmatch(rule.strip())
    if not m or m.group(1) not in TOOLS:
        raise PyAisyncError(
            f"{where}: invalid rule {rule!r}; expected 'tool' or 'tool(pattern)' "
            f"with tool in {list(TOOLS)}"
        )
    pattern = m.group(2)
    if pattern is not None and not pattern.strip():
        raise PyAisyncError(f"{where}: rule {rule!r} has an empty pattern")
    return m.group(1), pattern
