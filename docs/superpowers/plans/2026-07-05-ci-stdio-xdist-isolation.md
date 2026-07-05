# CI real-stdio xdist isolation — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stop the real MCP stdio integration test from intermittently failing when it competes with the xdist worker pool on loaded Windows CI runners.

**Architecture:** Introduce a narrowly defined `serial` pytest marker and apply it only to `test_repo_tools_respond_over_real_stdio`. Keep the fast suite parallel for all other tests, then run the serial subset in the same fast job without xdist; do not move the test into the lifecycle job or weaken its 5-second call assertions.

**Tech Stack:** pytest markers, pytest-xdist, GitHub Actions YAML.

## Global Constraints

- Preserve the real MCP server subprocess and all three tool calls.
- Preserve the existing 5-second timeout; the failure is resource contention, not an accepted slower contract.
- Keep the test in the fast CI job so it does not lengthen the lifecycle critical path.
- Do not touch production code.
- Preserve unrelated untracked workspace files.

---

### Task 1: Prove and configure serial isolation

**Files:**
- Modify: `tests/test_distribution.py`
- Modify: `pyproject.toml`
- Modify: `.github/workflows/setup-v2.yml`
- Test: `tests/test_distribution.py`

**Interfaces:**
- Consumes: pytest's registered marker configuration and `-m` expression selection.
- Produces: a `serial` marker whose tests are excluded from xdist and run in a separate non-xdist CI invocation.

- [ ] **Step 1: Add a structural regression test that initially fails**

Add `tests/test_ci_configuration.py` with a test that parses `pyproject.toml`, reads the workflow text, and inspects the target test's pytest marks. It must assert that `serial` is registered, the parallel command excludes it, the serial command selects it without `-n`, and the real-stdio test carries the marker.

- [ ] **Step 2: Verify the regression test fails for the missing isolation**

Run: `.venv/bin/python -m pytest -q tests/test_ci_configuration.py`

Expected: failure because `serial` is not registered or present in the workflow.

- [ ] **Step 3: Add the minimal isolation configuration**

Register `serial: subprocess integration tests that must not run under xdist` in `pyproject.toml`; decorate `test_repo_tools_respond_over_real_stdio` with `@pytest.mark.serial`; import `pytest`; replace the fast test command with:

```yaml
python -m pytest -q -m "not slow and not serial" -n auto
python -m pytest -q -m "serial and not slow"
```

- [ ] **Step 4: Verify the regression test passes**

Run: `.venv/bin/python -m pytest -q tests/test_ci_configuration.py`

Expected: `1 passed`.

- [ ] **Step 5: Repeatedly exercise the isolated real-stdio test**

Run: `for i in {1..10}; do .venv/bin/python -m pytest -q -m "serial and not slow" || exit 1; done`

Expected: all ten invocations pass.

- [ ] **Step 6: Verify both fast-suite partitions and compilation**

Run:

```bash
.venv/bin/python -m pytest -q -m "not slow and not serial" -n auto
.venv/bin/python -m pytest -q -m "serial and not slow"
.venv/bin/python -m compileall -q neo_localmcp setup.py
```

Expected: both pytest partitions pass and compileall exits 0.

- [ ] **Step 7: Commit the focused implementation**

```bash
git add tests/test_ci_configuration.py tests/test_distribution.py pyproject.toml .github/workflows/setup-v2.yml docs/superpowers/plans/2026-07-05-ci-stdio-xdist-isolation.md
git commit -m "fix(ci): isolate real stdio integration test from xdist"
```

### Task 2: Publish and verify live CI

**Files:** none.

**Interfaces:**
- Consumes: the focused commit from Task 1.
- Produces: a GitHub issue containing the observed failure evidence and a PR linked to it with green macOS/Windows CI.

- [ ] **Step 1: File a dedicated issue**

Create a `type:fix` issue describing the observed `anyio.BrokenResourceError`, the duplicate-run evidence, and the accepted isolation design.

- [ ] **Step 2: Push the branch and open a labeled PR**

Push `fix/ci-isolate-real-stdio` and open a PR into `main` titled `fix(ci): isolate real stdio integration test from xdist`, labeled `type:fix`, with `Fixes #<issue>` in the body.

- [ ] **Step 3: Verify live CI**

Run: `gh pr checks <PR number> --watch`

Expected: title validation, both fast jobs, both lifecycle jobs, and `ci-gate` pass.

## Self-Review

- Spec coverage: retains the real stdio test, isolates it from xdist, preserves its timeout, and keeps it out of lifecycle.
- Placeholder scan: runtime issue and PR numbers are intentionally obtained during publication; no implementation decision is deferred.
- Consistency: the marker name is `serial` in pytest configuration, the decorator, and both workflow expressions.
