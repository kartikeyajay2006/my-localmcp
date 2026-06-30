---
description: Apply an exact, developer-approved unified patch
argument-hint: "<patch-file> [--check-only]"
---

Read the exact unified diff from the supplied patch file, then call the `apply_patch` MCP tool with explicit `repo_root`. Use `check_only=true` unless the user explicitly asked to apply the patch. neo-localmcp validates and applies exact patches only; it never generates one.
