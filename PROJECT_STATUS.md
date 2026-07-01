# Project Status

Updated: 2026-07-01

## Current phase

V1 reliability and token-savings implementation. Core repository indexing, bounded context retrieval, Ollama lifecycle supervision, reduced MCP tools, client configuration cleanup, packaging scaffolding, and regression tests are implemented in the working tree.

1.0.9 onboarding-polish work (`docs/1.0.9_PLAN.md`) is complete and released: all phases 9a-9g (agent-integration doc, install-time Ollama presence/model check, versioned `.mcpb` packaging, called-out post-install indexing nudge, installer surface selection with path preview, the uninstaller overhaul with multi-select surfaces + granular data keep/delete, and the P5 retrieval-boost audit + upgrade-persistence guarantee + config-tuning) are implemented and verified. `__version__` is now `1.0.9`, bumped in lockstep across `neo_localmcp/__init__.py`, `pyproject.toml`, and `packages/claude-desktop/mcpb/manifest.json`; the versioned bundle `packages/claude-desktop/neo-localmcp-v1.0.9.mcpb` is built and the real `~/.neo-localmcp` CLI install was upgraded to 1.0.9 live (`doctor` ok, single `.venv-nlm-v1.0.9`).

## Acceptance targets

- At least 50% fewer discovery/read tokens on representative coding tasks.
- At least 30% fewer total task tokens without lower edit or test accuracy.
- Complete indexes explicitly report completeness and prune vanished files safely.
- Ollama failures never suppress deterministic repository context.
- Normal Ollama operations have bounded health, start, warm, and inference deadlines.

## Verified

- Automated regression suite passes on Windows (82 tests), including real MCP stdio handler calls, helper-process protocol isolation, byte-for-byte verification that every packaged `neo_localmcp` source/template matches the working tree, Markdown heading-section retrieval, retrieval-memory behavior, Ollama enrichment bounding, the graceful-stop lifecycle, and the Windows upgrade cycle end-to-end including the 1.0.8 single-venv-per-version behavior (same-version reinstall is a no-op, `-Repair` forces a real rebuild, a different version gracefully stops and replaces the old venv).
- 1.0.8 built and installed: `packages/claude-desktop/neo-localmcp.mcpb` rebuilt and verified byte-identical to source; the CLI install (`~/.neo-localmcp`) was upgraded from a real leftover 1.0.7 install to 1.0.8. The separately-installed Claude Desktop/Code extension was deliberately left untouched — swap the rebuilt `.mcpb` in manually when ready.
- **Retrieval-boost memory (P5) is now audited, upgrade-safe, and tunable (1.0.9, 9g)**: a 2026-07-01 live multi-call session against this repo confirmed the wiring works as designed -- `shown`/`followed`/`corrected` counts move on real `prepare_context`+`file_excerpts` calls, a boost only appears once the same task has been shown >= `min_shown` (3) times, grows with net-followed, and stays capped (8) far below any structural signal (a heading milestone match is +60); silence (shown-but-not-followed) is never penalized. Upgrade persistence is now asserted by a test: `retrieval_boost` rows survive a real venv-swap install intact (the guarantee is data-level -- the db file bytes do legitimately change because `init`/`doctor` write to it, but the memory data does not). `RETRIEVAL_BOOST_CAP`/`RETRIEVAL_BOOST_MIN_SHOWN` are promoted to config (`memory.retrieval_boost_cap`/`retrieval_boost_min_shown`), defaults unchanged since the audit found no evidence to move them (kept at 3 by explicit decision).
- Deterministic context returns current hashed excerpts within a requested budget.
- Live Ollama status distinguishes installed and loaded models at the configured endpoint.
- Live supervisor warm-up loaded `qwen3:8b` fully into VRAM in about 14 seconds; bounded ranking inference completed in about 11 seconds and end-to-end context plus Ollama advisory completed in about 19 seconds.
- Codex app/CLI/IDE configuration is unified at `~/.codex/config.toml`.
- **Windows upgrades are now genuinely graceful, verified live, not just in principle**: a running server responds to `neo-localmcp stop` by exiting itself within seconds, releasing its file locks cleanly.
- **Windows installs now keep exactly one venv per version, not one per install run** (1.0.8): 1.0.7's side-by-side scheme was a workaround for the pre-graceful-stop era and, left in place, caused real repeated-pip-install disk churn (traced live to spiking C: drive utilization across a heavy testing session, unrelated to Ollama model storage on a separate drive). Reinstalling the same version is now a ~1.2-second no-op instead of a 40-90 second rebuild, verified live; upgrading to a new version sweeps the entire legacy `venvs/` directory in one pass. macOS (`install.sh`) is still on the old side-by-side scheme, same 7d gap as before, tracked in `docs/1.0.7_PLAN.md`.
- **1.0.9 phases 9a-9f verified**: `install.ps1` reports Ollama presence and per-model (`fast_model`/`summary_model`) availability at install time without ever blocking -- every external Ollama call is wrapped in a time-bounded (10-15s) PowerShell job after a real hang was caught by `tests/test_upgrade_cycle.py` and fixed. `.mcpb` packaging is versioned (`packages/claude-desktop/neo-localmcp-v1.0.8.mcpb`, rebuilt and verified byte-identical to source), with a fixed-name local copy still placed at `~/.neo-localmcp/neo-localmcp.mcpb` for Claude Desktop's manual-install instructions. `setup.ps1`'s client registration step is now per-surface (Claude Code / Codex / Claude Desktop) with a live path preview sourced from `client_status()` before any files are touched. The uninstaller is a per-surface multi-select with a granular keep/delete checklist for CLI local state (venv/config/database/mcpb/servers), each destructive data category behind its own typed-`DELETE` gate and the shared-database warning; new `remove_claude_code()`/`remove_codex()` + `neo-localmcp remove-client` back it. A silent data-loss bug (PowerShell array-splat dropping every switch, so a confirmed database delete was preserved) was caught by live testing and fixed with a hashtable splat; a wizard-driving regression test now guards it. Full regression suite passes including the real install/uninstall subprocess integration tests.

## Remaining validation

- Run the installer, MCPB package, and client smoke tests on macOS (including graceful-stop and single-venv-per-version parity, currently Windows-only — see `docs/1.0.7_PLAN.md` 7d).
- Collect baseline versus assisted token measurements from real Claude/Codex tasks.
- Local Ollama auto-start and model warm-up were verified from a genuine connection-refused state; readiness recovered in under 18 seconds without automatic model eviction.
- Validate Claude Desktop installation from the generated `.mcpb` package.
- **New, found 2026-07-01**: `ollama_client.py`'s `start_service()` does not reliably inherit a custom `OLLAMA_MODELS` env var when spawned under some process ancestries (e.g. under Claude Desktop's extension host), silently falling back to the near-empty default models path instead of erroring. Confirmed live on a real machine with models on an external drive. Not yet fixed.

## Known limitations

- Symbol extraction remains regex-based rather than a full Tree-sitter structural index.
- Token counts are estimated from returned characters until client usage telemetry is available.
- Compatibility alias `context_prepare` remains exposed for one release.
- macOS/Linux install/uninstall scripts do not yet have the graceful-stop / single-venv-per-version upgrade flow (Windows-only so far).
- Section-summary caching is keyed on source-file content hash only, not on the neo-localmcp code version -- a cache entry generated by a buggy older version is not automatically invalidated when the bug is fixed; only a source-file content change (or manual cache clear) will regenerate it.
- `start_service()` may silently use the wrong Ollama models directory depending on process ancestry (see Remaining validation above).
- Retrieval-boost memory: "can this be counted on to improve retrieval over time?" is now a qualified yes, not the previous no. It provably works and survives upgrades (see Verified), and it can never hurt correctness -- it's capped far below structural evidence and never penalizes silence. But its impact is modest by design: it only nudges once the *same task string* recurs >= 3 times in one repo, so it helps repeated workflows in a repo, not first-time or one-off tasks, and its real-world benefit has still only been exercised in one controlled audit session, not measured across sustained multi-session usage. It is a safe, bounded nudge, not a broadly-learning ranker.

## Next milestone

Cross-platform acceptance and token benchmark: package on Windows/macOS, run representative Python and C#/XAML tasks, compare discovery and total tokens, and tune excerpt selection from measured misses.
