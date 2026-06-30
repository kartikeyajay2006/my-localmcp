# V4 changes

This ZIP is a fresh-install baseline, not an upgrade/migration release.

## Removed from V3 scope

- Migration command and migration module.
- Old `/neo:*` command namespace.
- Route-management subsystem.
- Patch review/handoff tools.
- External Serena/codebase-memory sidecar orchestration.
- C#/XAML-specific helper tools.
- Broad memory/orchestrator framing.

## Added / simplified

- Official product and MCP server name is always `neo-localmcp`.
- Primary CLI command is `neo-localmcp`.
- Claude Code slash namespace is `/neo-localmcp:*`.
- Persistent repo context is stored in `~/.neo-localmcp/repo-context.sqlite`.
- Hash-aware indexing skips unchanged files.
- File summaries are tied to file hashes and treated as helper context, not truth.
- Exact patch application is available through `git apply`; patch generation is intentionally outside neo-localmcp.
- One-time cleanup scripts remove old MCP venture files/references while preserving `.zip` files.

## Core rule

`neo-localmcp` never generates source code. It only retrieves, indexes, summarizes, and applies developer-approved patches.
