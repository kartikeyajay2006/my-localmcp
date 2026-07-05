# CI Phase 2 — offline wheelhouse for lifecycle installs — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the `slow` (lifecycle) CI job's ~6 real `venv`+`pip install-from-source` cycles resolve offline from a prebuilt wheelhouse, cutting the Windows lifecycle step (the commit→merge critical path) without changing any production code or what the tests assert.

**Architecture:** Workflow-only change to `.github/workflows/setup-v2.yml`'s `slow` job. Add one step that prebuilds every wheel the in-test installs need (runtime deps + build backend + pip) with the network, then set `PIP_NO_INDEX=1` + `PIP_FIND_LINKS=<wheelhouse>` as **step-level env on the lifecycle step only**. The installer's `subprocess.run` calls inherit these env vars (no `env=` override in `runtime.py`; the lifecycle test's `_setup` helper spreads `**os.environ`), so every in-test `python -m venv`/`pip install` goes offline. No source change.

**Tech Stack:** GitHub Actions YAML; `pip download`; pip's native `PIP_NO_INDEX`/`PIP_FIND_LINKS` env vars.

## Global Constraints

- **`main` is merge-only:** branch + PR + green `ci-gate` required; merge-commit strategy only. (CLAUDE.md)
- **Deterministic retrieval / lifecycle behavior must not change** — this phase touches *where wheels come from*, never test assertions or `runtime.py` command construction. (design spec, Phase 2)
- **Required status check is `ci-gate`** (+ `Validate PR title`) since Phase 1 — do not change job names or branch protection here.
- **Keep Phase 0/1 intact:** the `slow` job keeps its `dorny/paths-filter` step and the `if: github.event_name == 'push' || steps.changes.outputs.code == 'true'` guards; the new wheelhouse step carries the same guard.
- **Wheelhouse must be built on the same runner that consumes it** — platform/Python-version-specific wheels (psutil, pydantic-core, cryptography) must match the consuming interpreter (cp312, macOS/Windows).
- **Config-file change:** workflow YAML is the TDD config-file exception. Verification is a local YAML parse plus **live CI measurement** of the lifecycle step vs the recorded baseline.
- **Measured baseline (run 28724512399, `main`, 2026-07-04):** `windows-latest / lifecycle` job = 5m29s total; its **`Native lifecycle` step = 4m47s** (the target); `macos-latest / lifecycle` `Native lifecycle` ≈ 1m. Local spike (macOS, warm `cache: pip`): offline source-install 2.26s vs warm-network 4.88s (~2.2x) per install; wheelhouse = 41 wheels, zero sdists.

---

### Task 1: Branch and record the baseline

**Files:** none (branch + recorded numbers).

**Interfaces:**
- Consumes: nothing.
- Produces: the `perf/ci-speed-phase-2` branch; the baseline lifecycle-step timings to compare against in Task 3.

- [ ] **Step 1: Create the branch off up-to-date main**

```bash
git checkout main
git pull --ff-only
git checkout -b perf/ci-speed-phase-2
```

- [ ] **Step 2: Record the current lifecycle-step baseline**

Run:
```bash
gh run list --branch main --workflow "setup-v2 macOS and Windows" --event push --limit 1 --json databaseId --jq '.[0].databaseId'
```
Then, with that run id:
```bash
gh run view <run-id> --json jobs --jq '.jobs[] | select(.name|endswith("lifecycle")) | {name, started: .startedAt, completed: .completedAt, native: (.steps[]|select(.name=="Native lifecycle")|{s:.startedAt,c:.completedAt})}'
```
Expected: two lifecycle jobs; note each `Native lifecycle` step's start→complete duration. Baseline reference: Windows `Native lifecycle` ≈ 4m47s. Record the exact numbers to compare in Task 3 Step 4.

---

### Task 2: Add the offline wheelhouse to the `slow` job

**Files:**
- Modify: `.github/workflows/setup-v2.yml` — inside the `slow` job only: add a `Build offline wheelhouse for lifecycle installs` step after `Install test dependencies`, and add a `PIP_NO_INDEX`/`PIP_FIND_LINKS` `env:` block to the existing `Native lifecycle` step.

**Interfaces:**
- Consumes: the branch from Task 1.
- Produces: a `slow` job whose `Native lifecycle` step runs with `PIP_NO_INDEX=1` and `PIP_FIND_LINKS=${{ runner.temp }}/wheelhouse`, populated by the new step.

- [ ] **Step 1: Add the wheelhouse build step and env-guard the lifecycle step**

In `.github/workflows/setup-v2.yml`, locate the `slow:` job's steps. The current tail is:

```yaml
      - name: Install test dependencies
        if: github.event_name == 'push' || steps.changes.outputs.code == 'true'
        run: python -m pip install -e ".[test]"
      - name: Native lifecycle
        if: github.event_name == 'push' || steps.changes.outputs.code == 'true'
        run: python -m pytest -q -m slow ${{ matrix.lifecycle }}
```

Replace exactly that with:

```yaml
      - name: Install test dependencies
        if: github.event_name == 'push' || steps.changes.outputs.code == 'true'
        run: python -m pip install -e ".[test]"
      - name: Build offline wheelhouse for lifecycle installs
        # The lifecycle tests run setup.py install/reinstall ~6x, each doing a real
        # `python -m venv` + pip install of the runtime FROM SOURCE. Prebuild every
        # wheel those installs need -- runtime deps (mcp[cli], psutil), the build
        # backend (setuptools, wheel), and pip itself -- ONCE here, with the network.
        # The Native lifecycle step then runs with PIP_NO_INDEX + PIP_FIND_LINKS so
        # each in-test install resolves offline from this dir (wheel-unpacking, no
        # PyPI round-trip). The installer's subprocesses inherit the env vars; no
        # production code changes. Verified locally: 41 wheels, no sdists, offline
        # source-install of neo-localmcp succeeds (deps + build backend + pip-upgrade).
        if: github.event_name == 'push' || steps.changes.outputs.code == 'true'
        shell: bash
        run: python -m pip download -d "${{ runner.temp }}/wheelhouse" pip setuptools wheel "mcp[cli]" psutil
      - name: Native lifecycle
        if: github.event_name == 'push' || steps.changes.outputs.code == 'true'
        env:
          PIP_NO_INDEX: '1'
          PIP_FIND_LINKS: ${{ runner.temp }}/wheelhouse
        run: python -m pytest -q -m slow ${{ matrix.lifecycle }}
```

Notes for the implementer (do not add to the file):
- `${{ runner.temp }}` is used directly (not a job-level `env:`) because the `runner` context is unavailable in job-level `env`. It resolves per-OS (e.g. `C:\...\Temp` on Windows) and pip accepts native paths in `--find-links`/`PIP_FIND_LINKS`.
- `shell: bash` is set on the build step because Windows runners default to `pwsh`, and the `-d "..."` quoting is written for bash; bash ships on all GitHub runners.
- Only the `Native lifecycle` step is offline. `Install test dependencies` stays online (it needs pytest/xdist, which are not in the wheelhouse).

- [ ] **Step 2: Local YAML parse sanity check**

Run: `ruby -ryaml -e 'YAML.load_file(".github/workflows/setup-v2.yml"); puts "yaml ok"'`
Expected: `yaml ok`.

- [ ] **Step 3: Structural sanity checks**

- `grep -c "PIP_NO_INDEX" .github/workflows/setup-v2.yml` → Expected `1`.
- `grep -c "PIP_FIND_LINKS" .github/workflows/setup-v2.yml` → Expected `1`.
- `grep -c "pip download" .github/workflows/setup-v2.yml` → Expected `1`.
- `grep -c "if: github.event_name == 'push'" .github/workflows/setup-v2.yml` → Expected `6` (Phase 1 had 5; +1 for the new wheelhouse step).

- [ ] **Step 4: Commit**

```bash
git add .github/workflows/setup-v2.yml
git commit -m "perf(ci): install lifecycle runtime offline from a prebuilt wheelhouse"
```

---

### Task 3: Open the PR, verify green, and measure the win

**Files:** none (PR + measurement).

**Interfaces:**
- Consumes: the commit from Task 2.
- Produces: an open PR proving the lifecycle tests still pass fully offline, and a measured before/after of the Windows `Native lifecycle` step.

- [ ] **Step 1: Push and open the PR (labeled)**

```bash
git push -u origin perf/ci-speed-phase-2
gh pr create --base main \
  --title "perf(ci): install lifecycle runtime offline from a prebuilt wheelhouse" \
  --label "type:perf" \
  --body "Phase 2 of the CI-speed initiative (docs/superpowers/specs/2026-07-04-ci-speed-design.md). The slow/lifecycle job prebuilds a wheelhouse (runtime deps + build backend + pip) then runs the lifecycle tests with PIP_NO_INDEX + PIP_FIND_LINKS so their ~6 in-test venv+pip installs resolve offline. No production code change; installer subprocesses inherit the env. Measuring the Windows lifecycle step vs the ~4m47s baseline."
```
(Label at creation — per repo convention every PR carries its `type:`.)

- [ ] **Step 2: Confirm the lifecycle tests still PASS fully offline**

This PR touches `.github/workflows/**` → `code=true` → everything runs.

Run (repeat until complete): `gh pr checks <PR#>`
Expected: `ci-gate` and all `fast`/`lifecycle` contexts **pass**. A green `windows-latest / lifecycle` here is the correctness proof — the ~6 in-test installs succeeded with `PIP_NO_INDEX=1`, i.e. the wheelhouse was complete. If it fails with a pip resolution error, a needed wheel is missing from the `pip download` list — STOP and report (do not merge); the fix is adding the missing distribution to the download step.

- [ ] **Step 3: Confirm the wheelhouse step actually ran and the lifecycle step was offline**

Find the run and inspect the Windows lifecycle job's steps:
```bash
gh pr checks <PR#> --json name,link | python3 -c "import sys,json; [print(r['link']) for r in json.load(sys.stdin) if r['name']=='windows-latest / lifecycle']"
```
Then `gh run view <run-id> --json jobs --jq '.jobs[] | select(.name=="windows-latest / lifecycle") | [.steps[].name]'`
Expected: the step list includes `Build offline wheelhouse for lifecycle installs` (conclusion success) before `Native lifecycle`.

- [ ] **Step 4: Measure the win (Windows `Native lifecycle` step, after vs baseline)**

```bash
gh run view <run-id> --json jobs --jq '.jobs[] | select(.name=="windows-latest / lifecycle") | (.steps[]|select(.name=="Native lifecycle")|{s:.startedAt,c:.completedAt})'
```
Compute the start→complete duration and compare to the **4m47s** baseline from Task 1.
- If meaningfully lower (e.g. ≥ ~30s faster): success — record the delta in the PR body and proceed.
- If ~unchanged: the wheelhouse mechanism is correct (offline install proven in Step 2) but pip was not the dominant cost inside the lifecycle step. Record the measured delta honestly in the PR body and note that Phase 4 (cutting the *number* of build cycles) is the larger lever. This is an acceptable outcome — the change is still a correctness/robustness win (offline, no PyPI flakiness) at zero risk.

- [ ] **Step 5: Add a PROJECT_NOTES entry with the measured result**

Insert a new dated entry after the `# Project Notes` header in `PROJECT_NOTES.md` recording: what Phase 2 did, and the **measured** Windows `Native lifecycle` before/after numbers from Step 4 (use the real numbers, not an estimate). Commit:
```bash
git add PROJECT_NOTES.md
git commit -m "docs: record CI Phase 2 wheelhouse result in PROJECT_NOTES"
git push
```

- [ ] **Step 6: Report and hand off for merge**

Summarize the measured before/after and the green offline lifecycle run. Do not self-merge — merging is the maintainer's action.

---

## Self-Review

**Spec coverage (Phase 2 section of the design spec):**
- "build a wheelhouse (neo-localmcp deps + mcp[cli] + psutil), set PIP_NO_INDEX/PIP_FIND_LINKS" → Task 2 Step 1 (adds pip/setuptools/wheel too, required because neo-localmcp installs from source — proven necessary by the local spike). ✅
- "no production-code change; installer subprocesses inherit the env vars" → Architecture + confirmed via `runtime.py` `subprocess.run` (no `env=`) and the lifecycle `_setup` helper spreading `**os.environ`. ✅
- "wheelhouse must contain a pip wheel if a cycle upgrades pip under PIP_NO_INDEX" → covered (pip in the download list; spike ran the offline `pip install --upgrade pip` successfully). ✅
- "confirm venv creation cost separately — not addressed by this phase" → acknowledged in Global Constraints/Task 3 Step 4 (if unchanged, Phase 4 is the lever). ✅
- "measure after the phase" → Task 1 baseline + Task 3 Step 4 measurement + Step 5 recorded result. ✅

**Placeholder scan:** No `TBD`/`TODO`/"handle edge cases". `<PR#>`/`<run-id>` are runtime identifiers. The one conditional outcome (Task 3 Step 4) gives concrete instructions for both the win and the no-change branch. ✅

**Type/name consistency:** the wheelhouse path `${{ runner.temp }}/wheelhouse` is identical in the build step and the `PIP_FIND_LINKS` env. Step name `Build offline wheelhouse for lifecycle installs` and `Native lifecycle` match between Task 2 (definition) and Task 3 (verification greps). Branch `perf/ci-speed-phase-2` identical across tasks. Guard-count expectation (6) accounts for Phase 1's 5 + the new step. ✅

## Non-goals / deferred
- **Caching the wheelhouse across runs** (actions/cache) — `cache: pip` already makes `pip download` a cache hit; a separate wheelhouse cache is a later micro-optimization, not now.
- **Faster venv creation** (`virtualenv`/`uv`, `--without-pip`) — a source/dependency change; only revisit if Task 3 Step 4 shows venv creation dominates.
- **Reducing the number of build cycles** — Phase 4, separate plan.
- **The `fast` job's single `pip install -e .[test]`** — already cached and not on the critical path; left online.
