# V1 Implementation Plan

## Objective

Reduce expensive-model repository discovery by returning a small, fresh, task-specific bundle of source excerpts. Target 50% fewer discovery/read tokens and 30% fewer total tokens without reducing edit accuracy.

## Milestone 1: correctness and bounded retrieval — implemented

- Complete manifest accounting with explicit capped-index status.
- Transactional pruning of deleted and renamed paths after complete scans.
- Metadata-first freshness checks, canonical-root repository identity, and summary provenance.
- `prepare_context` with token budget, six-file default, hashes, exact excerpts, and retrieval metrics.
- Batched live search plus indexed symbol/path lookup.

## Milestone 2: Ollama reliability — implemented

- Purpose-aware fast and summary models with separate contexts and deadlines.
- Version, installed-model, and loaded-model preflight.
- Local auto-start, model warm-up, cross-process serialization, 30-minute keep-alive, and failure cooldown.
- Remote endpoint safety, missing-model fallback, busy classification, and no automatic unload.
- Compact MCP readiness tools and complete status attached to Ollama-backed results.

## Milestone 3: distribution and compatibility — implemented, cross-platform validation pending

- Small MCP tool surface and server instructions.
- Explicit or MCP-provided repository roots with ambiguous-scope refusal.
- Shared Codex app/CLI/IDE configuration.
- Claude Desktop MCPB manifest and build scripts.
- Idempotent installer modes and data-preserving uninstallers.

## Milestone 4: acceptance — active

- Run automated tests and client smoke tests on Windows and macOS.
- Build/install the Claude Desktop package on both platforms.
- Capture baseline and assisted traces for Python, JS/HTML/CSS, SQL, C#/XAML, Markdown, Docker, and cloud-config tasks.
- Record search calls, source bytes, estimated/actual tokens, latency to first edit, changed files, and test outcomes.
- Accept V1 after reaching the token targets without an accuracy regression; otherwise tune excerpt ranking before adding embeddings.

## Deferred

- Tree-sitter structural parsing and call graphs.
- Embedding retrieval.
- Long-running filesystem watcher daemon.
- Automatic model downloads or shared-model eviction.
