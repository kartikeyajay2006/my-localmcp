---
description: Summarize one file with the configured Ollama model
argument-hint: "<file-path>"
---

Call the `summarize_file` MCP tool with the exact file path and explicit `repo_root`. It reads the file with the configured summary model and stores the result. Do not emulate it with `prepare_context`, which ranks candidates but does not summarize file contents.
