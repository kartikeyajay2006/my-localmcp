---
description: Retrieve indexed context and excerpts for one file
argument-hint: "<file-path> [line]"
---

Use the `neo-localmcp` MCP server for this command.

Call `file_excerpts` with the exact path and explicit `repo_root`. If a line is supplied, request a bounded range around it; otherwise request the first 80 lines. Preserve returned line numbers and mention truncation.
