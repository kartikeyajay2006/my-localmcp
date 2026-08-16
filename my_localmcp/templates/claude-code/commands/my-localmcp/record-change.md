---
description: Record a verified change and refresh affected files
argument-hint: "<summary> [paths...]"
---

Use the `my-localmcp` MCP server for this command.

Rules:
- my-localmcp retrieves, indexes, summarizes, ranks, and applies exact approved patches only.
- It does not generate source code or make engineering decisions.
- Prefer `context_prepare` before broad repo search. Context is deterministic/no-Ollama by default in V4.2.
- Treat cached context as a way to narrow reads; current source remains truth.
