# V4.2.3 Changes — MCP Client Compatibility

V4.2.3 is focused on making the same fast deterministic context path work through MCP clients, not just the CLI.

## Fixes

- `context_prepare` MCP tool now returns compact MCP-safe JSON by default.
- `context_prepare` MCP default is explicitly no-Ollama / deterministic.
- MCP `context_prepare` default limit reduced to 5 read-first files.
- Full diagnostic search payload remains available from CLI `--format json`; MCP does not dump it by default.
- Added `clients` CLI command and `clients_status` MCP tool.
- Expanded setup targets:
  - `claude-code`
  - `claude-desktop`
  - `codex-cli`
  - `codex-desktop`
  - `codex` = both Codex targets
  - `all` = Claude Code + Claude Desktop + Codex CLI + Codex Desktop
- Claude Code setup tries `claude mcp add --scope user` first, then falls back to the older command.
- Claude Desktop setup writes `claude_desktop_config.json` and creates a backup before modifying it.
- Codex CLI setup writes `~/.codex/config.toml`.
- Codex Desktop setup writes the platform app-support Codex `config.toml` path.

## Ollama behavior

Ollama did not get weaker:

- CLI still uses `--ollama-rank` for optional reranking.
- MCP uses `use_ollama=true` for optional reranking.
- Deterministic read order remains the source of truth.
- The Ollama prompt still receives the ranked candidates, scores, reasons, and line hints.
