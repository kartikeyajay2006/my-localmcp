# neo-localmcp

`neo-localmcp` is a local repository-context server for Claude and Codex. It indexes a repository once, incrementally refreshes changed files, and returns a bounded bundle of current source excerpts so the primary model can avoid repeated broad searches and full-file reads.

V1 targets at least a 50% reduction in discovery/read tokens and a 30% reduction in total task tokens without reducing edit accuracy. Deterministic source retrieval is always authoritative; Ollama is optional preprocessing for ranking and summarization.

## Core workflow

The primary MCP tool is:

```text
prepare_context(task, repo_root, token_budget=3000, max_files=6)
```

It returns ranked current-source excerpts, hashes, line ranges, related symbols/tests, selection reasons, index completeness, and approximate retrieval-token measurements. Use `file_excerpts` for additional exact ranges and `repo_lookup` for precise symbols or paths.

Recommended agent instruction:

```text
Before broad repository search, call neo-localmcp prepare_context.
Use its current source excerpts first. Request additional exact ranges only when needed.
Treat source and tests as truth. Ollama output is optional advisory context.
```

## Install

Requirements: Python 3.10 or newer. Windows and macOS are primary targets.

Windows:

```powershell
powershell -ExecutionPolicy Bypass -File .\install.ps1
neo-localmcp setup --client all
```

macOS:

```bash
./install.sh
neo-localmcp setup --client all
```

Both installers are idempotent. Upgrades create side-by-side environments under `~/.neo-localmcp/venvs` and repoint stable CLI/MCP launchers, so an active desktop client cannot lock the upgrade target. Use `-DryRun`/`--dry-run`, `-Repair`/`--repair`, or `-Upgrade`/`--upgrade`. Uninstalling preserves configuration and repository memory unless the remove-data option is supplied:

```powershell
.\uninstall.ps1
```

```bash
./uninstall.sh
```

## Client integration

- Claude Code is configured using `claude mcp add` and receives `/neo-localmcp:*` command templates.
- Claude Desktop uses the package generated at `packages/claude-desktop/neo-localmcp.mcpb`. Build it with `scripts/build-mcpb.ps1` or `scripts/build-mcpb.sh`, then install it through Settings > Extensions > Advanced settings.
- Codex app, CLI, and IDE share `~/.codex/config.toml`; setup writes one marked, replaceable block there.
- MCP calls use a client workspace root when available. If none or several are exposed, pass `repo_root` explicitly or set `NEO_LOCALMCP_REPO`; the server refuses ambiguous automatic scope.

## Repository indexing

Run manually when desired:

```bash
neo-localmcp index --repo-root /path/to/repo
neo-localmcp status --repo-root /path/to/repo
```

The first context request creates a complete index automatically. Later requests compare path, size, and modification time before hashing. Complete refreshes transactionally prune deleted and renamed files. Capped indexes explicitly report `index_complete=false` with both `indexed_files` and `eligible_files`.

Repository identity includes the canonical root and Git remote, keeping clones and worktrees separate. Summaries are stored with source hash, model, and prompt version and are replaced—not duplicated—when regenerated.

## Ollama

Context retrieval works without Ollama. Enable it per request with `use_ollama=true` or CLI `--ollama-rank`.

Configure localhost or a remote endpoint:

```bash
neo-localmcp set-ollama --base-url http://127.0.0.1:11434 --fast-model qwen3:8b --summary-model qwen3-coder:30b --num-ctx 32768
neo-localmcp ollama status
neo-localmcp ollama ensure
```

Ranking uses `fast_model`, an 8K context, and a 60-second inference limit. Summarization uses `summary_model`, the configured larger context, and a 200-second limit.

Before inference, neo-localmcp checks Ollama version, installed models, and running models. A cold model is warmed with a 30-minute keep-alive. A missing model is never downloaded automatically. Localhost may be started automatically with `ollama serve`; remote services are never started or stopped by neo-localmcp.

States returned to Claude/Codex include `unreachable`, `model_missing`, `model_cold`, `warming`, `ready`, `busy`, `timed_out`, and `failed`. Failures preserve deterministic context. HTTP 503 is treated as busy and does not trigger a restart.

Administrative commands:

```bash
neo-localmcp ollama status
neo-localmcp ollama ensure
neo-localmcp ollama start
neo-localmcp ollama warm
neo-localmcp ollama test
neo-localmcp ollama unload   # explicit only
neo-localmcp ollama stop     # only a service started by neo-localmcp
```

Models may be shared with other agents. neo-localmcp never unloads automatically and does not alter Ollama's global parallelism or queue configuration.

## CLI reference

Normal workflow:

```bash
neo-localmcp context "debug model startup: ensure, warm, status" --repo-root . --token-budget 3000 --max-files 6
neo-localmcp lookup ensure --repo-root .
neo-localmcp file neo_localmcp/ollama_client.py --around-line 200 --context-lines 20
neo-localmcp record-change "Fixed model readiness" neo_localmcp/ollama_client.py --repo-root .
```

Administration remains CLI-only: `doctor`, `where`, `index`, `refresh`, `reindex`, `reset-repo`, `reset-all`, `test-determinism`, `setup`, `set-ollama`, `summarize`, and exact approved `apply-patch`.

The MCP surface includes `prepare_context`, compatibility alias `context_prepare`, `file_excerpts`, `repo_lookup`, `repo_status`, `doctor`, `refresh_index`, `summarize_file`, safe-by-default `apply_patch`, `record_change`, `ollama_status`, and `ollama_ensure`. Patch application defaults to check-only and requires `check_only=false` for an exact developer-approved diff.

## Verification

```bash
python -m pytest -q
python -m compileall -q neo_localmcp
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
