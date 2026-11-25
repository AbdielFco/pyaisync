from __future__ import annotations

import json
import tomllib
from pathlib import Path
from typing import Any

import pytest
from conftest import write

from pyaisync.cli import main
from pyaisync.loader import load_model

CFG = 'version = 1\ntargets = ["claude", "cursor", "opencode"]\n'


def run(root: Path, *args: str) -> int:
    return main(["--root", str(root), *args])


def toml(root: Path, rel: str) -> dict[str, Any]:
    return tomllib.loads((root / rel).read_text())


def jload(root: Path, rel: str) -> dict[str, Any]:
    data: dict[str, Any] = json.loads((root / rel).read_text())
    return data


@pytest.fixture
def ai(tmp_path: Path) -> Path:
    write(tmp_path, ".ai/config.toml", CFG)
    return tmp_path


# ------------------------------------------------------------------------------------ Claude
def claude_native(root: Path) -> None:
    write(
        root,
        ".claude/agents/rev.md",
        "---\nname: rev\ndescription: Reviewer\ntools: Read, Grep, Glob\nmodel: sonnet\n---\nReview.\n",
    )
    write(
        root,
        ".mcp.json",
        json.dumps(
            {
                "mcpServers": {
                    "docs": {
                        "type": "http",
                        "url": "https://example.com/mcp",
                        "headers": {"Authorization": "Bearer ghp_abcdefghijklmnopqrstuvwxyz1234"},
                    },
                    "tool": {
                        "type": "stdio",
                        "command": "uvx",
                        "args": ["srv"],
                        "env": {"LEVEL": "info", "API_KEY": "${MY_KEY}"},
                    },
                }
            }
        ),
    )
    write(
        root,
        ".claude/settings.json",
        json.dumps(
            {
                "model": "opus",
                "hooks": {
                    "PreToolUse": [
                        {
                            "matcher": "Bash|Edit",
                            "hooks": [{"type": "command", "command": "./g.sh", "timeout": 20}],
                        }
                    ],
                    "Stop": [{"hooks": [{"type": "command", "command": "./bye.sh"}]}],
                },
                "permissions": {
                    "allow": ["Bash(uv run pytest*)", "Read"],
                    "ask": ["Bash(git push*)"],
                    "deny": ["Read(.env*)", "WebFetch(domain:evil.example)"],
                },
            }
        ),
    )


def test_claude_import_and_roundtrip(ai: Path, capsys) -> None:  # type: ignore[no-untyped-def]
    claude_native(ai)
    assert run(ai, "import", "claude") == 0
    err = capsys.readouterr().err
    model = load_model(ai)  # everything imported must be valid canonical input
    agent = model.agents[0]
    assert (agent.name, agent.tools, agent.model) == ("rev", ("read", "grep", "glob"), "sonnet")
    servers = {s.name: s for s in model.mcp_servers}
    # the literal token was NOT written to .ai/ and became a reference
    assert "ghp_" not in (ai / ".ai/mcp.toml").read_text()
    assert dict(servers["docs"].headers)["Authorization"] == "Bearer ${AUTHORIZATION}"
    assert "literal secret" in err
    assert dict(servers["tool"].env) == {"API_KEY": "${MY_KEY}", "LEVEL": "info"}
    hooks = {h.event: h for h in model.hooks}
    assert (
        hooks["pre_tool_use"].matcher == ("shell", "edit") and hooks["pre_tool_use"].timeout == 20
    )
    assert hooks["stop"].command == "./bye.sh"
    assert model.permissions.allow == ("shell(uv run pytest*)", "read")
    assert model.permissions.ask == ("shell(git push*)",)
    assert model.permissions.deny == ("read(.env*)", "web(evil.example)")
    # regenerate for claude only: hooks/permissions/agent match the original semantics
    write(ai, ".ai/config.toml", 'version = 1\ntargets = ["claude"]\n')
    assert run(ai, "generate", "--adopt") == 0
    out = jload(ai, ".claude/settings.json")
    assert out["model"] == "opus"
    # exactly one item: the converted original is replaced, not duplicated
    assert [g["matcher"] for g in out["hooks"]["PreToolUse"]] == ["Bash|Edit|Write"]
    assert out["permissions"]["deny"] == ["Read(.env*)", "WebFetch(domain:evil.example)"]
    assert run(ai, "check") == 0
    # importing again must not re-import what pyaisync generated
    before = (ai / ".ai/hooks.toml").read_text()
    assert run(ai, "import", "claude") == 0
    assert (ai / ".ai/hooks.toml").read_text() == before


def test_claude_unsafe_things_are_skipped_not_widened(ai: Path, capsys) -> None:  # type: ignore[no-untyped-def]
    write(
        ai,
        ".claude/agents/a.md",
        "---\nname: a\ndescription: x\ntools: Read\ndisallowedTools: Bash\n---\nb\n",
    )
    write(ai, ".claude/agents/b.md", "---\nname: b\ndescription: x\ntools: Task, mcp__x\n---\nb\n")
    write(
        ai,
        ".claude/settings.json",
        json.dumps(
            {
                "hooks": {
                    "PreToolUse": [
                        {"matcher": "mcp__.*", "hooks": [{"type": "command", "command": "x"}]},
                        {"matcher": "Bash", "hooks": [{"type": "http", "url": "https://h"}]},
                    ],
                    "Elicitation": [{"hooks": [{"type": "command", "command": "n"}]}],
                },
                "permissions": {"allow": ["WebFetch", "Task", "Bash(ls)"], "deny": ["WebFetch"]},
            }
        ),
    )
    write(
        ai,
        ".mcp.json",
        json.dumps(
            {
                "mcpServers": {
                    "s": {"command": "x", "args": ["--t=ghp_abcdefghijklmnopqrstuvwxyz1234"]}
                }
            }
        ),
    )
    assert run(ai, "import", "claude") == 0
    err = capsys.readouterr()
    assert not (ai / ".ai/agents").exists()  # both agents skipped
    assert not (ai / ".ai/hooks.toml").exists() and not (ai / ".ai/mcp.toml").exists()
    perms = toml(ai, ".ai/permissions.toml")
    assert perms["allow"] == ["shell(ls)"]  # bare WebFetch allow would also allow WebSearch
    assert perms["deny"] == ["web"]  # widening a deny is safe
    assert "disallowedTools" in err.out and "mcp__.*" in err.err


# ------------------------------------------------------------------------------------ Cursor
def test_cursor_import_and_roundtrip(ai: Path) -> None:
    write(
        ai,
        ".cursor/agents/ro.md",
        "---\nname: ro\ndescription: Read only\nreadonly: true\nmodel: inherit\n---\nLook.\n",
    )
    write(
        ai,
        ".cursor/mcp.json",
        json.dumps(
            {
                "mcpServers": {
                    "r": {"url": "https://x.example/mcp", "headers": {"X-Id": "${env:ID}"}},
                    "l": {"command": "npx", "args": ["-y", "s"], "env": {"K": "${env:KK}"}},
                }
            }
        ),
    )
    write(
        ai,
        ".cursor/hooks.json",
        json.dumps(
            {
                "version": 1,
                "hooks": {
                    "preToolUse": [{"command": "./c.sh", "matcher": "Shell|Write", "timeout": 5}],
                    "beforeSubmitPrompt": [{"command": "./p.sh"}],
                    "stop": [{"type": "prompt", "prompt": "is it done?"}],
                },
            }
        ),
    )
    write(
        ai,
        ".cursor/cli.json",
        json.dumps(
            {
                "permissions": {
                    "allow": ["Shell(git)", "Shell(uv:run pytest*)", "Read(src/**)", "Write(**)"],
                    "deny": ["Shell(rm)", "Read(.env*)", "WebFetch(bad.example)"],
                }
            }
        ),
    )
    assert run(ai, "import", "cursor") == 0
    m = load_model(ai)
    assert m.agents[0].tools == ("read", "grep", "glob", "web")
    s = {x.name: x for x in m.mcp_servers}
    assert dict(s["r"].headers) == {"X-Id": "${ID}"} and s["r"].type == "http"
    assert dict(s["l"].env) == {"K": "${KK}"}
    h = {x.event: x for x in m.hooks}
    assert (
        h["pre_tool_use"].matcher == ("shell", "edit")
        and h["user_prompt_submit"].command == "./p.sh"
    )
    assert "stop" not in h  # prompt hooks are not imported
    # allow never widens, deny never narrows
    assert m.permissions.allow == ("shell(git)", "shell(uv run pytest*)", "read(src/**)", "edit")
    assert m.permissions.deny == ("shell(rm*)", "read(.env*)", "web(bad.example)")
    write(ai, ".ai/config.toml", 'version = 1\ntargets = ["cursor"]\n')
    assert run(ai, "generate", "--adopt") == 0
    assert jload(ai, ".cursor/mcp.json")["mcpServers"]["r"]["headers"] == {"X-Id": "${env:ID}"}
    assert jload(ai, ".cursor/hooks.json")["hooks"]["preToolUse"][0]["matcher"] == "Shell|Write"
    assert run(ai, "check") == 0


# ---------------------------------------------------------------------------------- OpenCode
def test_opencode_import_and_roundtrip(ai: Path, capsys) -> None:  # type: ignore[no-untyped-def]
    write(
        ai,
        ".opencode/agents/rv.md",
        "---\ndescription: Rev\nmode: subagent\npermissions:\n"
        "- {action: '*', resource: '*', effect: deny}\n"
        "- {action: read, resource: '*', effect: allow}\n"
        "- {action: webfetch, resource: '*', effect: allow}\n"
        "- {action: websearch, resource: '*', effect: allow}\n---\nBody.\n",
    )
    write(ai, ".opencode/agents/primary.md", "---\ndescription: P\nmode: primary\n---\nx\n")
    write(
        ai,
        ".opencode/agents/odd.md",
        "---\ndescription: O\nmode: subagent\npermissions:\n- {action: shell, resource: 'git *', effect: allow}\n---\nx\n",
    )
    write(
        ai,
        "opencode.json",
        json.dumps(
            {
                "theme": "dark",
                "mcp": {
                    "servers": {
                        "loc": {
                            "type": "local",
                            "command": ["uvx", "s", "--x"],
                            "environment": {"K": "{env:KK}"},
                        },
                        "rem": {"type": "remote", "url": "https://x.example/mcp"},
                    }
                },
                "permissions": [
                    {"action": "shell", "resource": "uv run pytest*", "effect": "allow"},
                    {"action": "shell", "resource": "git push*", "effect": "ask"},
                    {"action": "webfetch", "resource": "bad.example", "effect": "deny"},
                    {"action": "read", "resource": "*.env", "effect": "deny"},
                ],
            }
        ),
    )
    assert run(ai, "import", "opencode") == 0
    err = capsys.readouterr()
    m = load_model(ai)
    assert [a.name for a in m.agents] == ["rv"] and m.agents[0].tools == ("read", "web")
    assert "primary" in err.out and "odd" in err.out  # reported as skipped
    s = {x.name: x for x in m.mcp_servers}
    assert (s["loc"].command, s["loc"].args, dict(s["loc"].env)) == (
        "uvx",
        ("s", "--x"),
        {"K": "${KK}"},
    )
    assert m.permissions.allow == ("shell(uv run pytest*)",)
    assert set(m.permissions.deny) == {
        "web(bad.example)",
        "read(*.env)",
    }  # lone webfetch deny is safe
    write(ai, ".ai/config.toml", 'version = 1\ntargets = ["opencode"]\n')
    assert run(ai, "generate", "--adopt") == 0
    out = jload(ai, "opencode.json")
    assert out["theme"] == "dark" and out["mcp"]["servers"]["loc"]["command"] == ["uvx", "s", "--x"]
    assert run(ai, "check") == 0


def test_opencode_order_dependent_permissions_and_v1_and_jsonc(ai: Path, capsys) -> None:  # type: ignore[no-untyped-def]
    write(
        ai,
        "opencode.json",
        json.dumps(
            {
                "mcp": {"old": {"type": "local", "command": ["x"]}},
                "permissions": [
                    {"action": "shell", "resource": "*", "effect": "deny"},
                    {"action": "shell", "resource": "git status *", "effect": "allow"},
                ],
            }
        ),
    )
    assert run(ai, "import", "opencode") == 0
    err = capsys.readouterr().err
    assert "order-dependent" in err and "V1" in err
    assert not (ai / ".ai/permissions.toml").exists() and not (ai / ".ai/mcp.toml").exists()
    write(ai, "opencode.json", '{\n // c\n "mcp": {}\n}\n')
    assert run(ai, "import", "opencode") == 0
    assert "plain JSON" in capsys.readouterr().err


# ------------------------------------------------------------------------------- behaviours
def test_generated_output_is_never_reimported(ai: Path) -> None:
    write(ai, ".ai/mcp.toml", '[servers.docs]\ntype = "http"\nurl = "https://example.com/mcp"\n')
    write(ai, ".ai/hooks.toml", '[[hooks]]\nevent = "stop"\ncommand = "./b.sh"\n')
    write(ai, ".ai/permissions.toml", 'deny = ["read(.env*)"]\n')
    write(ai, ".ai/agents/a.md", "---\ndescription: A\ntools: [read]\n---\nx\n")
    assert run(ai, "generate") == 0
    snapshot = {p: p.read_bytes() for p in (ai / ".ai").rglob("*") if p.is_file()}
    for target in ("claude", "cursor", "opencode"):
        assert run(ai, "import", target) == 0
    assert snapshot == {p: p.read_bytes() for p in (ai / ".ai").rglob("*") if p.is_file()}


def test_mcp_conflict_policies(ai: Path) -> None:
    write(ai, ".ai/mcp.toml", '[servers.docs]\ntype = "http"\nurl = "https://mine.example/mcp"\n')
    write(
        ai,
        ".mcp.json",
        json.dumps({"mcpServers": {"docs": {"type": "http", "url": "https://theirs.example/mcp"}}}),
    )
    run(ai, "import", "claude")
    assert "mine.example" in (ai / ".ai/mcp.toml").read_text()
    run(ai, "import", "claude", "--on-conflict", "rename")
    names = {s.name for s in load_model(ai).mcp_servers}
    assert names == {"docs", "docs-claude"}
    run(ai, "import", "claude", "--on-conflict", "overwrite")
    assert {s.name: s.url for s in load_model(ai).mcp_servers}[
        "docs"
    ] == "https://theirs.example/mcp"


def test_dry_run_writes_nothing(ai: Path) -> None:
    claude_native(ai)
    assert run(ai, "import", "claude", "--dry-run") == 0
    assert not (ai / ".ai/mcp.toml").exists() and not (ai / ".ai/agents").exists()
    assert not (ai / ".ai/hooks.toml").exists() and not (ai / ".ai/permissions.toml").exists()


def test_init_from_imports_everything(tmp_path: Path) -> None:
    claude_native(tmp_path)
    assert run(tmp_path, "init", "--from", "claude") == 0
    m = load_model(tmp_path)
    assert m.agents and m.mcp_servers and m.hooks and m.permissions
