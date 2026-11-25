from __future__ import annotations

from pathlib import Path

from conftest import write

from pyaisync.cli import main


def run(root: Path, *args: str) -> int:
    return main(["--root", str(root), *args])


def test_init_creates_structure_and_refuses_rerun(tmp_path: Path) -> None:
    assert run(tmp_path, "init") == 0
    assert (tmp_path / ".ai/config.toml").is_file()
    assert (tmp_path / ".ai/rules/project.md").is_file()
    assert run(tmp_path, "init") == 2
    assert run(tmp_path, "generate") == 0  # the examples generate without errors
    assert run(tmp_path, "check") == 0


def test_init_detects_targets(tmp_path: Path) -> None:
    (tmp_path / ".cursor").mkdir()
    run(tmp_path, "init")
    assert 'targets = ["cursor"]' in (tmp_path / ".ai/config.toml").read_text()


def test_import_claude_then_generate_roundtrip(tmp_path: Path) -> None:
    write(tmp_path, "CLAUDE.md", "# My project\nUse uv.\n")
    write(tmp_path, ".claude/commands/fix.md", "---\ndescription: Fix\n---\nFix $ARGUMENTS\n")
    write(tmp_path, ".claude/skills/s1/SKILL.md", "---\nname: s1\ndescription: d\n---\nbody\n")
    assert run(tmp_path, "init", "--from", "claude") == 0
    assert "Use uv." in (tmp_path / ".ai/rules/project.md").read_text()
    assert "always: true" in (tmp_path / ".ai/rules/project.md").read_text()
    assert (tmp_path / ".ai/commands/fix.md").is_file()
    assert (tmp_path / ".ai/skills/s1/SKILL.md").is_file()
    # native file untouched (read-only)
    assert (tmp_path / "CLAUDE.md").read_text() == "# My project\nUse uv.\n"
    # generate would overwrite unmanaged native files -> requires --adopt (safe by default)
    assert run(tmp_path, "generate") == 2
    assert run(tmp_path, "generate", "--adopt") == 0
    assert "Use uv." in (tmp_path / "CLAUDE.md").read_text()
    assert "Fix $ARGUMENTS" in (tmp_path / ".claude/commands/fix.md").read_text()
    assert run(tmp_path, "check") == 0
    # re-importing generated output is a no-op (the marker is detected)
    assert run(tmp_path, "import", "claude") == 0
    assert (tmp_path / ".ai/rules/project.md").read_text().count("Use uv.") == 1


def test_import_cursor_rules(tmp_path: Path) -> None:
    write(tmp_path, ".ai/config.toml", 'version = 1\ntargets = ["cursor"]\n')
    write(
        tmp_path,
        ".cursor/rules/Py Style.mdc",
        "---\ndescription: Style\nglobs: src/**/*.py, tests/**\nalwaysApply: false\n---\nTypes.\n",
    )
    write(tmp_path, ".cursor/rules/siempre.mdc", "---\nalwaysApply: true\n---\nAlways.\n")
    write(tmp_path, ".cursor/rules/ignorada.md", "not mdc\n")
    assert run(tmp_path, "import", "cursor") == 0
    rule = (tmp_path / ".ai/rules/py-style.md").read_text()
    assert "globs:\n- src/**/*.py\n- tests/**" in rule and "Types." in rule
    assert "always: true" in (tmp_path / ".ai/rules/siempre.md").read_text()
    assert not (tmp_path / ".ai/rules/ignorada.md").exists()


def test_import_conflicts(tmp_path: Path) -> None:
    write(tmp_path, ".ai/config.toml", 'version = 1\ntargets = ["claude","opencode"]\n')
    write(tmp_path, "CLAUDE.md", "claude version\n")
    write(tmp_path, "AGENTS.md", "opencode version\n")
    run(tmp_path, "import", "claude")
    run(tmp_path, "import", "opencode")  # skip by default
    assert "claude" in (tmp_path / ".ai/rules/project.md").read_text()
    run(tmp_path, "import", "opencode", "--on-conflict", "rename")
    assert "opencode" in (tmp_path / ".ai/rules/project-opencode.md").read_text()
    run(tmp_path, "import", "opencode", "--on-conflict", "overwrite")
    assert "opencode" in (tmp_path / ".ai/rules/project.md").read_text()


def test_import_dry_run_and_requires_init(tmp_path: Path) -> None:
    write(tmp_path, "CLAUDE.md", "x\n")
    assert run(tmp_path, "import", "claude") == 2
    write(tmp_path, ".ai/config.toml", 'version = 1\ntargets = ["claude"]\n')
    assert run(tmp_path, "import", "claude", "--dry-run") == 0
    assert not (tmp_path / ".ai/rules").exists()


def test_import_skips_symlinked_skill_files(tmp_path: Path) -> None:
    import os

    write(tmp_path, ".ai/config.toml", 'version = 1\ntargets = ["claude"]\n')
    write(tmp_path, ".claude/skills/s/SKILL.md", "---\nname: s\n---\nx\n")
    os.symlink("/etc/hostname", tmp_path / ".claude/skills/s/leak")
    assert run(tmp_path, "import", "claude") == 0
    assert not (tmp_path / ".ai/skills/s").exists()
