"""The deterministic retrieval engine.

``repo_memory`` is the SQLite index and persistence layer (files, symbols,
FTS, and the implicit-feedback retrieval-boost memory); ``query`` parses a
task string into intent plus strong/weak terms. Together they are the
model-free core that ``mcp.memory`` ranks against -- nothing here depends on
Ollama.
"""
