from __future__ import annotations

from pathlib import Path

import pytest

CONFIG = 'version = 1\ntargets = ["claude", "cursor", "opencode"]\n'


def write(root: Path, rel: str, content: str | bytes) -> Path:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(content, str):
        p.write_text(content, encoding="utf-8")
    else:
        p.write_bytes(content)
    return p


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    write(tmp_path, ".ai/config.toml", CONFIG)
    write(tmp_path, ".ai/rules/project.md", "---\nalways: true\n---\nUse uv.\n")
    write(
        tmp_path,
        ".ai/rules/python.md",
        '---\ndescription: Python style\nglobs: ["**/*.py"]\n---\nUse type hints.\n',
    )
    write(tmp_path, ".ai/rules/manual.md", "---\n---\nOnly when asked.\n")
    write(tmp_path, ".ai/commands/review.md", "---\ndescription: Review\n---\nReview $ARGUMENTS\n")
    write(
        tmp_path,
        ".ai/skills/deploy/SKILL.md",
        "---\nname: deploy\ndescription: Deploy\n---\nSteps.\n",
    )
    write(tmp_path, ".ai/skills/deploy/scripts/run.sh", "echo hi\n")
    return tmp_path
