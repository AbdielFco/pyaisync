from __future__ import annotations

from pathlib import Path

from conftest import write

from pyaisync.cli import main


def run(root: Path, *args: str) -> int:
    return main(["--root", str(root), *args])


def test_generate_creates_native_files(repo: Path) -> None:
    assert run(repo, "generate") == 0
    claude = (repo / "CLAUDE.md").read_text()
    assert "## project" in claude and "Use uv." in claude
    assert "Applies to: `**/*.py`" in claude
    assert (repo / "AGENTS.md").read_text() == claude
    assert (
        (repo / ".cursor/rules/project.mdc").read_text().startswith("---\nalwaysApply: true\n---")
    )
    py = (repo / ".cursor/rules/python.mdc").read_text()
    assert (
        "description: Python style" in py
        and "globs: '**/*.py'" in py
        and "alwaysApply: false" in py
    )
    manual = (repo / ".cursor/rules/manual.mdc").read_text()
    assert "alwaysApply: false" in manual and "description" not in manual
    assert "description: Review" in (repo / ".claude/commands/review.md").read_text()
    assert "description: Review" in (repo / ".opencode/commands/review.md").read_text()
    assert "Review" in (repo / ".cursor/commands/review.md").read_text()
    # skills: a single copy in .claude/skills (all 3 read it)
    assert (repo / ".claude/skills/deploy/SKILL.md").is_file()
    assert (repo / ".claude/skills/deploy/scripts/run.sh").read_text() == "echo hi\n"
    assert not (repo / ".agents").exists()
    assert (repo / ".ai/.manifest.json").is_file()


def test_skills_go_to_agents_dir_without_claude(repo: Path) -> None:
    write(repo, ".ai/config.toml", 'version = 1\ntargets = ["cursor", "opencode"]\n')
    assert run(repo, "generate") == 0
    assert (repo / ".agents/skills/deploy/SKILL.md").is_file()
    assert not (repo / ".claude").exists()
    assert not (repo / "CLAUDE.md").exists()


def test_idempotent_and_check(repo: Path, capsys) -> None:  # type: ignore[no-untyped-def]
    assert run(repo, "generate") == 0
    before = {p: p.read_bytes() for p in repo.rglob("*") if p.is_file()}
    assert run(repo, "generate") == 0
    assert before == {p: p.read_bytes() for p in repo.rglob("*") if p.is_file()}
    assert run(repo, "check") == 0


def test_check_detects_drift_and_does_not_write(repo: Path) -> None:
    assert run(repo, "check") == 1
    assert not (repo / "CLAUDE.md").exists()
    run(repo, "generate")
    write(repo, ".ai/rules/project.md", "---\nalways: true\n---\nChange.\n")
    assert run(repo, "check") == 1


def test_dry_run_writes_nothing(repo: Path) -> None:
    assert run(repo, "generate", "--dry-run") == 0
    assert not (repo / "CLAUDE.md").exists() and not (repo / ".ai/.manifest.json").exists()


def test_does_not_overwrite_unmanaged_file(repo: Path) -> None:
    write(repo, "CLAUDE.md", "mine\n")
    assert run(repo, "generate") == 2
    assert (repo / "CLAUDE.md").read_text() == "mine\n"
    assert not (repo / "AGENTS.md").exists()  # nothing is written when there is a conflict
    assert run(repo, "generate", "--adopt") == 0
    assert "Use uv." in (repo / "CLAUDE.md").read_text()


def test_hand_edited_managed_file_conflicts(repo: Path) -> None:
    run(repo, "generate")
    write(repo, "AGENTS.md", "edited\n")
    write(repo, ".ai/rules/project.md", "---\nalways: true\n---\nNew.\n")
    assert run(repo, "generate") == 2
    assert (repo / "AGENTS.md").read_text() == "edited\n"
    assert run(repo, "generate", "--force") == 0
    assert "New." in (repo / "AGENTS.md").read_text()


def test_removed_source_deletes_generated_and_prunes_dirs(repo: Path) -> None:
    run(repo, "generate")
    (repo / ".ai/commands/review.md").unlink()
    run(repo, "generate")
    assert not (repo / ".claude/commands").exists()
    assert not (repo / ".opencode").exists()


def test_stale_edited_file_is_kept_with_warning(repo: Path) -> None:
    run(repo, "generate")
    (repo / ".ai/commands/review.md").unlink()
    write(repo, ".claude/commands/review.md", "manual edit\n")
    assert run(repo, "generate") == 0
    assert (repo / ".claude/commands/review.md").read_text() == "manual edit\n"


def test_targets_filter_in_frontmatter(repo: Path) -> None:
    write(repo, ".ai/rules/only-cursor.md", "---\ntargets: [cursor]\n---\nCursor only.\n")
    run(repo, "generate")
    assert "Cursor only." not in (repo / "CLAUDE.md").read_text()
    assert (repo / ".cursor/rules/only-cursor.mdc").is_file()


def test_features_flag(repo: Path) -> None:
    assert run(repo, "generate", "--features", "rules") == 0
    assert (repo / "CLAUDE.md").is_file()
    assert not (repo / ".claude").exists()


def test_user_content_in_unmanaged_dirs_untouched(repo: Path) -> None:
    write(repo, ".claude/settings.json", '{"x": 1}\n')
    write(repo, ".claude/commands/mio.md", "mine\n")
    run(repo, "generate")
    assert (repo / ".claude/settings.json").read_text() == '{"x": 1}\n'
    assert (repo / ".claude/commands/mio.md").read_text() == "mine\n"
