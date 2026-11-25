from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from conftest import write

from pyaisync.cli import main
from pyaisync.errors import PyAisyncError
from pyaisync.loader import load_model

PLUGIN_CFG = (
    'version = 1\ntargets = ["claude", "cursor", "opencode"]\n\n'
    '[plugin]\nname = "my-tools"\nversion = "1.2.0"\ndescription = "Team tools"\n'
    'author = "Team"\nlicense = "MIT"\n'
)


def run(root: Path, *args: str) -> int:
    return main(["--root", str(root), *args])


def jload(path: Path) -> dict[str, Any]:
    data: dict[str, Any] = json.loads(path.read_text())
    return data


@pytest.fixture
def pkg(repo: Path) -> Path:
    write(repo, ".ai/config.toml", PLUGIN_CFG)
    write(
        repo, ".ai/agents/rev.md", "---\ndescription: Reviewer\ntools: [read, grep]\n---\nReview.\n"
    )
    write(repo, ".ai/mcp.toml", '[servers.docs]\ntype = "http"\nurl = "https://example.com/mcp"\n')
    write(
        repo,
        ".ai/hooks.toml",
        '[[hooks]]\nevent = "stop"\ncommand = "${CLAUDE_PLUGIN_ROOT}/bye.sh"\n',
    )
    write(repo, ".ai/permissions.toml", 'deny = ["read(.env*)"]\n')
    return repo


def test_plugin_layout(pkg: Path, capsys) -> None:  # type: ignore[no-untyped-def]
    assert run(pkg, "plugin", "claude") == 0
    out = pkg / "dist/my-tools"
    err = capsys.readouterr().err
    assert jload(out / ".claude-plugin/plugin.json") == {
        "name": "my-tools",
        "version": "1.2.0",
        "description": "Team tools",
        "author": {"name": "Team"},
        "license": "MIT",
    }
    assert (out / "commands/review.md").is_file()
    assert "tools: Read, Grep" in (out / "agents/rev.md").read_text()
    assert (out / "skills/deploy/SKILL.md").is_file() and (
        out / "skills/deploy/scripts/run.sh"
    ).is_file()
    assert jload(out / ".mcp.json")["mcpServers"]["docs"]["url"] == "https://example.com/mcp"
    assert jload(out / "hooks/hooks.json")["hooks"]["Stop"][0]["hooks"][0]["command"] == (
        "${CLAUDE_PLUGIN_ROOT}/bye.sh"
    )
    # nothing a plugin cannot carry leaks into it
    assert not (out / "CLAUDE.md").exists() and not (out / ".claude").exists()
    assert not (out / "settings.json").exists()
    assert "rules are not part of a plugin" in err and "permissions are not part" in err
    assert "scripts are NOT bundled" in err
    # the project itself is untouched
    assert not (pkg / "CLAUDE.md").exists()


def test_plugin_is_idempotent_and_non_destructive(pkg: Path) -> None:
    assert run(pkg, "plugin", "claude", "--out", "build/p") == 0
    out = pkg / "build/p"
    snap = {p: p.read_bytes() for p in out.rglob("*") if p.is_file()}
    assert run(pkg, "plugin", "claude", "--out", "build/p") == 0
    assert snap == {p: p.read_bytes() for p in out.rglob("*") if p.is_file()}
    write(out, "agents/rev.md", "hand edited\n")
    assert run(pkg, "plugin", "claude", "--out", "build/p") == 2
    assert (out / "agents/rev.md").read_text() == "hand edited\n"
    write(out, "extra/mine.txt", "mine\n")
    assert run(pkg, "plugin", "claude", "--out", "build/p", "--force") == 0
    assert (out / "extra/mine.txt").read_text() == "mine\n"  # files it does not own are kept


def test_removed_component_disappears_from_plugin(pkg: Path) -> None:
    run(pkg, "plugin", "claude")
    (pkg / ".ai/agents/rev.md").unlink()
    run(pkg, "plugin", "claude")
    assert not (pkg / "dist/my-tools/agents").exists()


def test_plugin_dry_run_and_unmanaged_target(pkg: Path) -> None:
    assert run(pkg, "plugin", "claude", "--dry-run") == 0
    assert not (pkg / "dist").exists()
    write(pkg, "dist/my-tools/commands/review.md", "someone else's\n")
    assert run(pkg, "plugin", "claude") == 2
    assert (pkg / "dist/my-tools/commands/review.md").read_text() == "someone else's\n"


def test_plugin_refuses_to_overwrite_the_repo(pkg: Path) -> None:
    assert run(pkg, "plugin", "claude", "--out", ".") == 2
    assert run(pkg, "plugin", "claude", "--out", "..") == 2
    assert (pkg / ".ai/config.toml").read_text() == PLUGIN_CFG


def test_plugin_requires_config(repo: Path) -> None:
    assert run(repo, "plugin", "claude") == 2  # no [plugin] table


@pytest.mark.parametrize(
    "cfg",
    [
        '[plugin]\nname = "Bad Name"\n',
        '[plugin]\nname = "claude-tools"\n',
        '[plugin]\nname = "anthropic"\n',
        '[plugin]\nname = "x"\nsurprise = "y"\n',
        '[plugin]\nname = "x"\nversion = 2\n',
    ],
)
def test_invalid_plugin_config(repo: Path, cfg: str) -> None:
    write(repo, ".ai/config.toml", 'version = 1\ntargets = ["claude"]\n' + cfg)
    with pytest.raises(PyAisyncError):
        load_model(repo)


def test_skills_for_other_targets_are_not_packaged(pkg: Path) -> None:
    write(
        pkg,
        ".ai/skills/cursor-only/SKILL.md",
        "---\nname: c\ndescription: d\ntargets: [cursor]\n---\nx\n",
    )
    run(pkg, "plugin", "claude")
    assert not (pkg / "dist/my-tools/skills/cursor-only").exists()
