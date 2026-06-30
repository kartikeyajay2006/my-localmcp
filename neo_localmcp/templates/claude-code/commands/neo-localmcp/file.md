---
description: Retrieve indexed context and excerpts for one file
argument-hint: "<file-path> [line]"
---

Use the `neo-localmcp` MCP server for this command.

Rules:
- neo-localmcp retrieves, indexes, summarizes, ranks, and applies exact approved patches only.
- It does not generate source code or make engineering decisions.
- Prefer `context_prepare` before broad repo search. Context is deterministic/no-Ollama by default in V4.2.
- Treat cached context as a way to narrow reads; current source remains truth.
