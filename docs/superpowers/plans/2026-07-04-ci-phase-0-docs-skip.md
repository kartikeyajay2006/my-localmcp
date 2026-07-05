# CI Phase 0 — docs/meta skip + dedupe PR runs — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make docs/meta-only PRs skip the macOS+Windows test suite (closing #14) and stop every PR from running the whole matrix twice — without breaking branch protection.

**Architecture:** Two edits to the single workflow `.github/workflows/setup-v2.yml`. (1) Scope the `push` trigger to `main` and add a `concurrency` group so a same-repo PR branch runs the matrix once (via `pull_request`) instead of twice (`push` + `pull_request`). (2) Add a `dorny/paths-filter` step *inside* the existing `verify` job and guard the test-running steps with an `if:` so that, when only docs/meta files changed, the job still runs and reports its required check green but skips the actual test steps. The job name (and therefore the required check contexts) is unchanged, so branch protection keeps working untouched — this is the deliberate alternative to workflow-level `paths-ignore`, which would leave required checks unreported and PRs unmergeable.

**Tech Stack:** GitHub Actions YAML; `dorny/paths-filter@v3` (third-party action, explicitly recommended in #14); existing `actions/checkout@v4` + `actions/setup-python@v5`.

## Global Constraints

- **`main` is merge-only:** every change needs a branch + PR + green CI; no direct push. (CLAUDE.md)
- **Merge strategy is "Create a merge commit" only** — no squash/rebase. (CLAUDE.md)
- **Required check contexts today are exactly** `macos-latest / Python 3.12` and `windows-latest / Python 3.12` (from the job `name: ${{ matrix.os }} / Python 3.12`). **Phase 0 must not rename or remove these** — the job must still run on every PR and report success. (Renaming/splitting is Phase 1, not this plan.)
- **PR/issue/commit titles:** `type(area): description`; scope optional; subject must not start with an uppercase letter; `chore` is an allowed type. (`.github/workflows/pr-title.yml`, `.github/CONTRIBUTING.md`)
- **This is a configuration-file change (workflow YAML).** Per superpowers:test-driven-development, config files are an explicit exception to red-green TDD. Verification here is (a) a local YAML parse sanity check and (b) **live CI observation** on real and throwaway PRs — documented as concrete acceptance steps, not unit tests.
- **Ship Phase 0 and Phase 0b in one PR** (per the design spec). The CI-speed design spec is committed as part of this PR (it is currently untracked).

---

### Task 1: Branch and land the design spec

**Files:**
- Create (commit the already-written, currently-untracked file): `docs/superpowers/specs/2026-07-04-ci-speed-design.md`

**Interfaces:**
- Consumes: nothing.
- Produces: the `chore/ci-docs-skip-issue-14` branch that Tasks 2–4 commit onto; the committed design spec that the PR body references.

- [ ] **Step 1: Create the branch off up-to-date main**

```bash
git checkout main
git pull --ff-only
git checkout -b chore/ci-docs-skip-issue-14
```

- [ ] **Step 2: Commit the design spec (doc only)**

The spec file already exists in the working tree (untracked). Commit just it.

```bash
git add docs/superpowers/specs/2026-07-04-ci-speed-design.md
git commit -m "docs(ci): add measure-gated CI-speed design spec"
```

- [ ] **Step 3: Verify the working tree no longer lists the spec as untracked**

Run: `git status --short docs/superpowers/specs/2026-07-04-ci-speed-design.md`
Expected: no output (file is now tracked/committed).

---

### Task 2: Dedupe push/PR runs (Phase 0b)

**Files:**
- Modify: `.github/workflows/setup-v2.yml:3-5` (the `on:` block) and add a top-level `concurrency:` block after it.

**Interfaces:**
- Consumes: the branch from Task 1.
- Produces: a workflow that runs once per PR (via `pull_request`) and once per merge to `main` (via `push`), cancelling superseded in-progress runs.

- [ ] **Step 1: Replace the trigger block and add concurrency**

Open `.github/workflows/setup-v2.yml`. Replace lines 3–5, currently:

```yaml
on:
  push:
  pull_request:
```

with:

```yaml
on:
  push:
    branches: [main]
  pull_request:

concurrency:
  # One in-flight run per ref. A same-repo PR branch now runs the matrix once
  # (pull_request) instead of twice (push + pull_request); pushing a new commit
  # to a PR cancels the prior run. main still gets a post-merge run via push.
  group: ${{ github.workflow }}-${{ github.ref }}
  cancel-in-progress: true
```

- [ ] **Step 2: Local YAML parse sanity check**

macOS ships Ruby, which parses YAML without any install.

Run: `ruby -ryaml -e 'YAML.load_file(".github/workflows/setup-v2.yml"); puts "yaml ok"'`
Expected: `yaml ok` (no parse error). If Ruby is unavailable, use `python3 -m pip install --quiet pyyaml && python3 -c "import yaml; yaml.safe_load(open('.github/workflows/setup-v2.yml')); print('yaml ok')"`.

- [ ] **Step 3: Commit**

```bash
git add .github/workflows/setup-v2.yml
git commit -m "chore(ci): run the matrix once per PR and cancel superseded runs"
```

---

### Task 3: Skip test steps on docs/meta-only changes (Phase 0, closes #14)

**Files:**
- Modify: `.github/workflows/setup-v2.yml` — add a `dorny/paths-filter` step after `actions/checkout`, and add an `if:` guard to each of the four test-running steps (`Install test dependencies`, `Fast tests`, `Native lifecycle`, `Compile`).

**Interfaces:**
- Consumes: the workflow from Task 2.
- Produces: a `verify` job that always runs (reports its required check) but executes the four test steps only when a non-docs file changed, or when the event is a `push` to `main`.

- [ ] **Step 1: Add the paths-filter step immediately after checkout**

In the `steps:` list, the first step is `- uses: actions/checkout@v4`. Insert this **directly after** it (before `actions/setup-python`):

```yaml
      - name: Detect non-docs changes
        # Runs INSIDE the job (not a workflow-level paths-ignore) so the required
        # check contexts still report success on docs-only PRs -- a skipped
        # workflow never reports them and would wedge branch protection (#14).
        # predicate-quantifier 'every' + all-negation patterns means: a changed
        # file counts as "code" only if it matches NONE of the doc/meta globs, and
        # the `code` output is true when at least one such file changed.
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
```

- [ ] **Step 2: Guard each of the four test steps**

Add the same `if:` line to each test step's mapping (as the first key under the step name). On `push` to `main`, always run (main stays trustworthy and avoids any push-diff edge cases in the filter); on a PR, run only when code changed. The four steps become:

```yaml
      - name: Install test dependencies
        if: github.event_name == 'push' || steps.changes.outputs.code == 'true'
        run: python -m pip install -e ".[test]"
      - name: Fast tests
        if: github.event_name == 'push' || steps.changes.outputs.code == 'true'
        # -n auto parallelizes across the runner's CPU cores (~5x faster on
        # Windows). Only the fast suite is parallelized; the native-lifecycle
        # tests below build real venvs and manipulate real process trees, so
        # they stay serial to avoid cross-worker collisions.
        run: python -m pytest -q -m "not slow" -n auto
      - name: Native lifecycle
        if: github.event_name == 'push' || steps.changes.outputs.code == 'true'
        run: python -m pytest -q -m slow ${{ matrix.lifecycle }}
      - name: Compile
        if: github.event_name == 'push' || steps.changes.outputs.code == 'true'
        run: python -m compileall -q neo_localmcp setup.py
```

Note: `actions/setup-python` is intentionally **not** guarded — it is fast and keeping it unconditional preserves the pip cache warmth for the next real run.

- [ ] **Step 3: Local YAML parse sanity check**

Run: `ruby -ryaml -e 'YAML.load_file(".github/workflows/setup-v2.yml"); puts "yaml ok"'`
Expected: `yaml ok`.

- [ ] **Step 4: Eyeball the guarded step count**

Run: `grep -c "steps.changes.outputs.code == 'true'" .github/workflows/setup-v2.yml`
Expected: `4` (exactly the four test steps guarded; the filter step itself does not contain this string).

- [ ] **Step 5: Commit**

```bash
git add .github/workflows/setup-v2.yml
git commit -m "chore(ci): skip test steps on docs/meta-only PRs via in-job paths-filter"
```

---

### Task 4: Open the PR and verify live CI behavior (acceptance)

**Files:** none (verification + PR).

**Interfaces:**
- Consumes: all commits from Tasks 1–3.
- Produces: an open PR with green CI; documented evidence that (a) the skip path works on a docs-only PR, (b) the run path still works, (c) no double-run, (d) branch protection still enforces.

- [ ] **Step 1: Push the branch**

```bash
git push -u origin chore/ci-docs-skip-issue-14
```

- [ ] **Step 2: Open the PR**

```bash
gh pr create --base main \
  --title "chore(ci): skip tests on docs/meta-only PRs and dedupe push/PR runs" \
  --body "Closes #14. Phase 0 + 0b of the CI-speed design (docs/superpowers/specs/2026-07-04-ci-speed-design.md). Adds an in-job dorny/paths-filter so docs/meta-only PRs skip the test steps while the required checks still report green, and scopes triggers + adds a concurrency group so each PR runs the matrix once."
```

- [ ] **Step 3: Confirm the PR itself RUNS tests (run-path proof)**

This PR changes `.github/workflows/**`, which is NOT a doc/meta path, so `code` is true and tests must execute.

Run: `gh pr checks <PR#>` (repeat until complete)
Expected: `macos-latest / Python 3.12` and `windows-latest / Python 3.12` both **pass**, and their logs show the `Fast tests` step actually ran (not skipped). Also confirm each context appears **once** (no duplicate `push` run), proving Phase 0b.

- [ ] **Step 4: Prove the SKIP path on a throwaway docs-only PR**

The skip only exercises when a docs-only change hits the new workflow. Create a disposable PR branched off this one:

```bash
git checkout -b chore/ci-docs-skip-selftest
printf '\n<!-- ci docs-skip self-test, safe to revert -->\n' >> PROJECT_NOTES.md
git add PROJECT_NOTES.md
git commit -m "chore: ci docs-skip self-test (throwaway)"
git push -u origin chore/ci-docs-skip-selftest
gh pr create --base chore/ci-docs-skip-issue-14 \
  --title "chore: ci docs-skip self-test" \
  --body "Throwaway: verifies docs-only PRs skip the suite. Do not merge."
```

Run: `gh pr checks <selftest-PR#>`
Expected: `macos-latest / Python 3.12` and `windows-latest / Python 3.12` both report **pass within seconds**, and their logs show `Fast tests` / `Native lifecycle` / `Compile` as **skipped**. This is the #14 acceptance criterion: required checks green without executing the suite.

- [ ] **Step 5: Close the throwaway and clean up**

```bash
gh pr close <selftest-PR#> --delete-branch
git checkout chore/ci-docs-skip-issue-14
```

- [ ] **Step 6: Confirm branch protection still enforces**

Run: `gh api repos/NeelAPatel/neo-localmcp/branches/main/protection --jq '.required_status_checks.contexts'`
Expected: still lists `macos-latest / Python 3.12` and `windows-latest / Python 3.12` — unchanged by Phase 0, because the job (and thus its check names) is untouched. No branch-protection edit is required for Phase 0. (Phase 1 is where names change and a `ci-gate` context is introduced.)

- [ ] **Step 7: Report status and hand off for merge**

Summarize the evidence from Steps 3–4 (run-path green + skip-path fast-green) in a PR comment or to the user. Do **not** self-merge — merging is the maintainer's action per repo policy.

---

## Self-Review

**Spec coverage (Phase 0 + 0b sections of the design spec):**
- Phase 0 "skip test steps on docs/meta-only PRs via in-job `dorny/paths-filter`, avoid the `paths-ignore` branch-protection trap" → Task 3 + Task 4 Steps 3–4, 6. ✅
- Phase 0b "kill duplicate `push`+`pull_request` runs via trigger scoping + `concurrency`" → Task 2 + Task 4 Step 3. ✅
- "Fold Phase 0b into the Phase 0 PR" → single branch/PR across Tasks 1–4. ✅
- "Commit the (untracked) design spec with the CI branch" → Task 1. ✅
- Required-check names unchanged in Phase 0 → Global Constraints + Task 4 Step 6. ✅

**Placeholder scan:** No `TBD`/`TODO`/"add error handling"/"similar to Task N". `<PR#>`/`<selftest-PR#>` are runtime-substituted identifiers, not content placeholders. ✅

**Type/name consistency:** the filter step `id: changes` and its output `steps.changes.outputs.code` are used identically in Task 3 Step 1 (definition) and the four guards in Step 2 and the grep in Step 4. Branch name `chore/ci-docs-skip-issue-14` is identical across Tasks 1, 4. ✅

**Known edge (documented, not a gap):** `dorny/paths-filter` uses picomatch with dotfile matching enabled, so `**/*.md` covers `.github/*.md`; if a future docs path under a dot-directory is ever misclassified as "code," the only effect is that tests run unnecessarily (safe, never under-tests). The `push`-always-runs guard sidesteps push-event diff edge cases entirely.
