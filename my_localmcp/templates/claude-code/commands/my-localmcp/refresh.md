---
description: Refresh stale or changed files in the repository index
argument-hint: "[repo-path]"
---

Call the `refresh_index` MCP tool with explicit `repo_root` and `force=false`. Report indexed, unchanged, removed, and error counts. Do not substitute `prepare_context`; refresh is an explicit index operation.
