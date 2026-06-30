# V4.2.2 Changes — Determinism Test Harness + Reset Tools

V4.2.2 supersedes V4.2.1. Do not install V4.2.1 separately.

## Main fixes

- Added `neo-localmcp reset-repo --yes`.
  - Deletes only the current repo's indexed context.
  - Keeps global config and other repos.
- Added `neo-localmcp reset-all --yes`.
  - Deletes the full repo context SQLite DB.
  - Keeps `config.yaml` and installed client configs.
- Added `neo-localmcp test-determinism`.
  - Runs the same no-Ollama context query multiple times.
  - Compares stable output projection hashes.
  - Supports `--reset-repo` and `--reindex-first`.
- Bumped indexer version to `0.4.2.2`.
- Scoring now counts each unique evidence reason once.
  - Prevents duplicate FTS/search evidence from changing deterministic scores.
- Fixed a duplicate status count query.

## Ollama behavior

Ollama was not weakened.

- `context` remains deterministic/no-Ollama by default for fast first-pass repo narrowing.
- `context --ollama-rank` remains available as the opt-in second-pass reviewer.
- The Ollama prompt is stronger:
  - It is told it improves deterministic context, not replaces it.
  - It must keep source/test files above docs for debug/feature/refactor tasks.
  - It must not tell the agent to skip source files that directly contain requested symbols.
  - Candidate payload size was restored upward so Ollama sees richer context.

## Recommended serious test

```powershell
cd F:\LocalVSProj\AntiNotepad
neo-localmcp test-determinism "debug settings persistence: BackdropMaterial, LoadSettingsAsync, SaveSettingsAsync" --reset-repo --runs 5
neo-localmcp context "debug settings persistence: BackdropMaterial, LoadSettingsAsync, SaveSettingsAsync" --ollama-rank
```
