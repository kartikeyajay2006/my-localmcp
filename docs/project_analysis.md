# Comprehensive Analysis: my-localmcp

`my-localmcp` is a localized Model Context Protocol (MCP) server that provides deterministic, hash-aware repository context (indexed symbols, files, full-text search, and tests) to AI coding agents (such as Claude Code, Claude Desktop, or Codex). It prevents models from wasting their context windows on redundant or open-ended filesystem reads/greps, offering a bounded read plan.

---

## 1. Core Objectives and Philosophy

### The Problem
AI coding agents often spend significant tokens and time rediscovering repository structures through broad, open-ended searches, directory listings, or repeated full-file reads. This leads to high cost, noise, and stale/hallucinated context.

### The Solution: Deterministic Layer First
1. **Index Once, Retrieve Bounds**: Index repository structure once in a local SQLite database, and retrieve only task-relevant source context.
2. **Deterministic Primary Path**: Natural-language and hybrid queries are normalized and matched using Full-Text Search (FTS) and symbol lookup first. Deterministic results are *never* dependent on local AI models (Ollama).
3. **Optional Local AI Enrichment**: Local LLMs (via Ollama) can be used optionally to write file summaries, generate embeddings, and apply an additive semantic reranking step. If Ollama is down, context retrieval degrades gracefully and remains fully operational.
4. **Authoritative Source & Git State**: The project adheres to the rule that the actual repository files and Git state remain the absolute source of truth. The database is merely a cached query index.
5. **Narrow Write Boundary**: The tool allows file mutations only by applying exact developer-approved unified diffs (using `git apply --check` first). It does not autonomously write code or edit files.

---

## 2. Module Map & Directory Layout

The codebase is modularized as follows:

```
my-localmcp/
├── pyproject.toml              # Build & dependency configurations (Python 3.12+)
├── setup.py                    # Scriptable installer & management CLI
├── setup_wizard.py             # Interactive CLI install wizard
├── my.toml                    # Brand configuration file
├── docs/                       # Architectural specs and release plans
├── packages/                   # Managed client bundles (e.g. claude-desktop .mcpb)
└── my_localmcp/               # Main source package
    ├── __init__.py             # Defines the package version
    ├── runtime_cli.py          # Entrypoint for the 'my-localmcp' administrative CLI
    ├── config.py               # Config defaults, APP_DIR (~/.my-localmcp), config.yaml
    ├── branding.py             # Naming constants (derived from my.toml)
    ├── repo_utils.py           # Path validation, git info, subprocess, symbol regexes
    ├── ollama_client.py        # Bounded Ollama API client (tags, chat, embed)
    ├── ai_client_config.py     # Claude Desktop/Code/Codex integration managers
    ├── mcp_server_lifecycle.py # supervising server processes, stopping, PID tracking
    ├── benchmarker/            # Ground-truth evaluation queries for testing retrieval
    ├── templates/              # Markdown templates for custom slash commands
    ├── retrieval/              # The deterministic database lookup core
    │   ├── query.py            # Natural/hybrid task-query parsing & tokenizing
    │   └── repo_memory.py      # SQLite operations (indexing, FTS, retrieval-boost memory)
    └── mcp/                    # MCP server surface and tool handlers
        ├── server.py           # FastMCP entrypoint & stdio loop registration
        ├── context_worker.py   # Isolated subprocess executor for preparing context
        ├── system.py           # Lifecycle tools (init, status, doctor, reindex, reset)
        ├── memory.py           # The context pipeline (ranking, excerpt framing, boost logic)
        ├── ollama.py           # Ollama configuration tools
        ├── editing.py          # Unified patch application and file summarization
        └── _shared.py          # Common JSON and printing utilities
```

---

## 3. Database Schema (`repo-context.sqlite`)

Repository state is centralized in `~/.my-localmcp/repo-context.sqlite`. The primary schema includes:

- **`repos`**: Tracks registered repositories (`id`, `canonical_root`, `remote_url`, `last_indexed_at`, `indexed_commit`).
- **`files`**: Records individual files, content hashes, and optional summarization metadata (`path`, `content_hash`, `summary`, `summary_model`, `indexed_at`).
- **`symbols`**: Stores extracted symbols for fast indexing (`name`, `kind`, `path`, `start_line`, `end_line`).
- **`repo_fts`**: Virtual table backing Full-Text Search (FTS5) over paths, file content, and symbol names.
- **`task_queries`**: Logs past retrieval queries (`id`, `task_string`, `query_vector`, `embed_model`, `created_at`) used for reinforcement learning.
- **`retrieval_boost`**: Maps task query signatures to files that the agent/developer successfully followed or read (`term_key`, `path`, `shown_count`, `followed_count`, `corrected_count`). If a path is repeatedly used for a query, it gains a score boost.

---

## 4. Query Normalization & Ranking Policy

### Normalization
When an agent invokes `prepare_context("debug settings: LoadSettingsAsync")`, the query is parsed into:
* **Intent**: Detects intent keywords like `debug`, `test`, `refactor`, `explain`.
* **Strong terms**: CamelCase, snake_case, method calls, or specific terms following a colon (`:`).
* **Weak terms**: General natural-language terms.
* **Ignored terms**: Common fillers like "files", "explain", "code".

### Ranking Rules
Ranking scores candidates (files and symbol locations) using structural FTS and symbol matches:
* **Source-First (Default for debug/feature/refactor)**:
  1. Source files (highest weight)
  2. Test files
  3. Configurations
  4. Project status/notes
  5. Documentation
* **Explain/Overview First**:
  1. Project status / notes (highest weight)
  2. Documentation files
  3. Source files
  4. Test files
* **Boosts**:
  * An exact match on a strong term gives a significant weight bump.
  * A file containing multiple unique matched terms ranks higher than a file with repeated hits on a single term.
  * Retrieval-boost memory adds a small, capped nudge (+2 to +8) for repeatedly read files.
  * **Additive Semantic Re-ranking**: If `embed_model` is configured in Ollama, vectors are generated for candidates and compared to the task embedding. Cosine similarity is multiplied by 18 and added to the candidate's score. Semantic ranking *never* overrides strong structural FTS matches.

---

## 5. Unified Setup & Administration

`my-localmcp` is administered via a CLI, but operates silently inside your IDE as an MCP server.

### Installation Options
* **Guided CLI Wizard (Recommended)**:
  ```bash
  python3 setup_wizard.py
  ```
  This interactive terminal wizard walks you through checking prerequisites, configuring Ollama, selecting target clients, and creating the initial configuration.
* **Scripted Setup**:
  ```bash
  # Standard install
  python3 setup.py install

  # Register a specific client target (e.g. Claude Code or Codex)
  python3 setup.py install --client codex
  ```

### CLI Command Reference
* `my-localmcp index --repo-root <path>`: Index/reindex a repository.
* `my-localmcp refresh --repo-root <path>`: Incrementally index changed or stale files.
* `my-localmcp doctor`: Run self-diagnostic health checks.
* `my-localmcp context "<task>"`: Retrieve bounded context in JSON or formatted text directly from the CLI.
* `my-localmcp stop`: Gracefully terminate running MCP server processes (handles file locks cleanly on Windows).

---

## 6. MCP Tool Reference

Once registered, the server exposes the following tools to the AI agent:

| Tool Name | Purpose | Key Arguments |
|---|---|---|
| **`prepare_context`** | **Primary entrypoint.** Evaluates a task and returns ranked source code excerpts under a token budget. | `task`, `repo_root`, `token_budget`, `use_ollama` |
| **`file_excerpts`** | Fetches exact line ranges for specific files. | `ranges` (list of `{path, start_line, end_line}`), `retrieval_id` |
| **`repo_lookup`** | Performs a fast, exact search for a symbol or file path. | `query`, `repo_root`, `limit` |
| **`repo_status` / `doctor`** | Diagnostic tools reporting indexed file counts, git state, and Ollama status. | `repo_root` |
| **`refresh_index`** | Updates the local database for changed files since the last indexing pass. | `repo_root`, `force` |
| **`summarize_file`** | Generates an AI-written summary of a file or Markdown heading (cached; requires Ollama). | `path`, `heading`, `model` |
| **`apply_patch`** | **Only writer tool.** Validates/applies a unified diff. Defaults to validation mode. | `patch_text`, `check_only` |
| **`record_change`** | Tells the database that a file changed so it can immediately re-index it. | `summary`, `paths` |
| **`ollama_status` / `ollama_ensure`** | Queries Ollama readiness and warms up/loads a selected model. | `model`, `purpose` |

---

## 7. Development and Safety Guidelines

- **Upgrade Safety**: Swapping Ollama models or config parameters does not invalidate database summaries unless the source file is modified or a manual reindex is triggered.
- **Comment Standards**: Code comments in the codebase are concise and pseudocode-like (maximum of 3 lines per function). Arrow-based breadcrumb comments (`parent -> child`) are used to represent structural workflows.
- **Workflow & Merge Policy**: Direct pushes to `main` are blocked. All changes must go through a pull request and pass the parallel automated CI suite (macOS + Windows). Squash/rebase merges are disabled; only standard merge commits are allowed.
- **Platforms**: macOS and Windows are officially supported. Linux setup lifecycle support is currently deferred.
