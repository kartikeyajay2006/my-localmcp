## What

<!-- One or two sentences: what changed and why. -->

## Verification

**Code edits made** (`neo_localmcp/` or `tests/`)
- [ ] `pytest -q -m "not slow"` passes locally
- [ ] Rebuilt `.mcpb` if `neo_localmcp/` changed: `bash scripts/build-mcpb.sh` (or `.ps1` on native PowerShell) — writes `packages/claude-desktop/neo-localmcp-v<version>.mcpb`. If `npx` fails to find `mcpb`, see "Rebuilding the .mcpb bundle" in `.github/CONTRIBUTING.md`.

**No code edits** (root/docs-only, `.github/`, non-code files)
- [ ] No pytest needed

## Notes

<!-- Anything a reviewer (including future-you) needs to know: platforms actually tested, known gaps, follow-ups. -->
