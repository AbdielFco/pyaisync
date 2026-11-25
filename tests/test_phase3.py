from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from conftest import write

from pyaisync.cli import main
from pyaisync.errors import PyAisyncError
from pyaisync.loader import load_model

HOOKS = """
[[hooks]]
event = "pre_tool_use"
matcher = ["shell", "edit"]
command = "./scripts/guard.sh"
timeout = 30

[[hooks]]
event = "session_start"
command = "./scripts/hello.sh"
"""

PERMS = """
allow = ["shell(uv run pytest*)", "read"]
ask = ["shell(git push*)"]
deny = ["read(.env*)", "shell(rm -rf*)", "web(evil.example)"]
"""


def run(root: Path, *args: str) -> int:
    return main(["--root", str(root), *args])


def jload(root: Path, rel: str) -> dict[str, Any]:
    data: dict[str, Any] = json.loads((root / rel).read_text())
    return data


@pytest.fixture
def full(repo: Path) -> Path:
    write(repo, ".ai/hooks.toml", HOOKS)
    write(repo, ".ai/permissions.toml", PERMS)
    return repo


def test_hooks_per_target(full: Path, capsys) -> None:  # type: ignore[no-untyped-def]
    assert run(full, "generate") == 0
    out = capsys.readouterr()
    claude = jload(full, ".claude/settings.json")["hooks"]
    assert claude["PreToolUse"] == [
        {
            "matcher": "Bash|Edit|Write",
            "hooks": [{"type": "command", "command": "./scripts/guard.sh", "timeout": 30}],
        }
    ]
    assert claude["SessionStart"] == [
        {"hooks": [{"type": "command", "command": "./scripts/hello.sh"}]}
    ]
    cursor = jload(full, ".cursor/hooks.json")
    assert cursor["version"] == 1
    assert cursor["hooks"]["preToolUse"] == [
        {"command": "./scripts/guard.sh", "timeout": 30, "matcher": "Shell|Write"}
    ]
    assert cursor["hooks"]["sessionStart"] == [{"command": "./scripts/hello.sh"}]
    # OpenCode has no declarative hooks: warned, nothing generated for them
    assert "OpenCode has no declarative hooks" in out.err
    assert "hook" not in json.dumps(jload(full, "opencode.json"))
    # the summary lists every command that will run
    assert "./scripts/guard.sh" in out.out and "./scripts/hello.sh" in out.out
    assert "review them" in out.out


def test_permissions_per_target(full: Path, capsys) -> None:  # type: ignore[no-untyped-def]
    assert run(full, "generate") == 0
    err = capsys.readouterr().err
    p = jload(full, ".claude/settings.json")["permissions"]
    assert p["allow"] == ["Bash(uv run pytest*)", "Read"]
    assert p["ask"] == ["Bash(git push*)"]
    assert p["deny"] == ["Read(.env*)", "Bash(rm -rf*)", "WebFetch(domain:evil.example)"]
    cli = jload(full, ".cursor/cli.json")["permissions"]
    assert cli["allow"] == ["Shell(uv:run pytest*)", "Read(**)"]
    assert cli["deny"] == ["Read(.env*)", "Shell(rm:-rf*)", "WebFetch(evil.example)"]
    assert "ask" not in cli and "Cursor has no 'ask' rules" in err
    assert "Cursor CLI only" in err
    rules = jload(full, "opencode.json")["permissions"]
    effects = [r["effect"] for r in rules]
    assert effects == sorted(effects, key=["allow", "ask", "deny"].index)  # deny last: it wins
    assert {"action": "shell", "resource": "git push*", "effect": "ask"} in rules
    assert {"action": "webfetch", "resource": "evil.example", "effect": "deny"} in rules
    assert {"action": "websearch", "resource": "evil.example", "effect": "deny"} in rules
    assert {"action": "read", "resource": "*", "effect": "allow"} in rules


def test_idempotent_and_check(full: Path) -> None:
    assert run(full, "generate") == 0
    snap = {p: p.read_bytes() for p in full.rglob("*") if p.is_file() and ".git" not in p.parts}
    assert run(full, "generate") == 0
    assert snap == {
        p: p.read_bytes() for p in full.rglob("*") if p.is_file() and ".git" not in p.parts
    }
    assert run(full, "check") == 0


def test_user_settings_are_preserved(full: Path) -> None:
    user = {
        "model": "sonnet",
        "permissions": {"allow": ["Bash(ls)"], "defaultMode": "plan"},
        "hooks": {
            "PreToolUse": [
                {"matcher": "Bash", "hooks": [{"type": "command", "command": "mine.sh"}]}
            ]
        },
    }
    write(full, ".claude/settings.json", json.dumps(user))
    assert run(full, "generate") == 0
    data = jload(full, ".claude/settings.json")
    assert data["model"] == "sonnet" and data["permissions"]["defaultMode"] == "plan"
    assert data["permissions"]["allow"][0] == "Bash(ls)"  # user items stay first
    assert "Bash(uv run pytest*)" in data["permissions"]["allow"]
    assert len(data["hooks"]["PreToolUse"]) == 2
    assert data["hooks"]["PreToolUse"][0]["hooks"][0]["command"] == "mine.sh"


def test_changing_a_hook_replaces_it_without_duplicates(full: Path) -> None:
    run(full, "generate")
    write(full, ".ai/hooks.toml", HOOKS.replace("guard.sh", "guard2.sh"))
    assert run(full, "generate") == 0
    pre = jload(full, ".claude/settings.json")["hooks"]["PreToolUse"]
    assert len(pre) == 1 and pre[0]["hooks"][0]["command"] == "./scripts/guard2.sh"


def test_removing_everything_cleans_up_only_ours(full: Path) -> None:
    write(full, ".claude/settings.json", json.dumps({"permissions": {"allow": ["Bash(ls)"]}}))
    run(full, "generate")
    (full / ".ai/hooks.toml").unlink()
    (full / ".ai/permissions.toml").unlink()
    assert run(full, "generate") == 0
    assert jload(full, ".claude/settings.json") == {"permissions": {"allow": ["Bash(ls)"]}}
    for created_by_us in (".cursor/hooks.json", ".cursor/cli.json", "opencode.json"):
        assert not (full / created_by_us).exists(), created_by_us


def test_cursor_skips_hooks_it_cannot_match(repo: Path, capsys) -> None:  # type: ignore[no-untyped-def]
    write(
        repo,
        ".ai/hooks.toml",
        '[[hooks]]\nevent = "pre_tool_use"\nmatcher = ["glob"]\ncommand = "x.sh"\n',
    )
    assert run(repo, "generate") == 0
    assert "Cursor cannot match glob/web tools" in capsys.readouterr().err
    assert not (repo / ".cursor/hooks.json").exists()  # not widened to "match everything"
    assert jload(repo, ".claude/settings.json")["hooks"]["PreToolUse"][0]["matcher"] == "Glob"


def test_targets_and_features_filters(full: Path) -> None:
    assert run(full, "generate", "--features", "hooks") == 0
    assert (full / ".claude/settings.json").is_file()
    assert "permissions" not in jload(full, ".claude/settings.json")
    assert not (full / ".cursor/cli.json").exists()


def test_hand_edited_json_file_still_refuses_comments(full: Path) -> None:
    write(full, ".claude/settings.json", '{\n // mine\n "model": "x"\n}\n')
    assert run(full, "generate") == 2
    assert "// mine" in (full / ".claude/settings.json").read_text()


@pytest.mark.parametrize(
    "name,content",
    [
        ("hooks", '[[hooks]]\nevent = "nope"\ncommand = "x"\n'),
        ("hooks", '[[hooks]]\nevent = "stop"\ncommand = "x"\nmatcher = ["shell"]\n'),
        ("hooks", '[[hooks]]\nevent = "stop"\ncommand = ""\n'),
        ("hooks", '[[hooks]]\nevent = "stop"\ncommand = "a\\nb"\n'),
        ("hooks", '[[hooks]]\nevent = "stop"\ncommand = "x"\ntimeout = 0\n'),
        ("hooks", '[[hooks]]\nevent = "stop"\ncommand = "x"\ntimeout = "30"\n'),
        ("hooks", '[[hooks]]\nevent = "pre_tool_use"\ncommand = "x"\nmatcher = ["teleport"]\n'),
        (
            "hooks",
            '[[hooks]]\nevent = "stop"\ncommand = "curl -H ghp_abcdefghijklmnopqrstuvwxyz1234 x"\n',
        ),
        ("permissions", 'allow = ["teleport(x)"]\n'),
        ("permissions", 'allow = ["shell()"]\n'),
        ("permissions", 'allow = ["shell(x)"]\ndeny = ["shell(x)"]\n'),
        ("permissions", 'maybe = ["shell"]\n'),
        ("permissions", 'allow = "shell"\n'),
    ],
)
def test_invalid_hooks_and_permissions_rejected(repo: Path, name: str, content: str) -> None:
    write(repo, f".ai/{name}.toml", content)
    with pytest.raises(PyAisyncError):
        load_model(repo)


def test_init_templates_are_valid_and_generate_nothing_extra(tmp_path: Path) -> None:
    assert run(tmp_path, "init") == 0
    assert (tmp_path / ".ai/hooks.toml").is_file() and (tmp_path / ".ai/permissions.toml").is_file()
    assert run(tmp_path, "generate") == 0
    assert not (tmp_path / ".claude/settings.json").exists()
    assert run(tmp_path, "check") == 0
