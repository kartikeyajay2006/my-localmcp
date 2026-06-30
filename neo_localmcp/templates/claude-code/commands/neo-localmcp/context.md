Use the `neo-localmcp` MCP server before broad repo search.

Best style is natural language plus known symbols/files when available:

`debug settings persistence: BackdropMaterial, LoadSettingsAsync, MainViewModel`

Call `prepare_context` with a token budget near 3000 and at most six files. Use `use_ollama=false` unless local reranking is useful; deterministic current-source excerpts are authoritative.

Rules:
- neo-localmcp retrieves, indexes, summarizes, ranks, and applies exact approved patches only.
- It does not generate source code or make engineering decisions.
- Use `prepare_context` first to obtain bounded current-source excerpts.
- Verify current source before risky edits.
- Produce exact patches only.
