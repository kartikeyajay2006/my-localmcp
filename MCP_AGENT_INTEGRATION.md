# Agent integration

This file is the sidecar reference for an AI agent working in a repository
that neo-localmcp indexes. It has two parts:

1. A short paste-able snippet for the *downstream* repo's own
   `CLAUDE.md`/`AGENTS.md`, so the instruction persists across sessions.
2. A full tool-by-tool reference so an agent that reads this file directly
   knows exactly what each tool does, its arguments, and when to call it.

## 1. Paste-into-downstream-repo snippet

Paste this into the downstream repo's `CLAUDE.md`/`AGENTS.md` (not this
repo's) to get an agent to actually discover and use neo-localmcp there,
instead of silently falling back to broad search and full-file reads.

```text
This repository is indexed by neo-localmcp, a local MCP server for deterministic
repository context. Before broad repository search, read neo-localmcp's own
README/tool docs, then decide concretely where in *this* project it is the right
tool:

- Repo-wide task context -> prepare_context(task, repo_root, token_budget, max_files)
- A specific known file/range -> file_excerpts
- A symbol or path lookup -> repo_lookup

Report which of these you used (or that none applied) back to the user rather than
silently deciding. Use its current source excerpts first; request additional exact
ranges only when needed. Treat source and tests as truth. Ollama output, when
present, is optional advisory context, never authoritative.
```

## 2. Full MCP tool reference

Everything below is retrieval, indexing, ranking, or summarization.
**neo-localmcp never generates or edits source code**, except applying an
exact, developer-approved unified diff via `apply_patch` — and even that
defaults to validation-only (`check_only=true`). Administrative commands
(`index`, `reindex`, `reset-repo`, `reset-all`, `setup`, `servers`, `stop`,
...) are CLI-only and are never exposed as MCP tools; an agent cannot trigger
them.

Deterministic retrieval never depends on Ollama. Every Ollama-touching tool
degrades gracefully (missing/cold/busy/failed) without blocking or emptying
its response, except `summarize_file`, whose entire purpose is a model
summary.

### `prepare_context` (primary entry point)

```text
prepare_context(task: str, repo_root="auto", token_budget=3000, max_files=6, use_ollama=false, model=null)
```

Call this **before broad repository search**. Pass a natural-language or
hybrid task string — mixing plain description with known symbols/files after
a colon sharpens ranking, e.g. `"debug settings persistence: AppSettings,
LoadSettingsAsync"`. Returns ranked current-source excerpts with hashes, line
ranges, related tests, a `retrieval_id`, and explicit agent guidance. Purely
deterministic unless `use_ollama=true`, in which case a ranking model adds an
advisory read-order on top of the same deterministic result (never replaces
it). Pass `repo_root` explicitly whenever it's known; if omitted, the server
uses MCP workspace roots and refuses ambiguous scope rather than guessing.

`context_prepare` is a compatibility alias for the same tool, retained for one
release — prefer `prepare_context`.

### `file_excerpts`

```text
file_excerpts(ranges: [{path, start_line?, end_line?, ...}], repo_root="auto", max_chars=20000, retrieval_id=null)
```

Use for additional **exact** ranges once `prepare_context` has narrowed
things down — not as a first move. Pass the `retrieval_id` from a prior
`prepare_context` call to record whether the range you pulled overlapped
what was suggested; this only feeds a capped, observational retrieval-memory
signal and never changes what is returned.

### `repo_lookup`

```text
repo_lookup(query: str, repo_root="auto", limit=20)
```

Precise lookup for a known symbol name or path, ranked by relevance. Use when
you already know what you're looking for by name, rather than describing a
task.

### `repo_status` / `doctor`

```text
repo_status(repo_root="auto")
doctor(repo_root="auto")
```

Both are read-only. `repo_status` reports index counts, Git state, and Ollama
reachability for one repo. `doctor` is the broader health check (config, DB,
Ollama, running servers, command inventory). Use either to sanity-check
before relying on retrieval, especially after a version upgrade or if results
look stale.

### `refresh_index`

```text
refresh_index(repo_root="auto", force=false, max_files=null)
```

Updates only changed/stale/missing files since the last index. Cheap; safe to
call proactively if you suspect the index is behind recent edits.
`force=true` forces a full re-hash.

### `summarize_file`

```text
summarize_file(path: str, repo_root="auto", heading=null, model=null)
```

The one tool that **requires** Ollama — it produces a model-written summary
of one file (or one Markdown heading section within it), cached by source
hash so it's only regenerated when the file actually changes. If Ollama is
unavailable, this call fails explicitly rather than silently returning
nothing; it does not affect any other tool's deterministic behavior.

### `apply_patch`

```text
apply_patch(patch_text: str, repo_root="auto", check_only=true)
```

The only tool that can mutate files, and only for an **exact,
developer-approved** unified diff — neo-localmcp never invents the diff
content itself. Defaults to `check_only=true` (runs `git apply --check` and
reports whether the patch would apply cleanly, without touching disk). Only
pass `check_only=false` once a human has approved the exact diff text.

### `record_change`

```text
record_change(summary: str, paths: [str], repo_root="auto")
```

Call after a verified edit (whether made via `apply_patch` or by the
developer directly) to log what changed and re-index the touched paths, so
the next `prepare_context` call reflects current source immediately rather
than waiting for the next refresh.

### `ollama_status` / `ollama_ensure`

```text
ollama_status(model=null, purpose="ranking")
ollama_ensure(model=null, purpose="ranking")
```

`ollama_status` is read-only: endpoint, installed/loaded models, readiness
state (`unreachable`, `model_missing`, `model_cold`, `warming`, `ready`,
`busy`, `timed_out`, `failed`). `ollama_ensure` will start/warm a **local**
Ollama and the requested model if needed, but never starts or stops a remote
endpoint, and never downloads a missing model automatically. Use these to
check or prepare Ollama readiness before opting into `use_ollama=true` on
`prepare_context`, or before `summarize_file`.
