---
description: Search indexed repository paths and symbols
argument-hint: "<symbol-or-query>"
---

Use the `neo-localmcp` MCP server for this command.

Call `repo_lookup` once with the user's query, explicit `repo_root`, and a limit of 10 unless they request otherwise. Report exact path/symbol hits. If it returns no hits, say so; do not substitute fuzzy guesses.
