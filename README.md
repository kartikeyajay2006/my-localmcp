# neo-localmcp

`neo-localmcp` is an agent-ready local MCP server and CLI that helps Claude/Codex work in large repositories without repeatedly grepping and rereading the same files.

It keeps persistent repository working context, ranks relevant files/line ranges, and can use Ollama on your local machine/4080 for cheap ranking and summarization.

## V4/V4.2 rule

> `neo-localmcp` never generates source code. It only retrieves, indexes, summarizes, ranks, and applies developer-approved patches.

Claude/Codex remain responsible for reasoning, debugging, architecture, and exact patch creation. Current source files and git diff are always the truth.

## What V4.2 adds

V4.2 is **Fast Deterministic Context Polish**:

- `context` is deterministic/no-Ollama by default.
- New `context --ollama-rank` opt-in for local model reranking.
- New `neo-localmcp reindex` alias for force rebuilding repo context.
- Indexer version tracking so upgrades can force/recommend clean reindexing.
- Natural + hybrid context queries.
- Source-first ranking for debug/feature/refactor work.
- Stable output ordering for repeated deterministic runs.
- Docs/status hits can promote referenced source files without leaking docs line numbers into source line hints.
- Compact agent guidance.
- Visible Ollama model/timing when `--ollama-rank` or summarization uses Ollama.


## V4.2.5 Ollama safety

V4.2.5 keeps deterministic `READ FIRST` authoritative and treats Ollama as a bounded advisory only. The default Ollama timeout is now 200 seconds. If Ollama times out or fails, MCP clients still receive the deterministic context result.

## Install or overwrite existing V4

Extract the ZIP and open a terminal in the extracted `neo-localmcp` folder.

### Windows PowerShell

```powershell
powershell -ExecutionPolicy Bypass -File .\install.ps1
```

### Windows CMD

```cmd
install.cmd
```

### macOS / Linux

```bash
./install.sh
```

The installer overwrites/upgrades the installed package inside:

```text
~/.neo-localmcp/venv
~/.neo-localmcp/bin
```

It does **not** delete your repo context database by default.

Primary command:

```bash
neo-localmcp
```

If the command is not immediately on PATH, open a new terminal or call the installed command directly:

```powershell
$neo = "$env:USERPROFILE\.neo-localmcp\bin\neo-localmcp.cmd"
& $neo where
```

## Correct first-time usage

Run setup once from anywhere:

```bash
neo-localmcp setup --client all
```

Then go to the repo you want analyzed:

```bash
cd /path/to/your/repo
neo-localmcp index
neo-localmcp context "debug settings persistence: BackdropMaterial, LoadSettingsAsync"
```

On Windows example:

```powershell
cd F:\LocalVSProj\AntiNotepad
neo-localmcp index
neo-localmcp context "debug settings persistence: BackdropMaterial, LoadSettingsAsync"
```

## Preferred query style

`neo-localmcp` accepts normal English, but it works best with a simple hybrid style:

```text
<natural task>: <known symbols, files, APIs, errors>
```

Examples:

```bash
neo-localmcp context "debug settings persistence: BackdropMaterial, LoadSettingsAsync, SaveSettingsAsync"
neo-localmcp context "fix startup crash: App.xaml.cs, MainWindow, InitializeComponent"
neo-localmcp context "implement font size setting: Settings flyout, AppSettings, MainViewModel"
neo-localmcp context "explain swipe navigation: NavigateAsync, WouldBlockNavigation, MainPage.xaml.cs"
```

This format is recommended, not required. V4.2 normalizes messy input by dropping filler words, detecting symbols/files, inferring intent, and ranking source/tests/docs accordingly.

## Claude/Codex usage pattern

Tell Claude/Codex:

```text
Before broad repo search, use neo-localmcp context_prepare.
Ask naturally, but include known symbols/files when possible:
"debug X: SymbolA, SymbolB, FileName.cs".
Use the result to narrow reads. Verify current source before editing.
Produce exact patches only.
```

Claude should call `context_prepare` before grepping broadly. It should then read the recommended current source files/line ranges, reason normally, and produce an exact patch.

## Commands

### `neo-localmcp where`

Shows install/config paths, current repo root, repo DB, and configured Ollama model.

```bash
neo-localmcp where
```

### `neo-localmcp doctor`

Checks config, DB, repo status, command inventory, and Ollama reachability.

```bash
neo-localmcp doctor
```

### `neo-localmcp status`

Fast status for the current repo.

```bash
neo-localmcp status
neo-localmcp status --repo-root /path/to/repo
```

### `neo-localmcp setup`

Installs MCP config/slash commands for supported clients.

```bash
neo-localmcp setup --client all
neo-localmcp setup --client claude-desktop
neo-localmcp setup --client codex
neo-localmcp setup --dry-run
```

### `neo-localmcp index`

Indexes current repo files and symbols into persistent repo context.

```bash
neo-localmcp index
neo-localmcp index --max-files 1000
neo-localmcp index --force
```

Run this from the repo you want analyzed. If you just upgraded and want a clean rebuild, run `neo-localmcp reindex`.

### `neo-localmcp refresh`

Hash-aware re-index of stale/missing files.

```bash
neo-localmcp refresh
```

### `neo-localmcp reindex`

Force rebuild repository context with the current V4.2 indexer. Use after upgrading if context output looks stale.

```bash
neo-localmcp reindex
```

### `neo-localmcp context`

Prepares agent-ready context for a task.

```bash
neo-localmcp context "debug settings persistence: BackdropMaterial, LoadSettingsAsync"
neo-localmcp context "debug settings persistence"
neo-localmcp context "debug settings persistence" --ollama-rank --model qwen3:8b
neo-localmcp context "debug settings persistence" --format json
```

CLI default is readable text. MCP tools return structured JSON. V4.2 does not call Ollama for context unless `--ollama-rank` is used.

### `neo-localmcp lookup`

Lower-level search of repo context memory.

```bash
neo-localmcp lookup "MainViewModel"
```

Prefer `context` for agent workflow.

### `neo-localmcp file`

Returns cached file context, symbols, freshness, and optional excerpt.

```bash
neo-localmcp file AntiNotepad/ViewModels/MainViewModel.cs
neo-localmcp file AntiNotepad/ViewModels/MainViewModel.cs --around-line 77
```

### `neo-localmcp summarize`

Summarizes one file with Ollama and stores the summary as working context.

```bash
neo-localmcp summarize AntiNotepad/ViewModels/MainViewModel.cs
neo-localmcp summarize AntiNotepad/ViewModels/MainViewModel.cs --model qwen3:8b
```

### `neo-localmcp apply-patch`

Applies an exact approved unified diff using `git apply`.

```bash
neo-localmcp apply-patch fix.patch --check-only
neo-localmcp apply-patch fix.patch
```

`neo-localmcp` does not create the patch.

### `neo-localmcp record-change`

Records a completed change and re-indexes listed paths.

```bash
neo-localmcp record-change "Fixed settings persistence" AntiNotepad/ViewModels/MainViewModel.cs
```

### `neo-localmcp model status`

Shows configured Ollama settings and reachable Ollama models.

```bash
neo-localmcp model status
```

### `neo-localmcp set-ollama`

Sets local or remote Ollama defaults.

```bash
neo-localmcp set-ollama --base-url http://127.0.0.1:11434
neo-localmcp set-ollama --base-url http://your-4080-pc:11434 --summary-model qwen3-coder:30b --fast-model qwen3:8b --num-ctx 32768
```

## MCP tools

The server exposes these MCP tools:

- `where`
- `status`
- `doctor`
- `repo_index`
- `repo_refresh`
- `repo_lookup`
- `file_context`
- `context_prepare`
- `summarize_file`
- `apply_unified_patch`
- `record_change`
- `set_ollama`
- `model_status`

`context_prepare` is the main tool agents should use before broad repo search.

## Claude Code slash commands

Setup installs `/neo-localmcp:*` commands under `~/.claude/commands/neo-localmcp`:

```text
/neo-localmcp:status
/neo-localmcp:doctor
/neo-localmcp:index
/neo-localmcp:refresh
/neo-localmcp:lookup
/neo-localmcp:file
/neo-localmcp:context
/neo-localmcp:summarize
/neo-localmcp:apply-patch
/neo-localmcp:record-change
```

Recommended first move in a repo:

```text
/neo-localmcp:context debug the task: KnownSymbol, FileName.cs
```

## Optional one-time cleanup of old experiments

The cleanup script removes old `neo`, `neo-local`, `neo-local-agent`, and earlier `neo-localmcp` files/references. It preserves `.zip` files.

Dry run first:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\cleanup-old-neo-mcp.ps1
```

Apply:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\cleanup-old-neo-mcp.ps1 --apply
```

Try harder on read-only files while still preserving `.zip` files:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\cleanup-old-neo-mcp.ps1 --apply --force
```

If Windows reports a locked `.pyd`, `.dll`, or `.exe`, the script now skips it and tells you to close Python/Claude/MCP processes before rerunning.

macOS/Linux:

```bash
./scripts/cleanup-old-neo-mcp.sh
./scripts/cleanup-old-neo-mcp.sh --apply
```

## Architecture

```text
Claude / Codex
  ├─ reason, debug, design, create exact patches
  ↓
neo-localmcp
  ├─ index repo files/symbols
  ├─ normalize natural/hybrid context queries
  ├─ rank source/tests/docs by intent
  ├─ return files, symbols, line ranges, and guidance
  ├─ apply exact approved patches only
  └─ re-index changed files
  ↓
Ollama
  ├─ summarize
  ├─ compress
  ├─ rank
  └─ extract metadata-style context
```

Safety model:

- Memory/context narrows reads; it does not replace source truth.
- Summaries are tied to file hashes.
- Changed files force re-index.
- Claude/Codex must verify current source before risky edits.
- `neo-localmcp` never invents code.

## Troubleshooting

### `neo-localmcp` not found

Open a new terminal, or run the installed command directly:

```powershell
$neo = "$env:USERPROFILE\.neo-localmcp\bin\neo-localmcp.cmd"
& $neo where
```

### Context is indexing the wrong folder

Run:

```bash
neo-localmcp where
```

Then `cd` into the repo you actually want analyzed and run:

```bash
neo-localmcp index
neo-localmcp context "your task: KnownSymbol"
```

### Claude Code cannot see MCP

```bash
neo-localmcp setup --client all
claude mcp list
```

### Ollama/model not obvious

```bash
neo-localmcp model status
neo-localmcp context "your task" --format json
```

The context result includes `ollama_timing` when Ollama is called.

## V4.2.2 deterministic testing commands

V4.2.2 adds reset and determinism-test commands so you can test the index cleanly instead of manually deleting SQLite files.

Reset only the current repo from the shared DB:

```bash
neo-localmcp reset-repo --yes
```

Reset the entire repo context database while keeping config/client setup:

```bash
neo-localmcp reset-all --yes
```

Forcefully test deterministic context behavior:

```bash
neo-localmcp test-determinism "debug settings persistence: BackdropMaterial, LoadSettingsAsync, SaveSettingsAsync" --reset-repo --runs 5
```

Ollama remains opt-in for context ranking:

```bash
neo-localmcp context "debug settings persistence: BackdropMaterial, LoadSettingsAsync, SaveSettingsAsync" --ollama-rank
```

The determinism test intentionally disables Ollama. Test Ollama separately because model output is allowed to vary.
