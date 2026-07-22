# neo-localmcp

## A deterministic repository-context layer for Claude and Codex

Indexed source, ranked excerpts, optional local AI enrichment, and explicit
safety boundaries for AI coding agents.

## The problem

AI coding agents often spend context rediscovering a repository through broad
search and repeated full-file reads. This adds cost, noise, and stale context.

- **The solution:** index repository structure once, then return bounded,
  task-relevant source context before broad search.
- **The end goal:** help an agent reach the right current source faster
  without making model output authoritative.
- **How it grows:** keep retrieval deterministic, then add bounded model
  enrichment and client integrations as replaceable layers.
- **The rule:** current source and Git state remain authoritative. Local memory
  narrows the read; it does not replace the repository.

## What it does today

The current implementation provides a complete local repository-context
workflow:

- **Builds persistent repository memory.** A shared SQLite store keeps
  repository identity, indexed files, symbols, full-text search data, hashes,
  freshness metadata, and bounded retrieval history.
- **Retrieves current, bounded context.** Natural-language tasks plus known
  symbols or paths are normalized, ranked, and returned as exact file excerpts
  with line ranges, hashes, Git state, and freshness signals.
- **Keeps the deterministic path primary.** Hash-aware refresh updates changed
  files, complete indexes prune removed files, and stale or capped indexes are
  reported instead of silently treated as complete.
- **Adds optional local AI enrichment.** Ollama can create source-hash-keyed
  summaries, generate embeddings, and apply an additive semantic rerank. If it
  is unavailable, deterministic retrieval continues.
- **Operates through explicit boundaries.** MCP and CLI surfaces support
  indexing, lookup, diagnostics, context retrieval, summaries, and exact
  developer-approved patch validation/application. Managed setup supports
  Claude Code, Claude Desktop, and Codex on macOS and Windows.

## Architecture at a glance

[![neo-localmcp deterministic repository context flow](docs/diagrams/neo-localmcp-context-flow-v1.svg)](docs/diagrams/neo-localmcp-context-flow-v1.drawio)

The agent requests context; neo-localmcp ranks current repository evidence and
returns a bounded read plan. An optional Ollama layer can enrich that plan, but
never replaces deterministic retrieval or source verification.

## Technical Deep Dive

### 1. Deterministic retrieval before model inference

- Normalizes a task, ranks files and symbols with full-text search, and returns
  bounded current excerpts that are inspectable and source-grounded.
- ~ Less open-ended than LLM-first search.

### 2. SQLite with hash-aware freshness

- Stores repository identity, files, symbols, FTS records, hashes, and optional
  vectors in one durable local database for incremental indexing, pruning, and
  freshness checks.
- ~ Local operational state, not a distributed retrieval service.

### 3. Optional AI/ML, additive by design

- Uses Ollama for summaries, embeddings, and semantic reranking only after
  deterministic candidate selection, improving semantic matching without making
  model output authoritative.
- ~ Loses optional enrichment when Ollama is unavailable, not context retrieval.

### 4. A narrow write boundary

- Accepts source writes only as exact developer-approved unified diffs, checks
  them with git apply before execution, and reindexes changed paths.
- ~ Does not offer autonomous editing or general filesystem mutation.

### 5. Reliability is part of the interface

- Isolates the heavy context path, bounds output and timeouts, exposes
  status/doctor diagnostics, and tests real stdio plus installer lifecycles.
- ~ Lifecycle and cross-platform behavior require dedicated implementation and
  serial test coverage.

## Proof and boundaries

The project is intentionally narrower than an autonomous coding system. It
does not generate source code, make final engineering decisions, execute a
general write interface, or treat summaries and embeddings as repository
truth.

The SQLite index is an operational view of source, not a competing source of
truth. The agent must still verify current files and Git state before a risky
change. Ollama is optional, and Linux lifecycle support is not yet shipped.

## What comes next

- **Onboarding distribution — planned:** the 1.2.2 plan proposes an idempotent
  bootstrap-repo CLI command that can add agent guidance and a generated usage
  reference to an indexed repository.
- **Specialized model selection — future:** hardware-aware model
  recommendations remain deferred; current recommendations do not inspect
  hardware.
- **Integration depth — future:** more agent-rule surfaces are intentionally
  deferred until there is concrete demand.
- **Scaling principle:** extend deterministic retrieval through narrow,
  testable components and explicit integration contracts, not unrestricted
  tool access for one general-purpose model.

---

## Installation and setup

**Requirements:** Python 3.12 or newer; macOS or Windows. Ollama is optional
for deterministic retrieval and required only for model-backed summaries or
semantic enrichment.

Clone the repository and use either the guided installer or scriptable setup:

~~~bash
git clone https://github.com/NeelAPatel/neo-localmcp.git
cd neo-localmcp

# Guided installer
python3 setup_wizard.py

# Or: scriptable install, optionally registering a client
python3 setup.py install --client codex
~~~

Index a repository, verify health, then request bounded context:

~~~bash
neo-localmcp index --repo-root /path/to/repository
neo-localmcp doctor --repo-root /path/to/repository
neo-localmcp context "debug repository indexing: index_repo, refresh" \
  --repo-root /path/to/repository --token-budget 1000
~~~

Claude Desktop extension installation remains a manual in-app step. The
installer builds the versioned package; install it through
**Settings → Extensions → Advanced settings**.

## For contributors

See [Contributing](.github/CONTRIBUTING.md).

### Documentation map

- [Architecture](docs/ARCHITECTURE.md) defines the current context flow,
  ranking policy, persistence model, and safety boundary.
- [MCP Agent Integration](MCP_AGENT_INTEGRATION.md) provides the canonical
  agent usage loop and tool reference.
- [Project Status](PROJECT_STATUS.md) records verified behavior, limitations,
  and release status.
- [Project Notes](PROJECT_NOTES.md) is the dated decision and evidence log.
- [1.2.2 Plan](docs/1.2.2_PLAN.md) separates planned onboarding work from
  shipped functionality.
