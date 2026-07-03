# neo-localmcp Architecture

## Product name

The product/MCP name is always `neo-localmcp` unless explicitly changed later.

## V4/V4.2 boundary

`neo-localmcp` is a deterministic repository context layer. It is not a coding agent.

> `neo-localmcp` never generates source code. It only retrieves, indexes, summarizes, ranks, and applies developer-approved patches.

## Roles

## Setup lifecycle

`setup.py` is the sole supported lifecycle policy surface on macOS and Windows.
It builds and validates a candidate virtual environment before promotion, stops
only processes with verified Neo ownership, unloads only Neo-configured Ollama
models, preserves durable data by default, and restores recorded client targets.
Legacy platform scripts are archived under `_LegacyInstallers/`; Linux support is
deferred beyond 1.0.10.

### Claude / Codex

- Understand requirements
- Reason about implementation
- Debug and research
- Design changes
- Produce exact patches

### neo-localmcp

- Detect repo root
- Index files and symbols
- Track file hashes/freshness
- Normalize natural/hybrid context queries
- Rank relevant files and line ranges
- Follow source references from docs/status files
- Return agent guidance
- Apply exact approved unified diffs
- Re-index changed files

### Ollama

- Summarization
- Compression
- Ranking
- Metadata extraction style work

Ollama is local preprocessing, not final authority.

## Context flow

```text
Claude/Codex task
  ↓
context_prepare("debug X: SymbolA, FileName.cs")
  ↓
query normalization
  ↓
repo index + grep + symbol lookup
  ↓
source-first ranking by intent
  ↓
optional Ollama reranking
  ↓
agent guidance: read order + line hints
  ↓
Claude/Codex reads current source and reasons
```

## Query normalization

V4.2 accepts natural and hybrid input. Preferred style:

```text
<natural task>: <known symbols, files, APIs, errors>
```

The parser extracts:

- intent: debug / feature / refactor / test / explain / context
- strong terms: known symbols/files after `:` or CamelCase/method names
- weak terms: useful domain terms from natural text
- ignored terms: filler words like explain, identify, likely, files
- ranking policy: source-first, tests-first, or orientation-first

## Ranking policy

For debug/feature/refactor:

1. Source files
2. Tests
3. Config
4. Project status/notes
5. Docs
6. Instructions

For explain/overview:

1. Project status/notes
2. Docs
3. Source
4. Tests

Docs/status files can promote source files when they mention real file paths.

## Persistent repo context

Stored under the configured app home, normally:

```text
~/.neo-localmcp/repo-context.sqlite
```

Main tables:

- repos
- files
- symbols
- task_queries
- change_events
- repo_fts

File entries are hash-aware. Changed files are re-indexed.

## Safety model

- Cached context narrows reads; it does not replace source truth.
- The current source file and git diff are authoritative.
- Patches must come from Claude/Codex/user as exact diffs.
- `neo-localmcp apply-patch` uses `git apply --check` before applying.
- After patch application, changed files are re-indexed.
