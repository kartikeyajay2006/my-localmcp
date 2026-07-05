# CI Phase 1 — parallel fast/slow split + `ci-gate` required check — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Split the single per-OS `verify` job (fast tests → slow lifecycle, sequential) into two parallel jobs per OS so wall-clock per OS drops from `fast + slow` to `max(fast, slow)`, and introduce one aggregating `ci-gate` job as the *sole* required status check so future job changes never break branch protection.

**Architecture:** `.github/workflows/setup-v2.yml` gains three jobs replacing the one `verify` job: `fast` (fast suite + compile, matrix over both OSes), `slow` (native lifecycle, matrix over both OSes), and `ci-gate` (runs on ubuntu, `needs: [fast, slow]`, `if: always()`, fails unless both aggregate results are `success`). The Phase 0 mechanisms are preserved verbatim in `fast` and `slow`: the `dorny/paths-filter` step and the `if: github.event_name == 'push' || steps.changes.outputs.code == 'true'` guards, so docs-only PRs still skip test steps while the jobs (and thus `ci-gate`) still report green. Branch protection is then repointed from the two per-OS contexts to the single `ci-gate` context.

**Tech Stack:** GitHub Actions YAML; `dorny/paths-filter@v3`; GitHub branch-protection REST API via `gh api`.

## Global Constraints

- **`main` is merge-only:** branch + PR + green CI required; merge-commit strategy only. (CLAUDE.md)
- **`enforce_admins: true`** on `main` — protection applies even to the admin; there is no direct-push escape hatch. (verified via API 2026-07-04)
- **Current required status checks (verified 2026-07-04):** `strict: true`, contexts `["macos-latest / Python 3.12", "windows-latest / Python 3.12", "Validate PR title"]`. **After Phase 1 they become** `["ci-gate", "Validate PR title"]` — the per-OS `Python 3.12` contexts cease to exist when the `verify` job is renamed, so they MUST be removed from the required list or every PR (including this one) wedges forever.
- **Preserve all other protection settings** (`enforce_admins`, `required_pull_request_reviews`, `strict: true`) — use the surgical `.../protection/required_status_checks` endpoint, which touches only the status-check list.
- **Keep Phase 0 behavior intact:** docs/meta-only PRs still skip test steps; `push` to `main` always runs; no `push`/`pull_request` double-run.
- **PR/commit titles:** `type(area): description`, scope optional, subject lowercase-initial, `chore` allowed. (`.github/workflows/pr-title.yml`)
- **Config-file change:** workflow YAML is the TDD config-file exception. Verification is a local YAML parse plus **live CI observation** on the PR and throwaway PRs, documented as concrete acceptance steps.

---

### Task 1: Branch and record the CI work in PROJECT_NOTES

**Files:**
- Modify: `PROJECT_NOTES.md:1-3` (insert a new dated entry after the `# Project Notes` header).

**Interfaces:**
- Consumes: nothing.
- Produces: the `perf/ci-speed-phase-1` branch; a PROJECT_NOTES entry covering the CI-speed initiative through Phase 1.

- [ ] **Step 1: Create the branch off up-to-date main**

```bash
git checkout main
git pull --ff-only
git checkout -b perf/ci-speed-phase-1
```

- [ ] **Step 2: Add the PROJECT_NOTES entry**

Read the current top of the file first: `sed -n '1,4p' PROJECT_NOTES.md` (confirm line 1 is `# Project Notes` and line 3 begins the latest `## 2026-07-04 (N)` entry). Insert a new entry directly after the `# Project Notes` header line. The new entry text:

```markdown
## 2026-07-04 (5)

- **CI-speed initiative, Phases 0-1 (spec: `docs/superpowers/specs/2026-07-04-ci-speed-design.md`).** Phase 0 (#14, merged): docs/meta-only PRs now skip the macOS+Windows test steps via an in-job `dorny/paths-filter` (job still runs so required checks report green — a workflow-level `paths-ignore` would leave them unreported and wedge branch protection); verified live green in ~12s with test steps skipped. Phase 0b (merged): scoped `push` to `main` + added a `concurrency` group so a PR runs the matrix once instead of twice (`push`+`pull_request`). Phase 1 (this change): split the single per-OS `verify` job into parallel `fast` (fast suite + compile) and `slow` (native lifecycle) jobs so per-OS wall-clock is `max(fast, slow)` not `fast + slow`, gated by a single `ci-gate` job (`needs: [fast, slow]`, `if: always()`, fails unless both aggregate results are `success`). Branch protection was repointed from the two per-OS `Python 3.12` contexts to the sole `ci-gate` context, so future job-layout changes no longer touch protection.
```

- [ ] **Step 3: Commit**

```bash
git add PROJECT_NOTES.md
git commit -m "docs: record CI-speed Phases 0-1 in PROJECT_NOTES"
```

---

### Task 2: Split `verify` into `fast` + `slow` + `ci-gate`

**Files:**
- Modify: `.github/workflows/setup-v2.yml` — replace the entire `jobs:` block (currently the single `verify` job, lines 15–66) with three jobs.

**Interfaces:**
- Consumes: the branch from Task 1.
- Produces: check contexts `macos-latest / fast`, `windows-latest / fast`, `macos-latest / lifecycle`, `windows-latest / lifecycle`, and `ci-gate`. `ci-gate` is green iff both the `fast` and `slow` matrix aggregates are `success`.

- [ ] **Step 1: Replace the `jobs:` block**

Everything above `jobs:` (the `name`, `on`, and `concurrency` blocks — lines 1–14) stays unchanged. Replace lines 15–66 (the whole `jobs:` section) with:

```yaml
jobs:
  fast:
    name: ${{ matrix.os }} / fast
    runs-on: ${{ matrix.os }}
    strategy:
      fail-fast: false
      matrix:
        os: [macos-latest, windows-latest]
    steps:
      - uses: actions/checkout@v4
      - name: Detect non-docs changes
        # See Phase 0: in-job filter keeps required checks reporting on docs-only
        # PRs. Duplicated in the slow job because each job runs on a fresh runner;
        # de-duping two jobs via a composite action is more machinery than warranted.
        uses: dorny/paths-filter@v3
        id: changes
        with:
          predicate-quantifier: 'every'
          filters: |
            code:
              - '!**/*.md'
              - '!LICENSE'
              - '!.gitignore'
              - '!.github/ISSUE_TEMPLATE/**'
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
          cache: pip
      - name: Install test dependencies
        if: github.event_name == 'push' || steps.changes.outputs.code == 'true'
        run: python -m pip install -e ".[test]"
      - name: Fast tests
        if: github.event_name == 'push' || steps.changes.outputs.code == 'true'
        # -n auto parallelizes across the runner's CPU cores (~5x faster on
        # Windows). Only the fast suite is parallelized; the native-lifecycle
        # tests in the slow job build real venvs and manipulate real process
        # trees, so they stay serial to avoid cross-worker collisions.
        run: python -m pytest -q -m "not slow" -n auto
      - name: Compile
        if: github.event_name == 'push' || steps.changes.outputs.code == 'true'
        run: python -m compileall -q neo_localmcp setup.py

  slow:
    name: ${{ matrix.os }} / lifecycle
    runs-on: ${{ matrix.os }}
    strategy:
      fail-fast: false
      matrix:
        include:
          - os: macos-latest
            lifecycle: tests/installer/test_macos_lifecycle.py
          - os: windows-latest
            lifecycle: tests/installer/test_windows_lifecycle.py
    steps:
      - uses: actions/checkout@v4
      - name: Detect non-docs changes
        uses: dorny/paths-filter@v3
        id: changes
        with:
          predicate-quantifier: 'every'
          filters: |
            code:
              - '!**/*.md'
              - '!LICENSE'
              - '!.gitignore'
              - '!.github/ISSUE_TEMPLATE/**'
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
          cache: pip
      - name: Install test dependencies
        if: github.event_name == 'push' || steps.changes.outputs.code == 'true'
        run: python -m pip install -e ".[test]"
      - name: Native lifecycle
        if: github.event_name == 'push' || steps.changes.outputs.code == 'true'
        run: python -m pytest -q -m slow ${{ matrix.lifecycle }}

  ci-gate:
    # Single required status check. Aggregates the matrix jobs so branch
    # protection never has to change when the job layout changes. `if: always()`
    # makes it run even when a dependency fails (a failed `needs` would otherwise
    # skip it, and a skipped required check does not satisfy protection).
    name: ci-gate
    needs: [fast, slow]
    if: always()
    runs-on: ubuntu-latest
    steps:
      - name: Require fast and slow to have succeeded
        run: |
          echo "fast=${{ needs.fast.result }} slow=${{ needs.slow.result }}"
          if [ "${{ needs.fast.result }}" != "success" ] || [ "${{ needs.slow.result }}" != "success" ]; then
            echo "::error::A required CI job did not succeed (fast=${{ needs.fast.result }}, slow=${{ needs.slow.result }})."
            exit 1
          fi
          echo "All required CI jobs succeeded."
```

- [ ] **Step 2: Local YAML parse sanity check**

Run: `ruby -ryaml -e 'YAML.load_file(".github/workflows/setup-v2.yml"); puts "yaml ok"'`
Expected: `yaml ok`.

- [ ] **Step 3: Structural sanity checks**

Run each and confirm the exact count:
- `grep -c "if: github.event_name == 'push'" .github/workflows/setup-v2.yml` → Expected `5` (fast job: install deps, fast tests, compile = 3; slow job: install deps, native lifecycle = 2).
- `grep -c "name: ci-gate" .github/workflows/setup-v2.yml` → Expected `1`.
- `grep -c "needs: \[fast, slow\]" .github/workflows/setup-v2.yml` → Expected `1`.
- `grep -c "dorny/paths-filter@v3" .github/workflows/setup-v2.yml` → Expected `2` (one per test-bearing job).

- [ ] **Step 4: Commit**

```bash
git add .github/workflows/setup-v2.yml
git commit -m "perf(ci): split fast/slow into parallel jobs behind a single ci-gate"
```

---

### Task 3: Open the PR and verify the new CI graph (before touching protection)

**Files:** none (PR + live verification).

**Interfaces:**
- Consumes: commits from Tasks 1–2.
- Produces: an open PR whose CI proves (a) `ci-gate` reports green when all jobs pass, (b) the docs-skip still works under the split, (c) `ci-gate` reports RED when a job fails. Branch protection is **not** touched in this task.

- [ ] **Step 1: Push and open the PR (draft)**

```bash
git push -u origin perf/ci-speed-phase-1
gh pr create --base main --draft \
  --title "perf(ci): parallelize fast/slow jobs behind a single ci-gate check" \
  --body "Phase 1 of the CI-speed initiative (docs/superpowers/specs/2026-07-04-ci-speed-design.md). Splits the per-OS verify job into parallel fast + slow jobs (wall-clock max not sum) and adds a ci-gate aggregator as the sole required status check. Branch protection will be repointed to ci-gate as part of merging (see plan Task 4). Keeps Phase 0 docs-skip + dedupe intact."
```

- [ ] **Step 2: Confirm the new contexts all report and `ci-gate` is green (happy path)**

This PR touches `.github/workflows/**` → `code=true` → tests run.

Run (repeat until complete): `gh pr checks <PR#>`
Expected: `macos-latest / fast`, `windows-latest / fast`, `macos-latest / lifecycle`, `windows-latest / lifecycle`, `ci-gate`, and `Validate PR title` all **pass**. Note that `fast` contexts finish well before `lifecycle` contexts — confirming the parallelism (previously fast blocked slow within one job).

- [ ] **Step 3: Prove the docs-skip still works under the split (throwaway docs-only PR)**

```bash
git checkout -b perf/ci-phase1-docs-selftest
printf '\n<!-- ci phase1 docs-skip self-test, safe to revert -->\n' >> PROJECT_NOTES.md
git add PROJECT_NOTES.md
git commit -m "chore: ci phase1 docs-skip self-test (throwaway)"
git push -u origin perf/ci-phase1-docs-selftest
gh pr create --base perf/ci-speed-phase-1 --draft \
  --title "chore: ci phase1 docs-skip self-test" \
  --body "Throwaway: docs-only under the split must still go green fast with test steps skipped and ci-gate green. Do not merge."
git checkout perf/ci-speed-phase-1
```

Run: `gh pr checks <selftest-PR#>`
Expected: all of `macos-latest / fast`, `windows-latest / fast`, `macos-latest / lifecycle`, `windows-latest / lifecycle`, `ci-gate` **pass within seconds**; spot-check one job's steps show the test steps skipped (`gh run view --job <id>` → `Install test dependencies`/`Fast tests`/`Native lifecycle`/`Compile` marked with `-`). Then close it:

```bash
gh pr close <selftest-PR#> --delete-branch
git branch -D perf/ci-phase1-docs-selftest
```

- [ ] **Step 4: Prove `ci-gate` FAILS when a job fails (the hard gate)**

A required aggregator that never fails is worse than useless. Inject a real failure on a throwaway and confirm `ci-gate` goes red.

```bash
git checkout -b perf/ci-phase1-fail-selftest
# a guaranteed-failing fast-suite test
cat > tests/test_cigate_selftest.py <<'PY'
def test_cigate_must_fail_on_purpose():
    assert False, "intentional failure to verify ci-gate reports red"
PY
git add tests/test_cigate_selftest.py
git commit -m "chore: ci-gate failure self-test (throwaway)"
git push -u origin perf/ci-phase1-fail-selftest
gh pr create --base perf/ci-speed-phase-1 --draft \
  --title "chore: ci-gate failure self-test" \
  --body "Throwaway: a failing fast test must make the fast job fail and ci-gate report RED. Do not merge."
git checkout perf/ci-speed-phase-1
```

Run: `gh pr checks <fail-selftest-PR#>`
Expected: `macos-latest / fast` and `windows-latest / fast` **fail**; `ci-gate` **fails** (red) with the log line `A required CI job did not succeed (fast=failure, ...)`. This confirms the gate has teeth. Then close and clean up:

```bash
gh pr close <fail-selftest-PR#> --delete-branch
git branch -D perf/ci-phase1-fail-selftest
```

- [ ] **Step 5: Mark the Phase 1 PR ready for review**

```bash
gh pr ready <PR#>
```

Do not proceed to Task 4 until Steps 2 and 4 both passed (happy-path green ci-gate AND failure-path red ci-gate). If either did not behave as expected, STOP and report — do not touch branch protection with an unproven gate.

---

### Task 4: Repoint branch protection to `ci-gate`, then merge

**Files:** none (branch-protection API + merge).

**Interfaces:**
- Consumes: a green, proven Phase 1 PR from Task 3.
- Produces: `main` branch protection requiring `["ci-gate", "Validate PR title"]`; the Phase 1 PR merged; a verified post-merge main.

- [ ] **Step 1: Record the current protection for rollback**

Run: `gh api repos/NeelAPatel/neo-localmcp/branches/main/protection/required_status_checks --jq '{strict, contexts}'`
Expected (record this exact value for rollback): `{"strict":true,"contexts":["macos-latest / Python 3.12","windows-latest / Python 3.12","Validate PR title"]}`

- [ ] **Step 2: Repoint required status checks to `ci-gate` (surgical endpoint)**

This endpoint updates ONLY the status-check list; `enforce_admins`, `required_pull_request_reviews`, and `strict` are untouched.

```bash
gh api --method PATCH \
  repos/NeelAPatel/neo-localmcp/branches/main/protection/required_status_checks \
  --input - <<'JSON'
{"strict": true, "contexts": ["ci-gate", "Validate PR title"]}
JSON
```

- [ ] **Step 3: Verify the swap**

Run: `gh api repos/NeelAPatel/neo-localmcp/branches/main/protection/required_status_checks --jq '.contexts'`
Expected: `["ci-gate","Validate PR title"]` (the two per-OS `Python 3.12` contexts are gone).

Also confirm the rest of protection survived:
Run: `gh api repos/NeelAPatel/neo-localmcp/branches/main/protection --jq '{admins: .enforce_admins.enabled, reviews: (.required_pull_request_reviews != null)}'`
Expected: `{"admins":true,"reviews":true}`.

- [ ] **Step 4: Confirm the Phase 1 PR is now mergeable**

Run: `gh pr checks <PR#>` then `gh pr view <PR#> --json mergeable,mergeStateStatus --jq '{mergeable, mergeStateStatus}'`
Expected: `ci-gate` and `Validate PR title` green; `mergeable: "MERGEABLE"`. The old per-OS contexts are no longer required, so their absence from the required set no longer blocks.

- [ ] **Step 5: Merge and sync**

```bash
gh pr merge <PR#> --merge --delete-branch
git checkout main
git pull --ff-only
```

- [ ] **Step 6: Post-merge sanity — main's own push run uses ci-gate**

The merge fires a `push` run on `main` (Phase 0b keeps exactly one). Confirm it produces `ci-gate` green:

Run: `gh run list --branch main --workflow "setup-v2 macOS and Windows" --limit 1` then `gh run view <run-id>`
Expected: the run contains `fast`/`slow` matrix jobs and a `ci-gate` job, all green.

- [ ] **Step 7: Rollback reference (only if the swap wedged the repo)**

If, at any point, PRs become unmergeable because `ci-gate` is required but the workflow on `main` does not produce it (should not happen post-merge, but if the swap was done out of order), restore the old required set:

```bash
gh api --method PATCH \
  repos/NeelAPatel/neo-localmcp/branches/main/protection/required_status_checks \
  --input - <<'JSON'
{"strict": true, "contexts": ["macos-latest / Python 3.12", "windows-latest / Python 3.12", "Validate PR title"]}
JSON
```

---

## Self-Review

**Spec coverage (Phase 1 section of the design spec):**
- "Split each OS job into parallel fast + slow" → Task 2 (`fast`, `slow` jobs). ✅
- "single aggregating `ci-gate` job that `needs:` all split jobs and is the only required context; make branch protection require `ci-gate`" → Task 2 (`ci-gate`) + Task 4 (protection swap). ✅
- "Hard gate: confirm protection enforces `ci-gate` and that `ci-gate` correctly fails when any upstream job fails, before relying on it" → Task 3 Step 4 (failure injection) + Task 4 Step 3. ✅
- "The `dorny/paths-filter` guard from Phase 0 applies to both" → Task 2 both jobs carry the filter + guards; Task 3 Step 3 verifies. ✅
- "duplicates the cached pip install across two jobs — cheap" → acknowledged in Task 2 Step 1 comment. ✅
- PROJECT_NOTES loose end folded in → Task 1. ✅

**Placeholder scan:** No `TBD`/`TODO`/"handle edge cases". `<PR#>`, `<selftest-PR#>`, `<fail-selftest-PR#>`, `<id>`, `<run-id>` are runtime identifiers, not content gaps. Task 2 Step 3's first line self-corrects to "read the numbers rather than guessing" and gives exact expected counts on the following lines. ✅

**Type/name consistency:** job ids `fast`/`slow`/`ci-gate` and the `needs.fast.result`/`needs.slow.result` references match across Task 2 and the verification expectations in Tasks 3–4. Branch name `perf/ci-speed-phase-1` is identical across Tasks 1, 3, 4. Required-context target `["ci-gate", "Validate PR title"]` is identical in Task 4 Steps 2, 3, and the Global Constraints. ✅

**Ordering safety (the delicate part):** protection is swapped (Task 4) only AFTER the PR's CI has produced a green `ci-gate` and a proven-red failure path (Task 3). The PR's own CI uses the PR-branch workflow, so `ci-gate` exists on the PR before the swap; the swap makes the PR mergeable; the merge puts the ci-gate-producing workflow on `main` so all future PRs report it. The only exposure window (protection requires `ci-gate` while the PR is not yet merged) affects hypothetical *other* PRs branched off pre-Phase-1 `main`; none are open. Rollback is Task 4 Step 7.

## Non-goals / deferred

- **Node 20 → Node 24 action version bump** (`checkout`, `setup-python`, `dorny` deprecation warning): deferred to its own small `chore(ci):` PR so the risky branch-protection change stays isolated and reviewable. Not in this plan.
- **Phases 2–4** (wheelhouse, prebuilt-index fixture + stdio-flake fix, cycle trimming): separate plans.
- **DRY-ing the duplicated `dorny`/setup steps via a composite action or reusable workflow:** deliberately not done — two jobs don't justify the indirection (noted in Task 2).
