# CLAUDE.md

Guidance for Claude Code (or any agent) working in this repository.

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

- `mcp/` — the MCP server surface: `server.py` (FastMCP entrypoint; registers the tools with `mcp_server_lifecycle.py` and runs the stdio loop — the `neo-localmcp-server` console script is `neo_localmcp.mcp.server:main`), `context_worker.py` (the isolated subprocess runner for `prepare_context`), and the tool bodies split by category:
  - `mcp/system.py` — status/lifecycle tools: `init`, `status`, `where`, `model_status`, `doctor`, `repo_index`/`repo_reindex`/`repo_refresh`, `reset_repo`/`reset_all`, `repo_lookup`.
  - `mcp/memory.py` — the context-retrieval pipeline: `prepare_context`/`context_prepare`, `file_context`/`file_excerpts`, `record_change`, `test_determinism`, plus the scoring/ranking/formatting internals that back them.
  - `mcp/ollama.py` — Ollama-facing tools: `set_ollama`, `ollama_status`, `ollama_ensure`, `ollama_control`.
  - `mcp/editing.py` — the two source-touching tools: `summarize_file` and `apply_unified_patch` (the only writer, and only via an exact developer-approved diff).
  - `mcp/_shared.py` — small helpers shared across the category modules (`json_out`, model-timing formatting, response-slimming); not a tool module itself, so no category imports another.
- `runtime_cli.py` — CLI subcommands (`index`, `context`, `doctor`, `servers`, `stop`, `setup`, ...); `neo-localmcp` console script. Administration is CLI-only, never exposed as an MCP tool; imports the same `mcp/` tool bodies. Named `runtime_cli.py` (not `cli.py`) to read unambiguously alongside the installer CLI at `installer/cli.py`.
- `retrieval/` — the deterministic retrieval engine: `repo_memory.py` (SQLite repo/file/symbol index, `repo_fts`, and the retrieval-boost implicit-feedback memory — `get_boost_map`/`record_task_query`/`record_retrieval_feedback`) and `query.py` (natural/hybrid task-string parsing into intent + strong/weak terms). Model-free; `mcp/memory.py` ranks against it.
- `ollama_client.py` — Ollama lifecycle (status/start/warm/ensure), bounded inference (`num_predict`), never auto-downloads models.
- `mcp_server_lifecycle.py` — MCP **server process** registry + graceful-stop (`neo-localmcp stop`), used by `setup.py` before touching runtime files. Named to avoid colliding with `installer/`'s own "lifecycle" framing below — this file only supervises the running server process (PID registration, stop-file watch, clean exit), it never touches AI requests or the repo filesystem itself.
- `ai_client_config.py` — registers and deregisters neo-localmcp for Claude Code / Claude Desktop / Codex (`setup_*`/`remove_*` per surface, plus `remove_client`/`remove_clients` dispatchers). Claude Desktop removal is detect-and-warn only — the extension itself is removed through Claude's own UI, not automated. Reads the slash-command templates from `templates/`.
- `config.py` — single source of truth for `APP_DIR` (`~/.neo-localmcp` by default) and `config.yaml` defaults. Despite the extension, the on-disk content is JSON (legacy naming, kept for backward compatibility — see the `CONFIG_PATH` comment).
- `repo_utils.py` — low-level cross-cutting helpers (path safety, subprocess wrappers, symbol extraction, git info) shared by everything above.
- `installer/` — the lifecycle package (path/process/state/verification machinery, `mcpb.py`'s bundle builder), now also home to both installer frontends: `cli.py` (the scriptable installer CLI, moved from the old top-level `setup_cli.py`) and `wizard/` (the guided terminal installer behind `setup_wizard.py` — plain, stdlib-only, full-screen *numbered* UI, no TUI toolkit; its `preview_backend.py`/`live_backend.py` are the two `WizardBackend` implementations). See `docs/` design specs for this package's internal submodule breakdown — that level of detail doesn't belong in this always-loaded file.
- `benchmarker/` — retrieval-quality benchmarking: package `__init__.py` plus `queries/` (the query fixtures, e.g. `default.jsonl`) it runs against.
- `templates/` — the `/neo-localmcp:*` slash-command markdown installed into Claude Code (package data read by `ai_client_config.py`).
- `branding.py` / `neo.toml` — product naming constants (only place that should ever need to change if the product is renamed).

## Repo-wide conventions

- **Version is defined once**, in `neo_localmcp/__init__.py`'s `__version__`. Every
  release bumps it in lockstep with `pyproject.toml`'s `version` and
  `packages/claude-desktop/mcpb/manifest.json` — the three must always match.
- **macOS and Windows are the supported 1.0.10 platforms.** `setup.py` is the sole
  lifecycle policy surface. Linux support is deferred.
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

## Code commenting standard

Use concise developer comments when editing or generating code. The goal is
better navigation for Neel and future maintainers, not more comments.

Functions may have up to three short comment lines that describe purpose,
responsibility, inputs, outputs, or important constraints. Prefer compact
pseudocode-like sentences over prose paragraphs.

Use breadcrumb comments (`input -> state -> UI`, `action -> mutation -> redraw`,
`parent -> child`, `state -> visual`, `old position -> new position`, `if X then Y`)
only when the code is genuinely following a clear multi-step process or branch —
a real A -> B -> C or conditional flow. Don't force arrow notation onto code that
isn't actually a process/state transition; default to a short pseudocode-like
sentence comment instead. Use `^` only when it clearly points to the concept
directly above.

Lightweight concept tags are welcome when they make a section easier to scan:
`#Binding`, `#StateFlow`, `#Animation`, `#Layout`, `#MatchedGeometry`,
`#Preview`, `#Action`, `#Persistence`, and `#Render`.

Sentence-style comments are fine when useful, but keep them short, direct, and
technical. Avoid comments that explain obvious syntax, repeat the code,
describe every modifier, over-document simple styling, or fragment the code with
noise. Use longer comments only for genuinely tricky concepts.

## GitHub workflow

- **`main` is merge-only, enforced by branch protection**, not just a stated
  rule — every change, including one-line doc edits, requires a branch + PR +
  green CI (`setup-v2.yml`, macOS + Windows). This applies even to the repo
  admin (`enforce_admins: true`); there is no direct-push escape hatch short
  of disabling the rule first. (Renamed from `master` on 2026-07-04; GitHub
  redirects the old name automatically, but any *new* local clone should
  track `main` from the start.)
- **Merge strategy is "Create a merge commit" only** — squash and rebase are
  disabled at the repo level. Chosen deliberately: it's the only strategy
  where a local branch's original commits remain true ancestors of `main`,
  so `git branch -d` (not `-D`) works normally after merging, and the full
  branch structure stays visible in `git log --graph`/GitHub's network view.
  Use `git log --first-parent main` for a flattened, one-line-per-PR view
  when the full branch graph is too noisy.
- **Issue/PR titles follow `type(area): description`**, and issues/PRs get
  matching `type:`/`area:` labels — see `.github/CONTRIBUTING.md` for the
  full taxonomy (types: `meta`, `docs`, `chore`, `refactor`, `feat`, `fix`,
  `test`, `perf`, `security`; areas map to the module map above). `meta`-typed
  items get no area label — they're about the project, not the codebase.
- **CI runs the fast suite in parallel** (`pytest-xdist`, `-n auto`) but the
  slow, real-lifecycle tests (`tests/installer/test_*_lifecycle.py`) stay
  serial deliberately — they build real venvs and manipulate real process
  trees, and parallelizing them risks cross-worker collisions. Don't add
  `-n auto` to that step without re-verifying isolation first.
- Before merging, confirm CI is actually green on the PR — don't merge on the
  assumption that it'll pass. A stale-bundle bug (`test_distribution.py`)
  broke the default branch's CI for two merges in a row earlier in this
  project's history specifically because that step was skipped.

## Known gaps (see `PROJECT_STATUS.md` for the current authoritative list)

- Linux setup lifecycle and CI evidence are deferred beyond 1.0.10.
- `ollama_client.py`'s `start_service()` doesn't reliably inherit a custom
  `OLLAMA_MODELS` env var under some process ancestries (e.g. spawned under Claude
  Desktop's extension host) — falls back to the default models path silently instead
  of erroring.
- Section-summary cache is keyed on source-file content hash only, not code version —
  a cache entry from a buggy older release isn't invalidated by fixing the bug, only
  by the source file changing or a manual cache clear.
- **Swapping `fast_model`/`summary_model` (via `set-ollama`/config) does not
  invalidate or regenerate existing summaries — this is intended, not a bug** (issue
  #8, decided 2026-07-04). Summaries carry the producing model as metadata
  (`files.summary_model`, `section_summaries.model`), but on a model swap the old
  summaries keep being served as-is, still tagged with the previous model, until the
  source file's content hash changes (which re-summarizes) or the cache is manually
  cleared. Rationale: summaries are advisory enrichment, never authoritative (see
  `docs/ARCHITECTURE.md` Safety model — the current source file/git diff are the truth),
  and a description of an *unchanged* file stays accurate regardless of which model
  wrote it. Ranking is never cached (always live) and `retrieval_boost` has no model
  column, so neither is affected by a swap at all. Do **not** add
  invalidate-on-model-swap machinery to `set-ollama` without a concrete correctness
  problem to point at — the deliberate choice is status quo (option 1 of #8), matching
  this repo's minimalism convention. If a swap's stale summaries ever need clearing, do
  it explicitly (re-index the file or clear the cache), not implicitly on config write.
- Retrieval-boost memory (`repo_memory.py`) is audited and upgrade-safe (1.0.9,
  phase 9g), but modest by design: it only nudges once the same task string
  recurs >= 3 times in one repo, so it helps repeated workflows, not first-time
  tasks (see `PROJECT_STATUS.md` for the full audit evidence).
