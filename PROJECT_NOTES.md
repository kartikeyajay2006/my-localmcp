# Project Notes

## 2026-06-30

- Added complete manifest accounting, safe deletion pruning, metadata-first freshness checks, clone/worktree-safe identity, and content-addressed summary replacement.
- Added budgeted multi-file source excerpts and a single batched repository search to reduce repeated agent reads.
- Replaced direct Ollama generation with purpose-aware readiness, warm-up, bounded recovery, busy handling, local ownership, and deterministic fallback.
- Reduced the MCP surface, added workspace-root resolution, unified Codex configuration, and added Claude Desktop MCPB packaging scaffolding.
- Added Windows/macOS install lifecycle helpers, built a validated 1.0.0 Claude Desktop MCPB bundle, and expanded the regression suite to 17 passing tests on Windows.
- Verified live `qwen3:8b` cold-model warm-up, ready-state reporting, bounded inference, and combined deterministic/Ollama context output on the Windows 4080 host.
- Added explicit warm-timeout classification so model loading delays cannot be mislabeled as supervisor lock contention.
- Verified automatic localhost recovery from a stopped Ollama service through service start, `qwen3:8b` warm-up, and ready-state confirmation.
- Changed installers to side-by-side versioned virtual environments and stable MCP launchers so active Windows clients cannot lock an in-place upgrade.
- Added idempotent Claude Code registration migration from the legacy project-local venv command to the stable user-scope launcher.
- Completed a real side-by-side Windows install and migrated Claude Code plus Codex to the stable server launcher.
- Fixed Windows MCP worker Unicode output by enforcing UTF-8 across the subprocess protocol, added a CP1252 regression test, and released the rebuilt Claude Desktop bundle as 1.0.1.
- Prepared 1.0.2 from a live Claude f4-plan trial: exact document paths now outrank keyword noise, colon focus ignores filler terms while preserving milestone tokens such as `f4`, read-only lookup no longer runs Git metadata probes, and omitted Ollama `:latest` tags resolve to installed models.
- Expanded the 1.0.2 MCP bundle so `/doctor`, `/status`, `/index`, `/refresh`, `/summarize`, and exact approved `/apply-patch` operations have dedicated tools instead of being guessed as unrelated calls or requiring a CLI fallback.
- Released 1.0.3 after live Claude testing exposed a stdio-only deadlock: helper Git processes inherited the MCP protocol input handle. Helper commands now use a closed stdin, and a real client/server stdio regression test covers refresh, lookup, and patch validation.
- Corrected context selection so explicit paths always lead, explanation/context intents follow ranked docs rather than an unconditional source-first pass, filename token matches outrank repeated cross-document references, and stale line hints clamp safely to EOF.
- Verified the complete AntiNotepad MCP suite live: repository tools return in under two seconds, the f4 plan ranks first for the reported query, and full-file Ollama summarization completes successfully in about 23 seconds.
- Investigated a reported per-call process leak with a controlled 30-call single-session test: both `conhost.exe` and neo-localmcp-related process counts returned exactly to baseline after disconnect. Persistent Python processes map to active MCP client sessions, including Windows venv shim/base-interpreter pairs, rather than tool calls.
- Hardened all short-lived Git, context-worker, and client-setup subprocesses with closed stdin and Windows `CREATE_NO_WINDOW` so helpers cannot consume MCP protocol input or allocate transient console hosts.
- Released the subprocess hardening as 1.0.4. A supplied WER dump was also reviewed and identified as a kernel blue screen (`0x7E` / kernel access violation), not a Python, conhost, or neo-localmcp application crash.
