# Project Notes

## 2026-06-30

- Added complete manifest accounting, safe deletion pruning, metadata-first freshness checks, clone/worktree-safe identity, and content-addressed summary replacement.
- Added budgeted multi-file source excerpts and a single batched repository search to reduce repeated agent reads.
- Replaced direct Ollama generation with purpose-aware readiness, warm-up, bounded recovery, busy handling, local ownership, and deterministic fallback.
- Reduced the MCP surface, added workspace-root resolution, unified Codex configuration, and added Claude Desktop MCPB packaging scaffolding.
- Added Windows/macOS install lifecycle helpers, built a validated 1.0.0 Claude Desktop MCPB bundle, and expanded the regression suite to 14 passing tests on Windows.
- Verified live `qwen3:8b` cold-model warm-up, ready-state reporting, bounded inference, and combined deterministic/Ollama context output on the Windows 4080 host.
- Added explicit warm-timeout classification so model loading delays cannot be mislabeled as supervisor lock contention.
