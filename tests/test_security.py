from __future__ import annotations

import os
from pathlib import Path

import pytest
from conftest import write

from pyaisync.cli import main
from pyaisync.errors import PyAisyncError
from pyaisync.loader import load_model
from pyaisync.safefs import safe_join


@pytest.mark.parametrize("bad", ["Upper", "a b", "a..b", "x" * 80])
def test_invalid_names_rejected(repo: Path, bad: str) -> None:
    write(repo, f".ai/commands/{bad}.md", "x\n")
    with pytest.raises(PyAisyncError):
        load_model(repo)


def test_symlink_in_skill_rejected(repo: Path) -> None:
    os.symlink("/etc/passwd", repo / ".ai/skills/deploy/leak")
    with pytest.raises(PyAisyncError, match="symlink"):
        load_model(repo)


def test_symlinked_source_rule_rejected(repo: Path) -> None:
    secret = repo / "secret.txt"
    secret.write_text("x")
    os.symlink(secret, repo / ".ai/rules/evil.md")
    with pytest.raises(PyAisyncError, match="symlink"):
        load_model(repo)


def test_safe_join(tmp_path: Path) -> None:
    for bad in ("../x", "/etc/passwd", "a/../../x", "a\\b", ""):
        with pytest.raises(PyAisyncError):
            safe_join(tmp_path, bad)
    (tmp_path / "real").mkdir()
    os.symlink(tmp_path / "real", tmp_path / "link")
    with pytest.raises(PyAisyncError):
        safe_join(tmp_path, "link/file")


def test_generate_refuses_symlinked_output_dir(repo: Path, tmp_path_factory) -> None:  # type: ignore[no-untyped-def]
    outside = tmp_path_factory.mktemp("outside")
    os.symlink(outside, repo / ".claude")
    assert main(["--root", str(repo), "generate"]) == 2
    assert not any(outside.iterdir())


def test_bad_config(repo: Path) -> None:
    write(repo, ".ai/config.toml", 'version = 1\ntargets = ["vim"]\n')
    assert main(["--root", str(repo), "generate"]) == 2


def test_bad_frontmatter_has_filename(repo: Path, capsys) -> None:  # type: ignore[no-untyped-def]
    write(repo, ".ai/rules/bad.md", "---\ndescription: [oops\n---\nx\n")
    assert main(["--root", str(repo), "generate"]) == 2
    assert "bad.md" in capsys.readouterr().err


def test_hidden_files_in_source_dirs_are_ignored(repo: Path) -> None:
    write(repo, ".ai/commands/.oculto.md", "x\n")
    assert load_model(repo).commands[0].name == "review"
