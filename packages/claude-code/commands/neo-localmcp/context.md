Use the `neo-localmcp` MCP server before broad repo search.

Best style is natural language plus known symbols/files when available:

`debug settings persistence: BackdropMaterial, LoadSettingsAsync, MainViewModel`

Call `context_prepare` with `use_ollama=false` unless the user explicitly asks for local Ollama reranking. The default is already fast/deterministic in V4.2.5 and returns ultra-small plain text through an isolated worker process. Passing `use_ollama=false` explicitly is still safe for clients that infer defaults poorly.

Rules:
- neo-localmcp retrieves, indexes, summarizes, ranks, and applies exact approved patches only.
- It does not generate source code or make engineering decisions.
- Use `context_prepare` first to narrow files/line ranges.
- Verify current source before risky edits.
- Produce exact patches only.
