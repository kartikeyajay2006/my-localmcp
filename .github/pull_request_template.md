## What

<!-- One or two sentences: what changed and why. -->

## Verification

- [ ] `python -m pytest -q` passes locally (not just `compileall` — the full suite catches things compileall can't, like distribution/packaging checks)
- [ ] `python -m compileall -q neo_localmcp setup.py` is clean
- [ ] If `neo_localmcp/` changed: the versioned `.mcpb` bundle was rebuilt (`scripts/build-mcpb.sh` / `.ps1`) — `test_distribution.py` will fail CI otherwise
- [ ] `PROJECT_STATUS.md` / `PROJECT_NOTES.md` updated if this changes verified behavior (see `CLAUDE.md`)
- [ ] Non-trivial changes got a `/code-review` pass (this is a solo-maintainer project — there's no second human reviewer, so this is the substitute; not GitHub-enforced, a personal/agent discipline)
- [ ] CI is green on this PR (both macOS and Windows) before merging — do not merge on a hunch that it'll pass

## Notes

<!-- Anything a reviewer (including future-you) needs to know: platforms actually tested, known gaps, follow-ups. -->
