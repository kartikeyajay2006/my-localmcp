# AGENTS.md

Guidance for Codex (or any agent) working in this repository.

## What this is

`neo-localmcp` is a local MCP server that gives Claude/Codex deterministic,
hash-aware repository context (ranked source excerpts, symbols, tests) so the
primary model can skip repeated broad search/reads. Ollama is optional local
preprocessing (ranking/summarization) layered on top — never authoritative, never
required. **It does not generate or edit source code itself**, except applying an
exact developer-approved unified diff via `apply-patch` (`git apply --check` first).
See `docs/ARCHITECTURE.md` for the full context-flow/ranking-policy/safety model.

## Read first

Before non-trivial work, read:

```text
PROJECT_STATUS.md     live status: current phase, verified items, known limitations
PROJECT_NOTES.md      append-only decision/bug log
docs/ARCHITECTURE.md  roles, context flow, ranking policy, safety model
docs/<version>_PLAN.md   phase-by-phase plan for the release in progress, if one exists
```

Don't treat chat history as the source of truth for project state — these files are.

## Commands

```bash
# macOS / Windows (Python 3.12+)
python setup.py install
python setup.py reinstall
python setup.py uninstall

# Verification (run after any code change)
python -m pytest -q
python -m compileall -q neo_localmcp setup.py

# Manual smoke test
neo-localmcp doctor
neo-localmcp ollama status
neo-localmcp context "debug repository indexing: index_repo, refresh" --repo-root . --token-budget 1000
```

## Module map (`neo_localmcp/`)

- `server.py` — FastMCP entrypoint; registers with `lifecycle.py` and runs the stdio loop.
- `tools.py` — MCP tool implementations (`prepare_context`/`context_prepare`, `file_excerpts`, `summarize_file`, `apply_patch`, etc.).
- `cli.py` — CLI subcommands (`index`, `context`, `doctor`, `servers`, `stop`, `setup`, ...). Administration is CLI-only, never exposed as an MCP tool.
- `repo_memory.py` — SQLite persistence: repo/file/symbol index, `repo_fts`, and the retrieval-boost implicit-feedback memory (`get_boost_map`, `record_task_query`, `record_retrieval_feedback`).
- `ollama_client.py` — Ollama lifecycle (status/start/warm/ensure), bounded inference (`num_predict`), never auto-downloads models.
- `lifecycle.py` — server registry + graceful-stop (`neo-localmcp stop`), used by `setup.py` before touching runtime files.
- `client_setup.py` — registers and deregisters neo-localmcp for Claude Code / Claude Desktop / Codex (`setup_*`/`remove_*` per surface, plus `remove_client`/`remove_clients` dispatchers). Claude Desktop removal is detect-and-warn only — the extension itself is removed through Claude's own UI, not automated.
- `config.py` — single source of truth for `APP_DIR` (`~/.neo-localmcp` by default) and `config.yaml` defaults.
- `query.py` — natural/hybrid task-string parsing into intent + strong/weak terms.
- `identity.py` / `neo.toml` — product naming constants (only place that should ever need to change if the product is renamed).

## Repo-wide conventions

- **Version is defined once**, in `neo_localmcp/__init__.py`'s `__version__`. Every
  release bumps it in lockstep with `pyproject.toml`'s `version` and
  `packages/claude-desktop/mcpb/manifest.json` — the three must always match.
- **macOS and Windows are the supported 1.0.10 platforms.** `setup.py` is the sole
  lifecycle policy surface. Linux support is deferred. Legacy platform installers
  are retained under `_LegacyInstallers/` for historical reference only.
- **Repository memory is centralized, not per-repo.** All indexed repos share one
  `~/.neo-localmcp/repo-context.sqlite`, distinguished internally by `repo_id`
  (canonical root + git remote). A "wipe memory" action affects every indexed repo,
  not just the one you're standing in.
- **Deterministic retrieval must never depend on Ollama.** Every Ollama-touching code
  path (ranking, summarization) has a deterministic fallback; a failed/busy/cold
  Ollama call degrades gracefully, it never blocks or empties a context response.
- **New features get a written phase plan first** for anything non-trivial or
  multi-step, saved to `docs/<version>_PLAN.md` (see `docs/1.0.7_PLAN.md` and
  `docs/1.0.9_PLAN.md` for the expected shape: origin, phases with explicit scope,
  a deferred section, and — when phases are large enough to warrant a model switch —
  a model/effort table).
- `PROJECT_STATUS.md` and `PROJECT_NOTES.md` should be updated at the end of any
  session that changes verified behavior — status/limitations in the former, a
  one-or-two-line dated entry in the latter.

## Known gaps (see `PROJECT_STATUS.md` for the current authoritative list)

- Linux setup lifecycle and CI evidence are deferred beyond 1.0.10.
- `ollama_client.py`'s `start_service()` doesn't reliably inherit a custom
  `OLLAMA_MODELS` env var under some process ancestries (e.g. spawned under Claude
  Desktop's extension host) — falls back to the default models path silently instead
  of erroring.
- Section-summary cache is keyed on source-file content hash only, not code version —
  a cache entry from a buggy older release isn't invalidated by fixing the bug, only
  by the source file changing or a manual cache clear.
- Retrieval-boost memory (`repo_memory.py`) is audited and upgrade-safe (1.0.9,
  phase 9g), but modest by design: it only nudges once the same task string
  recurs >= 3 times in one repo, so it helps repeated workflows, not first-time
  tasks (see `PROJECT_STATUS.md` for the full audit evidence).
