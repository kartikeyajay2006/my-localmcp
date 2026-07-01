# Agent integration snippet

Paste this into the downstream repo's `CLAUDE.md`/`AGENTS.md` (not this repo's) to
get an agent to actually discover and use neo-localmcp there, instead of silently
falling back to broad search and full-file reads.

```text
This repository is indexed by neo-localmcp, a local MCP server for deterministic
repository context. Before broad repository search, read neo-localmcp's own
README/tool docs, then decide concretely where in *this* project it is the right
tool:

- Repo-wide task context -> prepare_context(task, repo_root, token_budget, max_files)
- A specific known file/range -> file_excerpts
- A symbol or path lookup -> repo_lookup

Report which of these you used (or that none applied) back to the user rather than
silently deciding. Use its current source excerpts first; request additional exact
ranges only when needed. Treat source and tests as truth. Ollama output, when
present, is optional advisory context, never authoritative.
```
