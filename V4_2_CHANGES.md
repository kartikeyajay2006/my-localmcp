# V4.2 Changes — Fast Deterministic Context Polish

- `neo-localmcp context` is deterministic/no-Ollama by default.
- Added `neo-localmcp context --ollama-rank` to opt into Ollama reranking.
- Kept `--no-ollama` as a compatibility flag.
- Added `neo-localmcp reindex` as a friendly force-rebuild command.
- Added repo indexer version tracking (`0.4.2`) so an upgraded indexer can force/recommend clean reindexing.
- Added stable ordering for deterministic context output.
- Fixed docs/status source reference promotion so docs line numbers no longer become source file line hints.
- Compacted line hints and agent guidance.
- Reduced Ollama ranking prompt size and made its section structure stricter.
- Updated MCP tool descriptions so agents know context is fast/deterministic by default and Ollama is opt-in.
