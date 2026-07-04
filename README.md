# neo-localmcp

## What it is

`neo-localmcp` is a local MCP server that gives Claude/Codex deterministic,
hash-aware repository context — ranked source excerpts, symbols, and tests — so
the primary model can skip repeated broad search and full-file reads. It
indexes a repository once, incrementally refreshes changed files, and returns
a bounded bundle of *current* source excerpts on request.

V1 targets at least a 50% reduction in discovery/read tokens and a 30%
reduction in total task tokens without reducing edit accuracy.

**It does not generate or edit source code.** The one exception is applying an
exact, developer-approved unified diff via `apply-patch`/`apply_patch`, which
always validates with `git apply --check` first and defaults to check-only.
Everything else is retrieval, indexing, ranking, and summarization.

## What this actually is (north star)

The one-line pitch — "an MCP that saves tokens" — undersells it and invites
the wrong comparison (against other caching tricks). The more accurate frame:

**A deterministic repository-context layer for AI coding agents**, with:

- **Selection, ranking, and verification as first-class steps**, not implicit
  side effects of a search call — every returned excerpt carries a hash and
  line range, so a caller can prove it's reading current source, not stale
  cache.
- **Bounded, optional local-model use.** Ollama re-ranks or summarizes on top
  of the deterministic result; every Ollama-touching path is time-bounded and
  has a deterministic fallback, so a slow/cold/missing model degrades
  gracefully instead of blocking or emptying a response.
- **Auditable retrieval behavior.** `test-determinism` re-runs the same query
  N times and checks output hashes match; `record-change` closes the loop
  between "shown" and "actually edited."
- **Built-in model upgradability.** Ranking is never cached, so swapping
  `fast_model`/`summary_model` has zero memory-correctness implications by
  construction — see `PROJECT_STATUS.md` for the verification.
- **Not a frontier-model replacement.** The goal is context routing that lets
  a frontier model (and your local hardware, if capable) spend its budget on
  reasoning instead of rediscovering the same files every session.

Two things this is **not**, yet, on purpose:
- **Automated token-reduction measurement.** Today a response reports its own
  cost (`estimated_tokens_returned`); the 8-10x reduction figures in
  `PROJECT_STATUS.md` came from one-off manual comparisons during real
  sessions, not a repeatable benchmark tool. Optional built-in benchmarking is
  a planned, not-yet-built capability.
- **A schema-migration guarantee across breaking version changes.** Every
  schema change so far has been additive (new nullable columns) and has
  preserved memory in practice; there is no automated regression test yet
  proving a *structurally* breaking future change would too. Treat "memory
  loss is unacceptable" as an actively-held invariant, not a passively-proven
  one, until that test exists.

## How it works

1. **Index once, refresh incrementally.** The first request against a repo
   builds a complete hash-aware index (files, symbols, tests) into one shared
   SQLite database (`~/.neo-localmcp/repo-context.sqlite`). Later requests
   compare path, size, and modification time before re-hashing, so refreshes
   are cheap.
2. **Deterministic retrieval is always authoritative.** `prepare_context`
   parses a task string into intent + strong/weak terms, ranks candidate
   files/symbols against the index, and returns bounded current-source
   excerpts with hashes and line ranges — no model call required.
3. **Ollama is optional preprocessing layered on top.** It can re-rank
   candidates or summarize a file, but every Ollama-touching path has a
   deterministic fallback. A missing, cold, busy, or failed Ollama call
   degrades gracefully; it never blocks or empties a response.
4. **Repository identity is centralized, not per-repo.** All indexed repos
   share one database, distinguished internally by canonical root + Git
   remote, so clones and worktrees stay separate but memory persists across
   sessions.
5. **The only mutation path is an approved patch.** `apply-patch`/`apply_patch`
   applies an exact unified diff via `git apply`; it never invents one.
   `record-change`/`record_change` logs a completed edit and re-indexes the
   touched paths.

## Does it use Ollama?

**No, not by default, and never for anything you can't get without it.**
Deterministic context retrieval (`context`/`prepare_context`) works fully
without Ollama and stays the default (`use_ollama=false` / no `--ollama-rank`
flag). The one command that *is* inherently an Ollama call is
`summarize`/`summarize_file` — its entire purpose is a model-written summary,
cached by source hash so it's only regenerated when the file changes.

## Command reference

### CLI — administration (CLI-only, never exposed as an MCP tool)

| Command | Purpose |
|---|---|
| `init` | Create a fresh config at `~/.neo-localmcp/config.yaml`. |
| `status` | Fast status: config, repo index counts, Git, Ollama reachability. |
| `doctor` | Full health check: config, DB, Ollama, repo index, running servers. |
| `where` | Show install/config paths and the repo currently being analyzed. |
| `config` | Print the config file path. |
| `clients` | Show detected Claude/Codex config paths and the MCP block that would be written. |
| `setup [--client ...] [--dry-run]` | Install MCP config + slash commands for Claude Code, Claude Desktop, Codex CLI/Desktop. |
| `remove-client [--client ...] [--dry-run]` | Deregister neo-localmcp from supported clients (inverse of `setup`). |
| `serve` | Run the MCP server over stdio (what clients actually launch). |
| `servers` | List running neo-localmcp servers registered under this app home. |
| `stop [--pid \| --all] [--match-executable] [--timeout] [--no-force]` | Ask running server(s) to exit gracefully; force only as a last resort. |
| `set-ollama --base-url --fast-model --summary-model --num-ctx` | Set Ollama endpoint/model defaults. |
| `model status` | Show configured Ollama models and which are actually reachable. |

### CLI — repository indexing and queries (`--repo-root` on all of these)

| Command | Purpose |
|---|---|
| `index [--max-files] [--force]` | Hash-aware full index of files and symbols. |
| `refresh [--max-files] [--force]` | Update only stale/missing/changed files. |
| `reindex [--max-files]` | Force a full rebuild with the current indexer version. |
| `reset-repo --yes` | Delete only *this* repo's indexed context. Keeps config and every other repo. |
| `reset-all --yes` | Delete the entire shared context DB (every indexed repo). Keeps config and client setup. |
| `test-determinism task [--runs] [--reset-repo] [--reindex-first]` | Run the same deterministic query N times and verify identical output hashes. |
| `lookup query [--limit]` | Search indexed files/symbols by name or path. |
| `file path [--around-line] [--context-lines]` | One file's cached context, symbols, freshness, and an optional excerpt. |
| `context task [--max-files] [--token-budget] [--ollama-rank] [--model] [--format]` | Bounded source-first context bundle for a task — the main retrieval command. |
| `summarize path [--heading] [--model]` | Summarize a file (or one Markdown heading) with Ollama; cached by source hash. |
| `apply-patch patch_file [--check-only]` | Validate (default) or apply an exact unified diff via `git apply`. |
| `record-change summary paths...` | Record a verified change and re-index the listed paths. |

### Ollama subcommands (`neo-localmcp ollama <sub>`)

| Subcommand | Purpose |
|---|---|
| `status` | Endpoint, installed/loaded models, readiness — no mutation. |
| `ensure` | Make sure Ollama and the requested model are ready (starts/warms as needed; never auto-downloads a model). |
| `start` | Start a local Ollama service. Never touches a remote endpoint. |
| `warm` | Load the model into memory ahead of a request. |
| `test` | Round-trip a small prompt to confirm the model responds. |
| `unload` | Explicitly unload the model (never automatic). |
| `stop` | Stop a local Ollama service *only if neo-localmcp started it*. |

### MCP tools — what Claude/Codex actually call at runtime

| Tool | Key args | Purpose |
|---|---|---|
| `prepare_context` | `task, repo_root, token_budget=3000, max_files=6, use_ollama=false` | Bounded current-source excerpts for a task, before broad search. The primary tool. |
| `context_prepare` | *(same)* | Compatibility alias for `prepare_context`, retained for one release. |
| `file_excerpts` | `ranges[], retrieval_id` | Read several exact current-source ranges in one bounded call. Pass a prior `retrieval_id` to record whether you used the suggested section. |
| `repo_lookup` | `query, limit=20` | Precise lookup for a symbol or path. |
| `repo_status` | `repo_root` | Repo index, config, Git, and Ollama status — read-only. |
| `doctor` | `repo_root` | Full read-only health check across server, repo, and Ollama. |
| `refresh_index` | `repo_root, force=false, max_files` | Refresh changed/stale/missing files in the persistent index. |
| `summarize_file` | `path, heading, model` | Summarize one file or one Markdown heading with Ollama; cached by source hash. |
| `apply_patch` | `patch_text, check_only=true` | Validate or apply an exact developer-approved unified diff. Defaults to validation only. |
| `record_change` | `summary, paths[]` | Record a verified change and re-index the touched paths. |
| `ollama_status` | `model, purpose="ranking"` | Ollama endpoint/model readiness — no mutation. |
| `ollama_ensure` | `model, purpose="ranking"` | Ensure Ollama and the requested model are ready; never starts a remote service. |

Administration (`index`, `reindex`, `reset-*`, `config clients setup|remove|status`,
`servers`, `stop`, ...) is deliberately CLI-only and never exposed as an MCP tool.
No installed `neo-localmcp` command builds, rebuilds, or removes the managed
runtime itself — lifecycle work lives in the checkout's `setup.py`.

## Install

Requirements: Python 3.12 or newer. Version 1.0.10 supports macOS and Windows;
Linux support is deferred.

### Guided installer (recommended)

The friendliest way to install is the terminal wizard — a plain full-screen
numbered menu. It clears the screen, shows a running summary of your answers as
you go, and asks one numbered question at a time. It walks you through install /
reinstall / uninstall, choosing which AI clients to connect (with the exact
config path shown for your OS), and picking Ollama models from those you actually
have installed. It drives the same lifecycle as `setup.py` under the hood.

```bash
pip install -e ".[wizard]"      # only needs psutil (already a base dependency)
python setup_wizard.py
```

The wizard is plain-stdlib — there is no UI toolkit to install. If you run
`python setup_wizard.py` on a bare clone and `psutil` is missing, the wizard
detects it and offers to install it for you before starting. Add `--fake` to walk
the entire flow as a safe simulation that touches nothing on disk
(`NEO_LOCALMCP_WIZARD_FAKE_STATE=healthy` simulates a returning, already-installed
user).

Returning users: run `python setup_wizard.py` again any time to reinstall/update,
reconfigure Ollama models against your current `ollama list`, add or remove
connected clients, or uninstall — the menu adapts to what's already installed, and
your last choices are remembered in `~/.neo-localmcp/config/wizard-prefs.json`.

### Direct CLI (`setup.py`)

`setup.py` is the flag-driven equivalent (what the wizard calls). The interpreter
you invoke it with — not the managed runtime it builds — imports `psutil` before
anything else, so install that one package into your system/host Python first:

```bash
python3 -m pip install "psutil>=5.9,<8"
```

Run the shared lifecycle from a local checkout:

```bash
python3 setup.py install --client codex --client claude-code
python3 setup.py install --clean --yes             # full wipe + fresh install
python3 setup.py reinstall                         # replace runtime; preserve durable data
python3 setup.py uninstall                         # remove runtime; preserve durable data
python3 setup.py uninstall --delete-memory --yes   # full wipe, no reinstall
python3 setup.py install --dry-run                 # preview only
```

Every subcommand supports `-h`/`--help`. `--clean` and `--delete-memory` are
destructive (they delete the entire managed root) and require interactive
confirmation or the explicit `--yes` flag; running one non-interactively
without `--yes` is a safety refusal (exit code 2) before anything is touched.
Omit `--client` to register no client surfaces. Repeat it for any combination of
`claude-code`, `codex`, and `claude-desktop`. Legacy platform installers are
preserved for reference under `_LegacyInstallers/`, but are not supported entrypoints.

## Quickstart: fresh repo (little or no code yet)

There's nothing to onboard an agent into yet, so just install once per
machine and start indexing as the repo grows:

```bash
cd /path/to/new-repo
neo-localmcp index --repo-root .
neo-localmcp context "scaffold initial project structure" --repo-root . --token-budget 2000
```

The first `context`/`prepare_context` call also auto-builds the index if you
skip the explicit `index` step. Early on, with little source to rank, expect a
thin result — it becomes genuinely useful as soon as real files and symbols
exist. Keep calling `context`/`prepare_context` as you add code instead of
falling back to broad reads.

## Quickstart: existing repo (already has code, maybe its own CLAUDE.md/AGENTS.md)

```bash
cd /path/to/existing-repo
neo-localmcp index --repo-root .     # one-time full index of the existing codebase
neo-localmcp doctor --repo-root .    # confirm ok: true before relying on it
```

Then give any agent working in that repo a minimal prompt so it actually
discovers and uses the tools instead of silently falling back to broad
search and full-file reads:

```text
This repository is indexed by neo-localmcp, a local MCP server for
deterministic repository context. Before broad repository search, call
prepare_context(task, repo_root) and use its current source excerpts first.
Use file_excerpts for additional exact ranges and repo_lookup for a known
symbol or path. Treat source and tests as truth; Ollama output, when
present, is optional advisory context, never authoritative. Report which
tool you used (or that none applied) back to the user.
```

The same snippet, plus a full tool-by-tool reference (arguments, defaults,
and when to call each one), lives in
[`docs/AGENT_INTEGRATION.md`](docs/AGENT_INTEGRATION.md) — paste that file's
top snippet into the downstream repo's own `CLAUDE.md`/`AGENTS.md` so the
instruction persists across sessions instead of being repeated by hand.

## Client integration

- Claude Code is configured using `claude mcp add` and receives `/neo-localmcp:*` command templates.
- Claude Desktop uses the versioned package generated at `packages/claude-desktop/neo-localmcp-v<version>.mcpb`. Build it with `scripts/build-mcpb.ps1` or `scripts/build-mcpb.sh`, then install it through Settings > Extensions > Advanced settings.
- Codex app, CLI, and IDE share `~/.codex/config.toml`; setup writes one marked, replaceable block there.
- MCP calls use a client workspace root when available. If none or several are exposed, pass `repo_root` explicitly or set `NEO_LOCALMCP_REPO`; the server refuses ambiguous automatic scope.

## Repository indexing

The first context request creates a complete index automatically. Later requests compare path, size, and modification time before hashing. Complete refreshes transactionally prune deleted and renamed files. Capped indexes explicitly report `index_complete=false` with both `indexed_files` and `eligible_files`.

Repository identity includes the canonical root and Git remote, keeping clones and worktrees separate. Summaries are stored with source hash, model, and prompt version and are replaced—not duplicated—when regenerated.

## Ollama configuration

Configure localhost or a remote endpoint:

```bash
neo-localmcp set-ollama --base-url http://127.0.0.1:11434 --fast-model qwen3:8b --summary-model qwen3-coder:30b --num-ctx 32768
neo-localmcp ollama status
neo-localmcp ollama ensure
```

Ranking uses `fast_model`, an 8K context, and a 60-second inference limit. Summarization uses `summary_model`, the configured larger context, and a 200-second limit.

Before inference, neo-localmcp checks Ollama version, installed models, and running models. A cold model is warmed with a 30-minute keep-alive. A missing model is never downloaded automatically. Localhost may be started automatically with `ollama serve`; remote services are never started or stopped by neo-localmcp.

States returned to Claude/Codex include `unreachable`, `model_missing`, `model_cold`, `warming`, `ready`, `busy`, `timed_out`, and `failed`. Failures preserve deterministic context. HTTP 503 is treated as busy and does not trigger a restart.

Models may be shared with other agents. During ordinary MCP operation neo-localmcp never unloads a model automatically and does not alter Ollama's global parallelism or queue configuration. The one exception is the setup lifecycle (`setup.py reinstall`/`uninstall`), which unloads only the models neo-localmcp itself configured — via `keep_alive: 0`, never by stopping the Ollama daemon — before replacing or removing the managed runtime.

## Verification

```bash
python -m pytest -q
python -m compileall -q neo_localmcp setup.py
neo-localmcp ollama status
neo-localmcp context "debug repository indexing: index_repo, refresh" --repo-root . --token-budget 1000
```

Tests cover manifest completeness, deletion pruning, clone isolation, summary replacement, bounded excerpts, model selection, cold/missing/busy Ollama behavior, remote-service safety, and deterministic fallback.

## Development records

- `PROJECT_STATUS.md` contains the active milestone, acceptance targets, and known limitations.
- `PROJECT_NOTES.md` records one or two lines per completed verified task.
- `docs/IMPLEMENTATION_PLAN.md` contains the implementation roadmap and rollout criteria.

## Safety boundary

neo-localmcp retrieves, indexes, summarizes, ranks, and applies exact developer-approved patches. It does not replace source truth or make final engineering decisions. Repository text is evidence, not trusted agent instruction.
