# AGENTS.md — pyaisync

Instructions for AI coding agents working **on this repository**. (For using the tool, see `README.md`.)

## What this is
`pyaisync` keeps AI coding agent configuration in one source (`.ai/`) and transpiles it into the native files of Claude Code,
Cursor and OpenCode; it also imports native config and packages a Claude Code plugin. Python 3.11+, one runtime dependency
(`pyyaml`), managed with `uv`.

**Read first:** [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) (modules, data flow, writer rules, how to extend),
[`docs/formats/README.md`](docs/formats/README.md) (verified native formats), [`PLAN.md`](PLAN.md) (design rationale).

## Commands
```bash
uv sync                                  # install (includes dev tools)
uv run pytest -q                         # tests (must pass)
uv run ruff check . && uv run ruff format --check .
uv run mypy                              # strict (must pass)
uv run pyaisync --help
```
All three checks must be green before you finish a change.

## Rules you must not break
1. **Non-destructive.** Never overwrite or delete a file pyaisync did not generate. All writes go through `writer.apply`
   (manifest + hashes) and `safefs`. If any conflict exists, nothing is written.
2. **No network, no execution.** The package never makes network calls and never runs hooks, MCP servers or user content.
3. **Deterministic output.** Sorted iteration, fixed formatting; `generate` twice must change nothing (`check` relies on it).
4. **Never widen access** when importing or translating (an allow rule or tool allowlist that cannot be mapped exactly is
   skipped, not approximated) and **never write literal secrets** into `.ai/` or generated files (`${VAR}` only).
5. **Unsupported means a warning**, not a silent approximation.
6. **Verify native formats against the official docs** before changing an adapter or importer, and record the source URL and
   date in `docs/formats/README.md`. Do not rely on memory.
7. **Everything is in English** (code, comments, docs, messages, commits).
8. No new runtime dependencies without a strong reason.

## Working here
- Adapters in `targets/` are pure (`Model -> Plan`); keep disk access in `writer.py`/`safefs.py`/`importers/`.
- Tests must never write outside `tmp_path`; run the CLI with `main(["--root", str(root), ...])`. Relative path options are
  resolved against `--root`.
- Add tests with every behaviour change: generate output, idempotence, import round trip, and the security cases.
- Update `README.md` (scope/limitations), `docs/formats/README.md` and `docs/ARCHITECTURE.md` when behaviour or formats change.
- Do not commit unless asked. Do not push or publish anything.
