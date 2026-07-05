# CI speed: measure-gated phased reduction of macOS+Windows CI wall-clock

## Origin

CI on `setup-v2.yml` (macOS + Windows, Python 3.12) runs long enough to be a
drag on the merge-only `main` workflow, where every change — including one-line
doc edits — needs green CI. The owner asked to "prioritize how to make these CI
tests faster," working inside-out, and explicitly chose **CI-speed first,
measured**: target the actual bottlenecks, establish a measured baseline, and
pursue structural refactors only as a separate later track where they
demonstrably help.

This spec covers the CI-speed track only. The structural-refactor track
(`tools.py`/`context_prepare` decomposition, `client_setup.py`↔`installer/clients.py`
consolidation, per-OS source unification, `_LegacyInstallers/` removal, wholesale
test rework) is **out of scope** here and is already partly tracked by existing
issues (#29, #30, #31, #32, #33). Those are deferred deliberately: they are good
hygiene but do not move CI time on their own.

## Measured baseline (local, 10-core M-series; captured 2026-07-04)

| Segment | Serial | Parallel (`-n auto`) | Notes |
|---|---|---|---|
| Fast suite (`-m "not slow"`, 287 tests) | 39.0s | 11.6s | ~20s of the serial time is `test_retrieval_memory.py` rebuilding a real repo index and looping `prepare_context` (memory boost needs a query to recur ≥3×). |
| Slow suite (`-m slow`, 7 tests) | not isolated-timed | deliberately serial | Each `test_full_*_lifecycle` runs ~5 real `python -m venv` + `pip install` cycles hitting the network. **This is the CI wall-clock dominator.** |

Workflow shape today: one job per OS, steps run **sequentially** —
`pip install -e ".[test]"` → fast suite → slow lifecycle → compile. No
parallelism between fast and slow. `-n auto` (pytest-xdist, issue #12) already
landed and is the only current speedup.

Re-measure after every phase, locally and on the PR's CI. Track results in the
table at the bottom of this doc.

## Assumptions validated against the code (2026-07-04)

- **"Mac/Windows is just pathing" — ~90% true.** Source is *unified*, not split
  per-OS. Branches concentrate in `installer/paths.py` (`Scripts`/`bin`, `.exe`
  suffix) plus one genuine Windows quirk, `rehome_windows_launchers` in
  `installer/runtime.py`. The per-OS *split* the owner had in mind lives in the
  **test files** (`test_macos_lifecycle.py`, `test_windows_lifecycle.py`), not
  the source.
- **"Duplicate setup files" — not in the installer.** `installer/` is 12 focused
  modules; `setup.py`/`setup_wizard.py` are thin entrypoints. The real overlap
  candidate is `client_setup.py` (460 LOC) vs `installer/clients.py` (360 LOC) —
  deferred to issue #33.
- **CI time is dominated by real work, not structure**: repeated indexing in the
  fast suite and real venv/pip builds in the slow suite. Confirms the
  CI-speed-first sequencing.

## Scope: five phases, lowest-risk-first, measure-gated

Phase 0 ships as its own standalone PR (immediate savings on all in-flight
meta/docs PRs). Phases 1–4 ship on one CI-speed branch, phased commits, single
PR. #13 (per-area CI reporting) is deferred entirely.

### Phase 0 — Skip test steps on docs/meta-only PRs (closes #14)

**Own PR, ships first.** A change touching only `.md` / `.github` files should
not re-run the full matrix (~6–10 min for a README edit).

**The trap (must be handled):** workflow-level `paths-ignore` **breaks branch
protection**. `main` requires check contexts `macos-latest / Python 3.12` and
`windows-latest / Python 3.12` to report success; a skipped workflow never
reports them, leaving the PR "pending" forever and unmergeable.

**Correct approach:** use `dorny/paths-filter` *inside* the job. The job still
runs and still reports its required status; when only docs/meta files changed,
the test-running steps are conditionally skipped (`if:` guards) and the check
goes green fast without executing the suite.

**Prerequisite:** issue #14 sequenced this after #12 (xdist), which has already
landed — so #14 is unblocked.

**Risk:** zero coverage impact. Only skips execution when no code/tests changed.

### Phase 0b — Stop double-running the whole matrix on every PR

The workflow triggers on **both** `push` and `pull_request`, so a same-repo
branch with an open PR runs the entire macOS+Windows matrix **twice** per commit
(observed live on PR #42: two identical Windows runs, one even flaked while the
other passed — see the reliability note below). Add a `concurrency` group
(`group: ci-${{ github.ref }}`, `cancel-in-progress: true`) and/or scope the
triggers so each commit runs the matrix once. Roughly halves CI minutes on PR
branches. Zero coverage impact; fold into the Phase 0 PR.

### Phase 1 — Split each OS job into parallel fast + slow jobs

Wall-clock per OS goes from `fast + slow` → `max(fast, slow)`.

- Job A (per OS): deps + fast suite (`-n auto`) + compile.
- Job B (per OS): deps + slow lifecycle suite (stays serial).

The matrix stays `{macos-latest, windows-latest}`; each OS now contributes two
jobs. The `dorny/paths-filter` guard from Phase 0 applies to both.

**Required-check naming (decided):** branch protection currently requires
`macos-latest / Python 3.12` and `windows-latest / Python 3.12`. Splitting jobs
renames/multiplies check contexts, which can silently make protection
unenforced. **Chosen approach:** add a single aggregating `ci-gate` job that
`needs:` all split jobs and is the *only* required context; make branch
protection require `ci-gate` instead of the per-OS contexts. This decouples the
required-check list from the job layout, so future splits (e.g. #13's per-area
jobs) don't repeatedly break protection. **Hard gate:** confirm protection
enforces `ci-gate` and that `ci-gate` correctly fails when any upstream job
fails, before relying on it.

**Trade-off:** duplicates the cached `pip install` setup across two jobs — cheap
relative to removing the fast↔slow serialization.

**Risk:** zero code/test change; the only risk is the branch-protection naming,
handled above.

### Phase 2 — Offline wheelhouse for lifecycle installs (keeps coverage)

The lifecycle tests do real `venv` + `pip install` ~5× each, hitting the network
and rebuilding. Make each install offline wheel-unpacking without changing what
the tests assert.

**Mechanism (no production-code change):** at CI-job start, build a wheelhouse —
a wheel of `neo-localmcp` plus its deps (`mcp[cli]`, `psutil`) — then set
`PIP_NO_INDEX=1` and `PIP_FIND_LINKS=<wheelhouse>` in the job environment. pip
honors both env vars **natively**, so every `pip install` the lifecycle runs
(including the `--force-reinstall` in `installer/runtime.py`'s `install_command`,
and the pip self-upgrade) resolves from local wheels offline.

**Why this is the safe seam:** it touches the *environment*, not
`runtime.py`'s command construction and not the test assertions. Production
behavior is unchanged; only the source of wheels moves from PyPI to a local dir.

**Caveat to verify:** the wheelhouse must contain a `pip` wheel too if any cycle
upgrades pip under `PIP_NO_INDEX`; otherwise skip/relax the pip self-upgrade for
tests. Confirm venv creation cost (`python -m venv`) separately — it is not a
pip operation and this phase does not address it (accepted, or revisited in
Phase 4 if it dominates).

**Risk:** zero coverage impact if assertions are untouched.

### Phase 3 — Session-scoped prebuilt-index fixture for the fast suite

Remove the ~20s of repeated real indexing in `test_retrieval_memory.py` /
`test_context.py`.

**Mechanism:** build the indexed corpus **once** in a session-scoped fixture
(one repo + one SQLite index). Each test copies the prebuilt SQLite index file
into its isolated `APP_DIR` (cheap file copy) instead of rebuilding it. This
preserves the existing per-test isolation — retrieval-memory boost state is
per-DB and stays separate because each test still gets its own copied DB — while
amortizing the index build.

**Risk:** medium. Requires care that (a) tests mutating the DB do so on their own
copy, (b) retrieval-memory accumulation tests still start from the intended
state, (c) determinism assertions still hold. Validate by running the affected
files repeatedly and diffing outputs against the current suite before/after.

**Reliability note (observed on PR #42, 2026-07-04):**
`tests/test_distribution.py::test_repo_tools_respond_over_real_stdio` spawns a
**real MCP server subprocess over stdio** with a 5s-per-call timeout, yet is
unmarked so it runs inside the `-n auto` fast suite. On a loaded Windows runner
it flaked with `anyio.BrokenResourceError` (server starved at startup, pipe
broke) — the same commit passed on the parallel duplicate run. This is both a
reliability and a speed liability. Options (pick during Phase 1/3): mark it
`slow` so it lands in the serial lifecycle job, isolate it from `-n auto`
(xdist group), or raise the startup/first-call timeout. Cheap; do it alongside
the job restructuring.

### Phase 4 — Trim redundant lifecycle build cycles (measure-gated, may skip)

**Only after re-measuring Phases 0–3.** If the slow suite is still the
dominator, audit the ~5 cycles in `test_full_*_lifecycle`; merge or remove any
cycle asserting an invariant already covered elsewhere. Each removal needs an
explicit written justification naming the invariant and where it stays covered.

If Phase 2 already made cycles cheap, consciously **not** doing this is a valid
outcome. CLAUDE.md documents these as deliberately real-build, serial, and
coverage-critical; do not weaken them without a measured payoff.

**Risk:** coverage-sensitive. Highest-scrutiny phase; gated on measurement.

## Non-goals / deferred

- **#13 (per-area CI reporting)** — deferred entirely. Needs test-tree
  reorganization by area + backfilling the currently-absent `neo_localmcp/wizard/`
  pytest tests. Its own effort later.
- **Structural refactors** — #29, #30, #31, #32, #33 and the `tools.py`
  god-file / per-command split. Separate track, only where proven to help CI.
- **venv creation speedup via `virtualenv`/`uv`** — not pursued unless Phase 4
  measurement shows venv creation (not pip) is the residual dominator; would
  introduce a dependency, so it stays out until data justifies it.
- **Linux CI** — remains deferred per repo policy.

## Sequencing and branches

1. **PR-0:** branch `chore/ci-skip-docs-meta-issue-14` → Phase 0 → closes #14.
   Ships first, independently mergeable.
2. **PR-1:** branch `perf/ci-speed` → Phases 1–4 as phased commits, single PR.
   Re-measure after each phase; Phase 4 is conditional on Phase 1–3 results.

Both PRs follow repo convention: `type(area): description` titles, matching
`type:`/`area:` labels, green CI before merge, merge-commit strategy.

## Testing / verification per phase

- **Phase 0:** open a docs-only PR and a code PR; confirm the required contexts
  report green on both, and that the docs-only PR skipped the suite (job log
  shows the skip) while still being mergeable under branch protection.
- **Phase 1:** confirm both split jobs report; confirm branch protection still
  enforces (required-check list updated); compare total wall-clock to baseline.
- **Phase 2:** confirm lifecycle tests still pass with `PIP_NO_INDEX=1`; confirm
  no network fetch occurs (offline); time the slow suite vs baseline.
- **Phase 3:** run affected fast-suite files repeatedly; diff outputs vs current
  suite; confirm determinism/retrieval-memory tests unchanged; time fast suite.
- **Phase 4:** for each trimmed cycle, document the covered invariant; full slow
  suite still green on both OSes.

## Measurement log (fill in as phases land)

| Milestone | Fast suite (CI) | Slow suite (CI) | Total per-OS wall-clock | Notes |
|---|---|---|---|---|
| Baseline | TBD (measure on CI) | TBD | TBD | local: fast 39.0s serial / 11.6s `-n auto` |
| After Phase 0 | — | — | — | docs/meta PRs only |
| After Phase 1 | | | | |
| After Phase 2 | | | | |
| After Phase 3 | | | | |
| After Phase 4 | | | | |
