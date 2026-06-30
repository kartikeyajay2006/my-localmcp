# Project Status

Updated: 2026-06-30

## Current phase

V1 reliability and token-savings implementation. Core repository indexing, bounded context retrieval, Ollama lifecycle supervision, reduced MCP tools, client configuration cleanup, packaging scaffolding, and regression tests are implemented in the working tree.

## Acceptance targets

- At least 50% fewer discovery/read tokens on representative coding tasks.
- At least 30% fewer total task tokens without lower edit or test accuracy.
- Complete indexes explicitly report completeness and prune vanished files safely.
- Ollama failures never suppress deterministic repository context.
- Normal Ollama operations have bounded health, start, warm, and inference deadlines.

## Verified

- Automated regression suite passes on Windows (28 tests), including real MCP stdio handler calls, helper-process protocol isolation, and byte-for-byte verification that every packaged `neo_localmcp` source/template matches the working tree.
- Deterministic context returns current hashed excerpts within a requested budget.
- Live Ollama status distinguishes installed and loaded models at the configured endpoint.
- Live supervisor warm-up loaded `qwen3:8b` fully into VRAM in about 14 seconds; bounded ranking inference completed in about 11 seconds and end-to-end context plus Ollama advisory completed in about 19 seconds.
- Codex app/CLI/IDE configuration is unified at `~/.codex/config.toml`.
- Windows/macOS upgrades use side-by-side virtual environments behind a stable server launcher, avoiding locked-executable replacement failures.
- A real Windows installation completed successfully after the side-by-side change; Claude Code and Codex both connect through the stable launcher.

## Remaining validation

- Run the installer, MCPB package, and client smoke tests on macOS.
- Collect baseline versus assisted token measurements from real Claude/Codex tasks.
- Local Ollama auto-start and model warm-up were verified from a genuine connection-refused state; readiness recovered in under 18 seconds without automatic model eviction.
- Validate Claude Desktop installation from the generated `.mcpb` package.

## Known limitations

- Symbol extraction remains regex-based rather than a full Tree-sitter structural index.
- Token counts are estimated from returned characters until client usage telemetry is available.
- Compatibility alias `context_prepare` remains exposed for one release.

## Next milestone

Cross-platform acceptance and token benchmark: package on Windows/macOS, run representative Python and C#/XAML tasks, compare discovery and total tokens, and tune excerpt selection from measured misses.
