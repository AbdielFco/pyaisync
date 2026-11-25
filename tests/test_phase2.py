from __future__ import annotations

import json
from pathlib import Path

import pytest
from conftest import write

from pyaisync.cli import main
from pyaisync.errors import PyAisyncError
from pyaisync.loader import load_model

MCP = """
[servers.docs]
type = "http"
url = "https://example.com/mcp"
headers = { Authorization = "Bearer ${DOCS_TOKEN}" }

[servers.local-tool]
type = "stdio"
command = "uvx"
args = ["my-mcp-server", "--key=${MY_KEY}"]
env = { API_KEY = "${MY_API_KEY}", LOG_LEVEL = "info" }
"""

AGENT_RO = "---\ndescription: Read-only reviewer\ntools: [read, grep, glob]\n---\nReview only.\n"
AGENT_RW = "---\ndescription: Fixer\ntools: [read, edit, shell]\n---\nFix things.\n"


def run(root: Path, *args: str) -> int:
    return main(["--root", str(root), *args])


def jload(root: Path, rel: str) -> dict:  # type: ignore[type-arg]
    return json.loads((root / rel).read_text())


@pytest.fixture
def full(repo: Path) -> Path:
    write(repo, ".ai/mcp.toml", MCP)
    write(repo, ".ai/agents/reviewer.md", AGENT_RO)
    write(repo, ".ai/agents/fixer.md", AGENT_RW)
    return repo


def test_agents_per_target(full: Path) -> None:
    assert run(full, "generate") == 0
    claude = (full / ".claude/agents/reviewer.md").read_text()
    assert "name: reviewer" in claude and "tools: Read, Grep, Glob" in claude
    assert "tools: Read, Edit, Write, Bash" in (full / ".claude/agents/fixer.md").read_text()
    assert "model:" not in claude  # inherit is omitted
    cur_ro = (full / ".cursor/agents/reviewer.md").read_text()
    assert "readonly: true" in cur_ro
    assert "readonly" not in (full / ".cursor/agents/fixer.md").read_text()
    oc = (full / ".opencode/agents/reviewer.md").read_text()
    assert "mode: subagent" in oc
    # deny everything first, then allow the listed tools (last match wins)
    assert oc.index("effect: deny") < oc.index("action: read")
    assert "action: edit" not in oc and "action: shell" not in oc


def test_mcp_per_target(full: Path) -> None:
    assert run(full, "generate") == 0
    c = jload(full, ".mcp.json")["mcpServers"]
    assert c["docs"] == {
        "type": "http",
        "url": "https://example.com/mcp",
        "headers": {"Authorization": "Bearer ${DOCS_TOKEN}"},
    }
    assert c["local-tool"]["type"] == "stdio" and c["local-tool"]["args"][1] == "--key=${MY_KEY}"
    cur = jload(full, ".cursor/mcp.json")["mcpServers"]
    assert cur["docs"] == {
        "url": "https://example.com/mcp",
        "headers": {"Authorization": "Bearer ${env:DOCS_TOKEN}"},
    }
    assert cur["local-tool"]["env"]["API_KEY"] == "${env:MY_API_KEY}"
    oc = jload(full, "opencode.json")["mcp"]["servers"]
    assert oc["docs"]["type"] == "remote"
    assert oc["docs"]["headers"]["Authorization"] == "Bearer {env:DOCS_TOKEN}"
    assert oc["local-tool"] == {
        "type": "local",
        "command": ["uvx", "my-mcp-server", "--key={env:MY_KEY}"],
        "environment": {"API_KEY": "{env:MY_API_KEY}", "LOG_LEVEL": "info"},
    }


def test_idempotent_and_check_with_json(full: Path) -> None:
    assert run(full, "generate") == 0
    snap = {p: p.read_bytes() for p in full.rglob("*") if p.is_file() and ".git" not in p.parts}
    assert run(full, "generate") == 0
    assert snap == {
        p: p.read_bytes() for p in full.rglob("*") if p.is_file() and ".git" not in p.parts
    }
    assert run(full, "check") == 0


def test_json_merge_preserves_user_keys(full: Path) -> None:
    write(full, ".mcp.json", json.dumps({"other": 1, "mcpServers": {"mine": {"command": "x"}}}))
    write(
        full,
        "opencode.json",
        json.dumps(
            {"theme": "dark", "mcp": {"servers": {"mine": {"type": "local", "command": ["y"]}}}}
        ),
    )
    assert run(full, "generate") == 0
    c = jload(full, ".mcp.json")
    assert (
        c["other"] == 1
        and c["mcpServers"]["mine"] == {"command": "x"}
        and "docs" in c["mcpServers"]
    )
    o = jload(full, "opencode.json")
    assert o["theme"] == "dark" and "mine" in o["mcp"]["servers"] and "docs" in o["mcp"]["servers"]


def test_json_name_collision_requires_adopt(full: Path) -> None:
    write(full, ".mcp.json", json.dumps({"mcpServers": {"docs": {"command": "mine"}}}))
    assert run(full, "generate") == 2
    assert jload(full, ".mcp.json")["mcpServers"]["docs"] == {"command": "mine"}
    assert not (full / "CLAUDE.md").exists()  # nothing written on conflict
    assert run(full, "generate", "--adopt") == 0
    assert jload(full, ".mcp.json")["mcpServers"]["docs"]["url"] == "https://example.com/mcp"


def test_hand_edited_json_entry_conflicts_then_force(full: Path) -> None:
    run(full, "generate")
    data = jload(full, ".mcp.json")
    data["mcpServers"]["docs"]["url"] = "https://evil.example/mcp"
    (full / ".mcp.json").write_text(json.dumps(data))
    write(full, ".ai/mcp.toml", MCP.replace("example.com", "example.org"))
    assert run(full, "generate") == 2
    assert jload(full, ".mcp.json")["mcpServers"]["docs"]["url"] == "https://evil.example/mcp"
    assert run(full, "generate", "--force") == 0
    assert jload(full, ".mcp.json")["mcpServers"]["docs"]["url"] == "https://example.org/mcp"


def test_removing_server_removes_only_ours(full: Path) -> None:
    write(full, ".mcp.json", json.dumps({"mcpServers": {"mine": {"command": "x"}}}))
    run(full, "generate")
    write(full, ".ai/mcp.toml", MCP.split("[servers.local-tool]")[0])
    assert run(full, "generate") == 0
    servers = jload(full, ".mcp.json")["mcpServers"]
    assert set(servers) == {"mine", "docs"}
    (full / ".ai/mcp.toml").unlink()
    assert run(full, "generate") == 0
    assert jload(full, ".mcp.json")["mcpServers"] == {"mine": {"command": "x"}}


def test_json_file_created_by_us_is_deleted_when_empty(full: Path) -> None:
    run(full, "generate")
    assert (full / ".cursor/mcp.json").is_file()
    (full / ".ai/mcp.toml").unlink()
    assert run(full, "generate") == 0
    assert not (full / ".cursor/mcp.json").exists()
    assert not (full / ".mcp.json").exists() and not (full / "opencode.json").exists()


def test_jsonc_file_is_refused_not_clobbered(full: Path) -> None:
    write(full, "opencode.json", '{\n  // comment\n  "theme": "dark"\n}\n')
    assert run(full, "generate") == 2
    assert "// comment" in (full / "opencode.json").read_text()


def test_check_detects_json_drift(full: Path) -> None:
    run(full, "generate")
    data = jload(full, ".cursor/mcp.json")
    data["mcpServers"]["docs"]["url"] = "https://changed"
    (full / ".cursor/mcp.json").write_text(json.dumps(data))
    assert run(full, "check") == 2  # hand-edited managed entry


def test_sse_skipped_for_opencode_with_warning(full: Path, capsys) -> None:  # type: ignore[no-untyped-def]
    write(full, ".ai/mcp.toml", '[servers.old]\ntype = "sse"\nurl = "https://x.example/sse"\n')
    assert run(full, "generate") == 0
    assert "SSE is not supported by OpenCode" in capsys.readouterr().err
    assert not (full / "opencode.json").exists()
    assert jload(full, ".mcp.json")["mcpServers"]["old"]["type"] == "sse"


@pytest.mark.parametrize(
    "toml",
    [
        '[servers.a]\ntype = "stdio"\ncommand = "x"\nenv = { API_KEY = "abc123" }\n',
        '[servers.a]\ntype = "http"\nurl = "https://x.example"\nheaders = { Authorization = "Bearer abc" }\n',
        '[servers.a]\ntype = "stdio"\ncommand = "x"\nenv = { NOTE = "ghp_abcdefghijklmnopqrstuvwxyz" }\n',
        '[servers.a]\ntype = "stdio"\n',
        '[servers.a]\ntype = "http"\nurl = "ftp://x"\n',
        '[servers.a]\ntype = "http"\nurl = "https://x"\ncommand = "y"\n',
        '[servers.a]\ntype = "weird"\n',
        '[servers."../x"]\ntype = "stdio"\ncommand = "x"\n',
    ],
)
def test_invalid_or_secret_mcp_rejected(repo: Path, toml: str) -> None:
    write(repo, ".ai/mcp.toml", toml)
    with pytest.raises(PyAisyncError):
        load_model(repo)


def test_invalid_agent_tools_rejected(repo: Path) -> None:
    write(repo, ".ai/agents/a.md", "---\ndescription: x\ntools: [read, teleport]\n---\nb\n")
    with pytest.raises(PyAisyncError, match="unknown tools"):
        load_model(repo)


def test_agent_model_passthrough_warns(repo: Path, capsys) -> None:  # type: ignore[no-untyped-def]
    write(repo, ".ai/agents/a.md", "---\ndescription: x\nmodel: sonnet\n---\nbody\n")
    run(repo, "generate")
    err = capsys.readouterr().err
    assert "passed verbatim" in err
    assert "model: sonnet" in (repo / ".claude/agents/a.md").read_text()


def test_features_filter_excludes_mcp(full: Path) -> None:
    assert run(full, "generate", "--features", "rules,subagents") == 0
    assert (full / ".claude/agents/reviewer.md").is_file()
    assert not (full / ".mcp.json").exists()


def test_import_warns_about_unimported_mcp_and_agents(tmp_path: Path, capsys) -> None:  # type: ignore[no-untyped-def]
    write(tmp_path, ".ai/config.toml", 'version = 1\ntargets = ["claude"]\n')
    write(tmp_path, ".mcp.json", "{}")
    write(tmp_path, ".claude/agents/a.md", "---\nname: a\n---\nx\n")
    assert run(tmp_path, "import", "claude") == 0
    err = capsys.readouterr().err
    assert ".mcp.json" in err and ".claude/agents" in err and "not implemented yet" in err
