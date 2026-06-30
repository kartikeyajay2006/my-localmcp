# V4.1 Changes — Agent-Ready Natural Context

- Added natural/hybrid query normalization for `context` / `context_prepare`.
- Added source-first ranking by inferred intent.
- Added source reference promotion from docs/status search hits.
- Added `read_first`, `agent_guidance`, and `interpreted_query` to context output.
- Added human-readable `neo-localmcp context` text output by default.
- Added `--format json`, `--model`, and `--no-ollama` context options.
- Added `neo-localmcp where`.
- Added `neo-localmcp model status`.
- Improved MCP tool descriptions so Claude/Codex can learn preferred usage at the tool boundary.
- Improved installer next-step messages to clarify that `index` and `context` run inside the target repo.
- Installer now uses `pip install --upgrade --force-reinstall` to overwrite an existing installed package.
- Cleanup script now skips locked Windows files instead of crashing and supports `--force` while preserving `.zip` files.
- README rewritten around install/overwrite, target repo workflow, Claude/Codex usage, commands, architecture, and troubleshooting.
