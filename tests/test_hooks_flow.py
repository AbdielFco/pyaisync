from __future__ import annotations

import json
import os
import stat
from pathlib import Path
from typing import Any

import pytest
from conftest import write

from pyaisync.cli import main
from pyaisync.errors import PyAisyncError
from pyaisync.loader import load_model


def run(root: Path, *args: str) -> int:
    return main(["--root", str(root), *args])


def jload(root: Path, rel: str) -> dict[str, Any]:
    data: dict[str, Any] = json.loads((root / rel).read_text())
    return data


CURSOR_HOOKS = {
    "version": 1,
    "hooks": {
        "beforeShellExecution": [
            {
                "command": "python3 .cursor/hooks/gates.py",
                "matcher": "git push|rm ",
                "failClosed": True,
                "timeout": 10,
            }
        ],
        "afterFileEdit": [{"command": "python3 .cursor/hooks/format.py"}],
        "stop": [{"command": "python3 ./.cursor/hooks/gates.py --stop", "loop_limit": 3}],
    },
}


@pytest.fixture
def cursor_repo(tmp_path: Path) -> Path:
    script = write(tmp_path, ".cursor/hooks/gates.py", "import sys\n")
    script.chmod(0o755)
    write(tmp_path, ".cursor/hooks/format.py", "x = 1\n")
    write(tmp_path, ".cursor/hooks/config.json", "{}")
    write(tmp_path, ".cursor/hooks.json", json.dumps(CURSOR_HOOKS))
    return tmp_path


def test_cursor_hooks_import_events_scripts_and_flags(cursor_repo: Path, capsys) -> None:  # type: ignore[no-untyped-def]
    assert run(cursor_repo, "init", "--from", "cursor") == 0
    out = capsys.readouterr()
    hooks = {h.event: h for h in load_model(cursor_repo).hooks}
    assert set(hooks) == {"before_shell_execution", "after_file_edit", "stop"}
    gate = hooks["before_shell_execution"]
    assert (gate.pattern, gate.fail_closed, gate.timeout) == ("git push|rm ", True, 10)
    assert gate.command == "python3 .ai/hooks/gates.py"
    assert hooks["stop"].command == "python3 .ai/hooks/gates.py --stop"  # "./" form handled too
    # scripts were copied (never moved), the executable bit survived, originals are untouched
    assert (cursor_repo / ".ai/hooks/gates.py").read_text() == "import sys\n"
    assert os.stat(cursor_repo / ".ai/hooks/gates.py").st_mode & 0o111
    assert not os.stat(cursor_repo / ".ai/hooks/format.py").st_mode & 0o111
    assert (cursor_repo / ".cursor/hooks/gates.py").is_file()
    assert json.loads((cursor_repo / ".cursor/hooks.json").read_text()) == CURSOR_HOOKS
    # what could not be carried over is reported, not silently dropped
    assert "'loop_limit' is not imported" in out.err
    assert "config.json" in out.err  # a neighbour file the scripts might need
    assert "generate --adopt" in out.out  # the flow tells you what to do next


def test_first_generate_after_import_replaces_the_originals_not_duplicates(
    cursor_repo: Path,
) -> None:
    run(cursor_repo, "init", "--from", "cursor")
    # without consent nothing is touched: the converted originals would be replaced
    assert run(cursor_repo, "generate") == 2
    assert json.loads((cursor_repo / ".cursor/hooks.json").read_text()) == CURSOR_HOOKS
    assert run(cursor_repo, "generate", "--adopt") == 0
    hooks = jload(cursor_repo, ".cursor/hooks.json")["hooks"]
    assert hooks["beforeShellExecution"] == [
        {
            "command": "python3 .ai/hooks/gates.py",
            "timeout": 10,
            "matcher": "git push|rm ",
            "failClosed": True,
        }
    ]
    assert hooks["afterFileEdit"] == [{"command": "python3 .ai/hooks/format.py"}]
    assert hooks["stop"] == [{"command": "python3 .ai/hooks/gates.py --stop"}]
    assert run(cursor_repo, "check") == 0
    assert run(cursor_repo, "generate") == 0  # and it stays quiet afterwards


def test_user_hooks_that_were_not_imported_survive(cursor_repo: Path) -> None:
    data = json.loads(json.dumps(CURSOR_HOOKS))
    data["hooks"]["stop"].append({"type": "prompt", "prompt": "done?"})
    data["hooks"]["preCompact"] = [{"command": "mine.sh"}]  # imported, identical after generate
    write(cursor_repo, ".cursor/hooks.json", json.dumps(data))
    run(cursor_repo, "init", "--from", "cursor")
    assert run(cursor_repo, "generate", "--adopt") == 0
    out = jload(cursor_repo, ".cursor/hooks.json")["hooks"]
    assert {"type": "prompt", "prompt": "done?"} in out["stop"]  # prompt hooks are never touched
    assert out["preCompact"] == [{"command": "mine.sh"}]


def test_claude_gets_project_dir_paths_and_skips_cursor_only_events(
    cursor_repo: Path, capsys
) -> None:  # type: ignore[no-untyped-def]
    run(cursor_repo, "init", "--from", "cursor")
    write(cursor_repo, ".ai/config.toml", 'version = 1\ntargets = ["cursor", "claude"]\n')
    assert run(cursor_repo, "generate", "--adopt") == 0
    err = capsys.readouterr().err
    claude = jload(cursor_repo, ".claude/settings.json")["hooks"]
    assert list(claude) == ["Stop"]  # the Cursor-only events are not invented for Claude
    assert claude["Stop"][0]["hooks"][0]["command"] == (
        'python3 "${CLAUDE_PROJECT_DIR}"/.ai/hooks/gates.py --stop'
    )
    assert "Claude Code has no such event" in err


def test_fail_closed_is_cursor_only_and_warned_for_claude(repo: Path, capsys) -> None:  # type: ignore[no-untyped-def]
    write(
        repo,
        ".ai/hooks.toml",
        '[[hooks]]\nevent = "stop"\ncommand = "./g.sh"\nfail_closed = true\n',
    )
    assert run(repo, "generate") == 0
    assert "'fail_closed' has no Claude Code equivalent" in capsys.readouterr().err
    assert jload(repo, ".cursor/hooks.json")["hooks"]["stop"][0]["failClosed"] is True
    assert "failClosed" not in json.dumps(jload(repo, ".claude/settings.json"))


def test_claude_only_event_is_skipped_for_cursor(repo: Path, capsys) -> None:  # type: ignore[no-untyped-def]
    write(repo, ".ai/hooks.toml", '[[hooks]]\nevent = "notification"\ncommand = "./n.sh"\n')
    assert run(repo, "generate") == 0
    assert "Cursor has no such event" in capsys.readouterr().err
    assert jload(repo, ".claude/settings.json")["hooks"]["Notification"]
    assert not (repo / ".cursor/hooks.json").exists()


def test_claude_hook_script_under_claude_dir_is_imported(tmp_path: Path) -> None:
    write(tmp_path, ".claude/hooks/check.sh", "#!/bin/sh\n").chmod(0o755)
    write(
        tmp_path,
        ".claude/settings.json",
        json.dumps(
            {
                "hooks": {
                    "PostToolUse": [
                        {
                            "matcher": "Write|Edit",
                            "hooks": [
                                {
                                    "type": "command",
                                    "command": '"${CLAUDE_PROJECT_DIR}"/.claude/hooks/check.sh',
                                }
                            ],
                        }
                    ]
                }
            }
        ),
    )
    assert run(tmp_path, "init", "--from", "claude") == 0
    hook = load_model(tmp_path).hooks[0]
    assert hook.command == ".ai/hooks/check.sh" and hook.matcher == ("edit",)
    assert os.stat(tmp_path / ".ai/hooks/check.sh").st_mode & 0o111
    assert run(tmp_path, "generate", "--adopt") == 0
    group = jload(tmp_path, ".claude/settings.json")["hooks"]["PostToolUse"]
    assert len(group) == 1  # the original group was replaced, not duplicated
    assert group[0]["hooks"][0]["command"] == '"${CLAUDE_PROJECT_DIR}"/.ai/hooks/check.sh'


def test_script_import_safety(tmp_path: Path) -> None:
    write(tmp_path, ".ai/config.toml", 'version = 1\ntargets = ["cursor"]\n')
    write(tmp_path, ".ai/hooks/gates.py", "different content\n")  # already there, other content
    write(tmp_path, ".cursor/hooks/gates.py", "mine\n")
    secret = write(tmp_path, "secret.txt", "s\n")
    os.symlink(secret, tmp_path / ".cursor/hooks/link.py")
    write(
        tmp_path,
        ".cursor/hooks.json",
        json.dumps(
            {
                "version": 1,
                "hooks": {
                    "stop": [{"command": "python3 .cursor/hooks/gates.py"}],
                    "sessionEnd": [{"command": "python3 .cursor/hooks/link.py"}],
                },
            }
        ),
    )
    assert run(tmp_path, "import", "cursor") == 0
    hooks = {h.event: h.command for h in load_model(tmp_path).hooks}
    assert (tmp_path / ".ai/hooks/gates.py").read_text() == "different content\n"  # not overwritten
    assert hooks["stop"] == "python3 .cursor/hooks/gates.py"  # original path kept
    assert hooks["session_end"] == "python3 .cursor/hooks/link.py"  # symlinks are never copied
    assert not (tmp_path / ".ai/hooks/link.py").exists()


def test_missing_hook_script_is_warned(repo: Path, capsys) -> None:  # type: ignore[no-untyped-def]
    write(
        repo, ".ai/hooks.toml", '[[hooks]]\nevent = "stop"\ncommand = "python3 .ai/hooks/nope.py"\n'
    )
    assert run(repo, "generate") == 0
    assert "hook script not found: .ai/hooks/nope.py" in capsys.readouterr().err
    write(repo, ".ai/hooks/nope.py", "x\n")
    run(repo, "generate")
    assert "hook script not found" not in capsys.readouterr().err


@pytest.mark.parametrize(
    "toml",
    [
        '[[hooks]]\nevent = "stop"\ncommand = "x"\npattern = "a|b"\n',  # pattern: Cursor-only events
        '[[hooks]]\nevent = "after_file_edit"\ncommand = "x"\nmatcher = ["shell"]\n',
        '[[hooks]]\nevent = "after_file_edit"\ncommand = "x"\npattern = 3\n',
        '[[hooks]]\nevent = "stop"\ncommand = "x"\nfail_closed = "yes"\n',
    ],
)
def test_invalid_hook_fields_rejected(repo: Path, toml: str) -> None:
    write(repo, ".ai/hooks.toml", toml)
    with pytest.raises(PyAisyncError):
        load_model(repo)


def test_post_tool_use_failure_takes_a_tool_matcher(repo: Path) -> None:
    write(
        repo,
        ".ai/hooks.toml",
        '[[hooks]]\nevent = "post_tool_use_failure"\nmatcher = ["shell"]\ncommand = "./f.sh"\n',
    )
    assert run(repo, "generate") == 0
    assert (
        jload(repo, ".claude/settings.json")["hooks"]["PostToolUseFailure"][0]["matcher"] == "Bash"
    )
    assert jload(repo, ".cursor/hooks.json")["hooks"]["postToolUseFailure"][0]["matcher"] == "Shell"


# ------------------------------------------------------------------ file modes and init flow
def test_generated_files_use_normal_permissions_and_skill_scripts_stay_executable(
    repo: Path,
) -> None:
    (repo / ".ai/skills/deploy/scripts/run.sh").chmod(0o755)
    assert run(repo, "generate") == 0
    umask = os.umask(0)
    os.umask(umask)
    assert stat.S_IMODE((repo / "CLAUDE.md").stat().st_mode) == 0o666 & ~umask  # not 0600
    assert (repo / ".claude/skills/deploy/scripts/run.sh").stat().st_mode & 0o111
    assert not (repo / ".claude/skills/deploy/SKILL.md").stat().st_mode & 0o111


def test_imported_skill_scripts_keep_their_executable_bit(tmp_path: Path) -> None:
    write(tmp_path, ".claude/skills/s/SKILL.md", "---\nname: s\ndescription: d\n---\nx\n")
    write(tmp_path, ".claude/skills/s/run.sh", "#!/bin/sh\n").chmod(0o755)
    run(tmp_path, "init", "--from", "claude")
    assert (tmp_path / ".ai/skills/s/run.sh").stat().st_mode & 0o111


def test_init_guides_a_fresh_project(tmp_path: Path, capsys) -> None:  # type: ignore[no-untyped-def]
    assert run(tmp_path, "init") == 0
    out = capsys.readouterr().out
    assert "Edit the examples" in out and "pyaisync generate" in out
    assert (tmp_path / ".ai/rules/project.md").is_file()


def test_init_in_a_repo_with_existing_config_points_to_import(tmp_path: Path, capsys) -> None:  # type: ignore[no-untyped-def]
    write(tmp_path, "CLAUDE.md", "# mine\n")
    (tmp_path / ".cursor").mkdir()
    assert run(tmp_path, "init") == 0
    out = capsys.readouterr().out
    assert "pyaisync import claude" in out and "pyaisync import cursor" in out
    assert not (tmp_path / ".ai/rules").exists()  # no example files next to real configuration


def test_init_from_suggests_the_other_tools_too(tmp_path: Path, capsys) -> None:  # type: ignore[no-untyped-def]
    write(tmp_path, "CLAUDE.md", "# mine\n")
    (tmp_path / ".cursor").mkdir()
    assert run(tmp_path, "init", "--from", "claude") == 0
    out = capsys.readouterr().out
    assert "Also found cursor configuration: pyaisync import cursor" in out
    assert "generate --adopt" in out
