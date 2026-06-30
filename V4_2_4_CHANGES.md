# neo-localmcp V4.2.5

MCP runtime safety fix for Claude Code / Claude Desktop / Codex clients.

## Fixes

- `context_prepare` now returns ultra-small plain text by default over MCP.
- `context_prepare` runs in a short-lived worker subprocess from the MCP server.
- No-Ollama `context_prepare` has a hard MCP worker timeout.
- Worker subprocess is killed on timeout so stale MCP server processes do not keep accumulating.
- Added `context_prepare_json` as an explicit diagnostic MCP tool for compact JSON.
- CLI still supports full `context --format json`; MCP default is now `mcp_text`.
- Updated Claude slash command guidance to mention the V4.2.5 safe worker path.

## Why

V4.2.3 proved that:

- CLI deterministic context works.
- direct Python `tools.context_prepare(..., use_ollama=False)` works.
- MCP `model_status` works.
- MCP `context_prepare` could still hang inside Claude Code.

V4.2.5 isolates `context_prepare` from the long-running MCP server process and makes the MCP payload plain text and small.
