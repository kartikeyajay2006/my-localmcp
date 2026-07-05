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

**Outcome (2026-07-05): premise invalidated by closer reading; not implemented.**
The original mechanism assumed one large shared corpus being rebuilt
repeatedly. Reading the actual test bodies (`_seed_repo` helpers in
`test_retrieval_memory.py` and siblings) shows each test builds its **own
small, test-specific repo** with content unique to that test — there is no
common corpus to share. A session-scoped shared-DB-copy fixture would either
be a no-op or, worse, leak one test's seeded content into another that expects
different data — a correctness risk for an unproven win. Retrieval-file
aggregate cost (30.83s across `test_retrieval_memory.py` + `test_context.py` +
`test_repo_memory.py` + `test_markdown_headings.py` + `test_schema_migration.py`,
measured 2026-07-05) is real but is dominated by per-test fixture/DB-creation
overhead and intentional in-test repetition loops, not a shareable rebuild.
Deliberately not pursued; a correct fix (e.g. caching only the empty SQLite
*schema* template, still indexing real per-test content) is a separate,
narrower idea not designed here.

**Separately discovered, unrelated to the above (spun off, not fixed here):**
`tests/installer/test_verification.py`'s `_base_kwargs()` never injects
`ollama_status_fn`, so ~14 tests call the **real** `ollama_client.status()` —
a live network probe against whatever `~/.neo-localmcp/config.yaml` exists on
the running machine. Confirmed live: a single such test takes 3.08s in
isolation (config.py's `connect_timeout_seconds=3` against an unreachable
default host); ~11-14 of these account for roughly a third of the fast suite's
serial runtime. This is a test-isolation bug, not a Phase 3 concern — tracked
as its own fix, out of scope here per this repo's one-fix-per-PR convention.

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

**Outcome (2026-07-05): measured, deliberately skipped.** Re-measured on `main`
post Phase 2 + the stdio-isolation fix (#51): Windows `Native lifecycle` still
the dominant cost at 4m29s (run 28753119082), confirming the slow suite
remains the critical path. However, each of `test_full_windows_lifecycle_via_setup`'s
~6 install/reinstall cycles was already confirmed (during Phase 2 planning) to
assert a **distinct** invariant not covered elsewhere: live-process kill on
reinstall, unrelated-process survival, cancellation refusal, broken-venv
recovery, interrupted-metadata recovery, and clean-vs-preserved-data handling.
Cutting any would trade real coverage for marginal time savings, which
CLAUDE.md explicitly warns against absent a concrete correctness problem to
point at. Phase 2 already captured the safe, environment-level win (the actual
cost these cycles used to pay for network pip resolution); the residual cost
is now genuine per-cycle test work, not waste. Skipping per the spec's own
"valid outcome" allowance.

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
| Baseline | — | Windows lifecycle job 5m29s, `Native lifecycle` step 4m47s (run 28724512399) | fast+slow sequential per OS | local: fast 39.0s serial / 11.6s `-n auto` |
| After Phase 0 | — | — | — | docs/meta PRs green in ~12s; verified skip-path + run-path live |
| After Phase 1 | Windows `fast` job ~2m39-3m2s | Windows `lifecycle` job ~4m29-5m42s | `max(fast,slow)` per OS, jobs run in parallel | `ci-gate` proven green-on-pass and red-on-injected-failure (PR #45) |
| After Phase 2 | unchanged | Windows `Native lifecycle` step 4m47s → 3m41s (~23%); job 5m29s → ~4m29s | ~1 min off Windows critical path | zero source change; offline wheelhouse, PR #49 |
| After Phase 3 | fast suite serial ~86s (test_verification.py's live-Ollama-call bug now the largest single item, ~33s — spun off separately); retrieval files 30.83s | stdio flake fixed (issue #50/PR #51): `test_repo_tools_respond_over_real_stdio` isolated from xdist via a `serial` marker, no longer races the worker pool | — | index-fixture premise invalidated (see Phase 3 outcome); not implemented |
| After Phase 4 | — | Windows `Native lifecycle` confirmed still 4m29s dominant (run 28753119082); deliberately not trimmed | — | measured, coverage-sensitive trim skipped per spec's own allowance |
