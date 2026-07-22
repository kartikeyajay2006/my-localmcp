# neo-localmcp

## A deterministic repository-context layer for Claude and Codex

Indexed source, ranked excerpts, optional local AI enrichment, and explicit
safety boundaries for AI coding agents.

## The problem

AI coding agents often spend context rediscovering a repository through broad
search and repeated full-file reads. This adds cost, noise, and stale context.

`neo-localmcp` builds a persistent repository index, retrieves task-relevant
source, and returns bounded excerpts that the agent can verify against current
files and Git state.

## The goal

Help the agent reach the right current source faster without making model
output authoritative. Deterministic retrieval is the foundation; Ollama adds
optional summarization, embeddings, and reranking when configured.
