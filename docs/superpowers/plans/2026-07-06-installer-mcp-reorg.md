# Installer & MCP-Command Reorg Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Consolidate the installer's two frontends (`setup_cli.py`, `wizard/`) under `neo_localmcp/installer/`, split the 1164-line `tools.py` monolith into `neo_localmcp/mcp_commands/` by category, rename `benchmark.py`+`benchmark_queries/` into `neo_localmcp/benchmarker/`, and purge inconsistent "dummy"/"fake" wizard terminology down to one word ("preview") — with zero behavior change, verified by the existing test suite plus updated import paths.

**Architecture:** This is a pure refactor of already-tested, working code — no new logic. Every task is a verbatim relocation of existing functions/classes into a new file (or a rename), followed by updating every known import call-site, followed by running the affected test(s) to confirm nothing broke. Tasks are TDD in spirit (verify-before/verify-after), not TDD in the "write a new failing test" sense, since the behavior under test does not change.

**Tech Stack:** Python 3.12+, pytest, setuptools (`pyproject.toml`).

## Global Constraints

- **Python floor is 3.12+** everywhere (`setup.py`, `setup_wizard.py`, `neo_localmcp/setup_cli.py` all guard this identically) — unaffected by this plan, but do not regress it.
- **No compatibility shims.** Per this repo's `CLAUDE.md`, do not add backwards-compat re-exports, deprecated aliases, or "old path still works" fallbacks for anything moved in this plan. Every import call-site gets updated directly.
- **`git mv` for every file relocation**, never delete+recreate — preserves history.
- **Full verification after every task:** `python -m pytest -q` and `python -m compileall -q neo_localmcp setup.py` must both stay green. Do not proceed to the next task if either fails.
- **This is a refactor-only plan.** No task may change observable behavior of any function being moved. If a step's diff does anything beyond "relocate code" + "fix the accompanying path arithmetic that the relocation invalidates" + "update an import," that is a bug in the plan step, not an intended improvement — stop and flag it.
- **Work happens on a dedicated branch** `refactor/installer-mcp-reorg`, cut from `docs/installer-mcp-reorg-design` so the design spec + this plan + the mermaid diagrams ride along and merge to `main` together with the implementation as one PR. Create it before Task 1: `git checkout docs/installer-mcp-reorg-design && git checkout -b refactor/installer-mcp-reorg`. (See Task 1, Step 1 for the fallback if the docs branch has already merged to `main`.)
- **Design source of truth:** `docs/superpowers/specs/2026-07-06-installer-mcp-reorg-design.md` and its four mermaid diagrams in `mermaid_diagrams/20260706_*.mmd`. This plan implements that spec exactly; if you find a conflict, the spec wins and this plan has a bug.

---

## Task inventory (quick reference)

| # | Task | Files created | Files deleted | Risk |
|---|---|---|---|---|
| 1 | Move `mcpb_build.py` → `installer/mcpb.py` | `installer/mcpb.py` | `mcpb_build.py` | Low |
| 2 | Move `setup_cli.py` → `installer/cli.py` | `installer/cli.py` | `setup_cli.py` | Medium (path arithmetic) |
| 3 | Move `wizard/` → `installer/wizard/`, rename real→live | `installer/wizard/*` | `wizard/*` (old location) | Medium |
| 4 | Rename `fake_backend.py` → `preview_backend.py` | `installer/wizard/preview_backend.py` | `installer/wizard/fake_backend.py` | Medium (path arithmetic) |
| 5 | Purge "dummy" terminology in `console.py`/`setup_wizard.py` | — | — | Low |
| 6 | Update `tests/test_wizard.py` + `tests/test_mcpb_build.py` | — | — | Low |
| 7 | Create `mcp_commands/_shared.py` | `mcp_commands/_shared.py` | — | Low |
| 8 | Create `mcp_commands/system.py` | `mcp_commands/system.py` | — | Medium |
| 9 | Create `mcp_commands/memory.py` | `mcp_commands/memory.py` | — | High (biggest file) |
| 10 | Create `mcp_commands/editing.py` | `mcp_commands/editing.py` | — | Medium |
| 11 | Create `mcp_commands/ollama.py` | `mcp_commands/ollama.py` | — | Low |
| 12 | Delete `tools.py`, repoint all callers | — | `tools.py` | High (many callers) |
| 13 | Rename `benchmark.py`+`benchmark_queries/` → `benchmarker/` | `benchmarker/__init__.py`, `benchmarker/queries/*` | `benchmark.py`, `benchmark_queries/` | Medium |
| 14 | Update `CLAUDE.md` module map + final full-suite verification | — | — | Low |

---

### Task 1: Move `mcpb_build.py` → `installer/mcpb.py` ✅ COMPLETE (commit 2cc410a)

**Why:** `mcpb_build.py`'s only consumer anywhere in the repo is `wizard/real_backend.py` (soon `installer/wizard/live_backend.py`) and its own test file — it is installer-only, so it belongs inside `installer/`.

**Files:**
- Move: `neo_localmcp/mcpb_build.py` → `neo_localmcp/installer/mcpb.py` (body unchanged — `build_mcpb()`, `_is_excluded()`, `_next_free_path()`, all constants, verbatim)
- Modify: `neo_localmcp/wizard/real_backend.py:21` (import line only — this file also moves in Task 3, but fix this import now so Task 1 is independently testable)
- Modify: `tests/test_mcpb_build.py:7` (import line only)

**Interfaces:**
- Produces: `neo_localmcp.installer.mcpb.build_mcpb(source_root: Path | str, version: str) -> Path | None` — identical signature/behavior to the old `neo_localmcp.mcpb_build.build_mcpb`.

- [x] **Step 1: Create the branch**

```bash
git checkout docs/installer-mcp-reorg-design && git checkout -b refactor/installer-mcp-reorg
```

Branch off `docs/installer-mcp-reorg-design` (NOT `main`) — that branch carries this plan, the design spec (`docs/superpowers/specs/2026-07-06-installer-mcp-reorg-design.md`), and the four `mermaid_diagrams/20260706_*.mmd` files, so the reorg branch inherits them and they land on `main` together with the implementation in one PR. `main` does not yet have these design artifacts. (If `docs/installer-mcp-reorg-design` has already been merged to `main` by the time you start, branch off `main` instead — either way the goal is a branch that contains both the design docs and the implementation.)

- [x] **Step 2: Move the file with `git mv`**

```bash
git mv neo_localmcp/mcpb_build.py neo_localmcp/installer/mcpb.py
```

Do not edit the file's contents — `build_mcpb()`'s logic has no `__file__`-relative path assumptions (it takes `source_root` as a parameter), so nothing inside the file needs to change.

- [x] **Step 3: Update `real_backend.py`'s import**

In `neo_localmcp/wizard/real_backend.py`, change line 21 from:

```python
from ..mcpb_build import build_mcpb
```

to:

```python
from ..mcpb import build_mcpb
```

- [x] **Step 4: Update `test_mcpb_build.py`'s import**

In `tests/test_mcpb_build.py`, change line 7 from:

```python
from neo_localmcp import mcpb_build
```

to:

```python
from neo_localmcp.installer import mcpb
```

Then update every call site in that file from `mcpb_build.build_mcpb(...)` to `mcpb.build_mcpb(...)` — this occurs at lines 47, 54, 78, 79, 80, 92 (six call sites, all `mcpb_build.build_mcpb(root, "9.9.9")` or similar — mechanically replace the `mcpb_build.` prefix with `mcpb.`).

Do NOT touch the file's bottom section (`# -- wizard hook --`, lines 95+) yet — those `real_backend`/`RealBackend` references are handled in Task 3/Task 6. Leave them as-is for now; this task only fixes the `mcpb_build` → `mcpb` rename.

- [x] **Step 5: Run the affected tests**

```bash
python -m pytest -q tests/test_mcpb_build.py -k "not wizard"
```

Expected: the four non-wizard tests (`test_build_writes_versioned_bundle`, `test_bundle_contents_match_layout`, `test_second_build_does_not_overwrite`, `test_returns_none_without_staging`) PASS. The three `test_wizard_*` tests at the bottom will still fail/error at this point because they still reference `neo_localmcp.wizard` (unmoved) — that's expected; Task 6 fixes them. Confirm only that the four non-wizard tests pass and nothing you touched broke.

- [x] **Step 6: Compile-check**

```bash
python -m compileall -q neo_localmcp setup.py
```

Expected: no output (success).

- [x] **Step 7: Commit**

```bash
git add neo_localmcp/installer/mcpb.py neo_localmcp/mcpb_build.py neo_localmcp/wizard/real_backend.py tests/test_mcpb_build.py
git commit -m "refactor(installer): move mcpb_build.py into installer/ as mcpb.py"
```

---

### Task 2: Move `setup_cli.py` → `installer/cli.py` ✅ COMPLETE (commit a0684a5)

**Why:** `setup_cli.py` exists purely to parse args and call `neo_localmcp.installer` operations — it is the installer's CLI frontend and belongs inside the domain it drives, directly analogous to `wizard/` being the installer's UI frontend (Task 3).

**Critical gotcha:** `setup_cli.py`'s `_source_root()` does `Path(__file__).resolve().parents[1]` to find the repo root. Today `__file__` = `neo_localmcp/setup_cli.py`, so `parents[0]` = `neo_localmcp/`, `parents[1]` = repo root. After the move, `__file__` = `neo_localmcp/installer/cli.py`, so `parents[1]` = `neo_localmcp/` (wrong!) — it must become `parents[2]` to still reach the repo root. **Missing this breaks every real (non-dry-run) install/reinstall/uninstall**, since `_source_root()` feeds `OperationContext.source_root`, which the runtime-build step validates against (`pyproject.toml` existence, etc.).

**Files:**
- Move: `neo_localmcp/setup_cli.py` → `neo_localmcp/installer/cli.py`
- Modify (within the moved file): imports, `_source_root()`, `argparse` help strings (none reference the old path, no change needed there)
- Modify: `setup.py:44` (delegation target)
- Modify: `neo_localmcp/wizard/real_backend.py:278-280` (the dynamic `_dry_run` import — this file also moves in Task 3, but fix the reference now)

**Interfaces:**
- Produces: `neo_localmcp.installer.cli.main(argv: list[str] | None = None) -> int`, `neo_localmcp.installer.cli.dry_run_plan(operation: str, *, clean: bool = False, delete_memory: bool = False) -> tuple[str, tuple[str, ...]]`, `neo_localmcp.installer.cli.build_parser() -> argparse.ArgumentParser`, `neo_localmcp.installer.cli.build_context(reporter: Reporter | None = None) -> OperationContext` — all identical signatures to the old `neo_localmcp.setup_cli` equivalents.
- Consumes (from Task 1, already in place): nothing new.

- [x] **Step 1: Move the file with `git mv`**

```bash
git mv neo_localmcp/setup_cli.py neo_localmcp/installer/cli.py
```

- [x] **Step 2: Fix `_source_root()`'s path depth**

In `neo_localmcp/installer/cli.py`, change:

```python
def _source_root() -> Path:
    return Path(__file__).resolve().parents[1]
```

to:

```python
def _source_root() -> Path:
    return Path(__file__).resolve().parents[2]
```

- [x] **Step 3: Convert the module-level import from absolute to relative-sibling**

In `neo_localmcp/installer/cli.py`, change:

```python
from neo_localmcp.installer import (  # noqa: E402
    ManagedPaths,
    Operation,
    OperationContext,
    OperationResult,
    OperationStatus,
    Reporter,
    confirm_full_wipe,
    detect_state,
    install,
    operation_explanation,
    reinstall,
    uninstall,
)
```

to:

```python
from .clients import apply_client_selection  # noqa: E402
from .ollama import configure_models  # noqa: E402
from .operations import install, reinstall, uninstall  # noqa: E402
from .output import Reporter, confirm_full_wipe, operation_explanation  # noqa: E402
from .paths import ManagedPaths  # noqa: E402
from .state import detect_state  # noqa: E402
from .types import Operation, OperationContext, OperationResult, OperationStatus  # noqa: E402
```

Note: `OperationContext` is defined in `.operations`, not `.types` — check `neo_localmcp/installer/operations.py`'s own `from .types import (...)` line if unsure, but per the barrel's own grouping (`from .operations import (OperationContext, SourceValidationError, ...)`), the correct import is:

```python
from .operations import OperationContext, install, reinstall, uninstall  # noqa: E402
```

So the full corrected block is:

```python
from .clients import apply_client_selection  # noqa: E402
from .ollama import configure_models  # noqa: E402
from .operations import OperationContext, install, reinstall, uninstall  # noqa: E402
from .output import Reporter, confirm_full_wipe, operation_explanation  # noqa: E402
from .paths import ManagedPaths  # noqa: E402
from .state import detect_state  # noqa: E402
from .types import Operation, OperationResult, OperationStatus  # noqa: E402
```

- [x] **Step 4: Replace the two inline `from neo_localmcp.installer import X` calls**

In `_run_config_ollama` (was line 369), change:

```python
def _run_config_ollama(args: argparse.Namespace, reporter: Reporter) -> int:
    from neo_localmcp.installer import configure_models

    ollama_cfg = configure_models(
```

to (drop the now-redundant inline import, since `configure_models` is already imported at module level per Step 3):

```python
def _run_config_ollama(args: argparse.Namespace, reporter: Reporter) -> int:
    ollama_cfg = configure_models(
```

In `_run_manage_clients` (was line 389), change:

```python
def _run_manage_clients(
    args: argparse.Namespace, context: OperationContext, reporter: Reporter,
) -> int:
    from neo_localmcp.installer import apply_client_selection

    outcome = apply_client_selection(
```

to:

```python
def _run_manage_clients(
    args: argparse.Namespace, context: OperationContext, reporter: Reporter,
) -> int:
    outcome = apply_client_selection(
```

- [x] **Step 5: Update `setup.py`'s delegation**

In `setup.py`, change line 44 from:

```python
        from neo_localmcp.setup_cli import main
```

to:

```python
        from neo_localmcp.installer.cli import main
```

- [x] **Step 6: Update `real_backend.py`'s dynamic dry-run import**

In `neo_localmcp/wizard/real_backend.py`, inside `_dry_run` (was lines 277-280), change:

```python
    def _dry_run(self, state: WizardState, emit: EmitFn) -> OperationOutcome:
        from .. import setup_cli  # public dry_run_plan() lives here; same repo

        key, plan = setup_cli.dry_run_plan(
            state.operation,
```

to:

```python
    def _dry_run(self, state: WizardState, emit: EmitFn) -> OperationOutcome:
        from ..installer import cli as installer_cli  # public dry_run_plan() lives here; same repo

        key, plan = installer_cli.dry_run_plan(
            state.operation,
```

(This file physically moves to `installer/wizard/real_backend.py` → `live_backend.py` in Task 3/Task 4; this step just fixes the reference now so it's correct wherever the file lives. After Task 3 the relative import stays `from ..installer import cli as installer_cli` since `..` will then mean `installer/`'s parent... — **no**, re-check: after Task 3, `real_backend.py`/`live_backend.py` lives at `neo_localmcp/installer/wizard/`, so `..` means `neo_localmcp/installer/`, and `from ..installer import cli` would incorrectly look for `neo_localmcp/installer/installer/cli`. **Do not write the final form yet in this task** — write it correctly for Task 2's *current* file location (`neo_localmcp/wizard/real_backend.py`, where `..` = `neo_localmcp/`), i.e.:

```python
    def _dry_run(self, state: WizardState, emit: EmitFn) -> OperationOutcome:
        from ..installer import cli as installer_cli  # public dry_run_plan() lives here; same repo

        key, plan = installer_cli.dry_run_plan(
            state.operation,
```

This is correct for Task 2 (file still at `neo_localmcp/wizard/`). Task 3 will correct the dot-depth again when the file itself moves.

- [x] **Step 7: Run the affected tests**

```bash
python -m pytest -q tests/installer/test_setup_cli.py
```

Expected: all tests PASS. This test file invokes `setup.py` as a subprocess and never imports `setup_cli`/`installer.cli` by module path, so it should need zero changes — it's testing exactly the `setup.py` → `installer/cli.py` delegation this task just wired up.

```bash
python -m pytest -q tests/installer/ -k "not lifecycle"
```

Expected: all PASS (the `test_*_lifecycle.py` files are slow/real-venv tests; skip them here for speed, they run in full verification at the end).

- [x] **Step 8: Compile-check**

```bash
python -m compileall -q neo_localmcp setup.py
```

- [x] **Step 9: Commit**

```bash
git add neo_localmcp/installer/cli.py neo_localmcp/setup_cli.py setup.py neo_localmcp/wizard/real_backend.py
git commit -m "refactor(installer): move setup_cli.py into installer/ as cli.py"
```

---

### Task 3: Move `wizard/` → `installer/wizard/`, rename `real_backend.py`/`RealBackend` → `live_backend.py`/`LiveBackend` ✅ COMPLETE (commit 5c7e32d)

**Why:** The wizard is the installer's interactive UI frontend, same reasoning as Task 2's CLI frontend. `RealBackend`/`FakeBackend` naming was flagged as confusing; `LiveBackend` is the clearer opposite of the "preview" backend (renamed in Task 4).

**Critical gotchas (both are real bugs if missed):**
1. `real_backend.py`'s relative-import depth changes: it currently does `from .. import client_setup, config, ollama_client` (two dots = `neo_localmcp/`, since the file is at `neo_localmcp/wizard/`). After the move to `neo_localmcp/installer/wizard/`, reaching `neo_localmcp/` needs **three** dots.
2. `real_backend.py`'s `self._source_root = Path(neo_localmcp.__file__).resolve().parent.parent` is **unaffected** by the move — it resolves via the absolute `import neo_localmcp` and `neo_localmcp.__file__`, not via this module's own `__file__`. Do not "fix" this line; it is already correct and moving the file changes nothing about it.

**Files:**
- Move: `neo_localmcp/wizard/__init__.py` → `neo_localmcp/installer/wizard/__init__.py` (unchanged content)
- Move: `neo_localmcp/wizard/_ansi.py` → `neo_localmcp/installer/wizard/_ansi.py` (unchanged content — no `__file__`-relative paths inside it)
- Move: `neo_localmcp/wizard/preflight.py` → `neo_localmcp/installer/wizard/preflight.py` (unchanged content — no `__file__`-relative paths inside it; only cosmetic `--fake` → `--preview` comment fix, done in Task 5)
- Move: `neo_localmcp/wizard/backend.py` → `neo_localmcp/installer/wizard/backend.py` (unchanged content — zero imports beyond stdlib)
- Move + rename: `neo_localmcp/wizard/real_backend.py` → `neo_localmcp/installer/wizard/live_backend.py`, class `RealBackend` → `LiveBackend`
- Modify: `neo_localmcp/wizard/console.py` → moves to `neo_localmcp/installer/wizard/console.py` in this task (import-path fix only; terminology purge is Task 5)
- Modify: `setup_wizard.py:54,59` (delegation targets)

**Interfaces:**
- Produces: `neo_localmcp.installer.wizard.console.run(argv: list[str] | None = None) -> int`; `neo_localmcp.installer.wizard.live_backend.LiveBackend` (implements `WizardBackend`); `neo_localmcp.installer.wizard.backend.WizardBackend`, `.WizardState`, `.DetectedInfo`, `.ClientOption`, `.OllamaInfo`, `.OperationOutcome`, `.StepEvent`, `.EmitFn`, `.human_size`, `.CLIENT_KEYS`, `.CLIENT_LABELS`, `.OP_INSTALL`, `.OP_REINSTALL`, `.OP_UNINSTALL`, `.OP_CONFIG_OLLAMA`, `.OP_MANAGE_CLIENTS`, `.FULL_WIPE_PHRASE` — all identical to today's `neo_localmcp.wizard.backend` exports, just at the new module path.
- Consumes (from Task 1 and Task 2, already in place): `neo_localmcp.installer.mcpb.build_mcpb`, `neo_localmcp.installer.cli.dry_run_plan`.

- [x] **Step 1: Move the four unchanged-content files with `git mv`**

```bash
mkdir -p neo_localmcp/installer/wizard
git mv neo_localmcp/wizard/__init__.py neo_localmcp/installer/wizard/__init__.py
git mv neo_localmcp/wizard/_ansi.py neo_localmcp/installer/wizard/_ansi.py
git mv neo_localmcp/wizard/preflight.py neo_localmcp/installer/wizard/preflight.py
git mv neo_localmcp/wizard/backend.py neo_localmcp/installer/wizard/backend.py
```

Do not edit these four files' contents in this step.

- [x] **Step 2: Move and rename `real_backend.py` → `live_backend.py`**

```bash
git mv neo_localmcp/wizard/real_backend.py neo_localmcp/installer/wizard/live_backend.py
```

- [x] **Step 3: Fix `live_backend.py`'s import depth and class name**

**Note on the file's actual current state:** Task 1 moved `mcpb_build.py` to `installer/mcpb.py` while this file was still at its old location (`neo_localmcp/wizard/real_backend.py`, `..` = `neo_localmcp/`). Reaching `neo_localmcp/installer/mcpb.py` from there needs `from ..installer.mcpb import build_mcpb` (NOT `from ..mcpb import build_mcpb` — that would resolve to a nonexistent `neo_localmcp.mcpb`). If Task 1 was executed correctly, the file's import block currently reads `from ..installer.mcpb import build_mcpb`, not `from ..mcpb_build import build_mcpb`. Verify this with `grep -n "mcpb" neo_localmcp/wizard/real_backend.py` before proceeding — if it still shows `..mcpb_build`, Task 1 was not completed correctly and must be fixed first.

Also per Task 2, Step 6, the `_dry_run` method's inline import currently reads `from ..installer import cli as installer_cli`.

In `neo_localmcp/installer/wizard/live_backend.py` (this task's new location, after Step 2's `git mv` above), fix **all** relative imports for the new depth (`wizard/` → `installer/` → `neo_localmcp/` is now three levels, not two — moving `wizard/` one level deeper changes every dot-count that used to reach `neo_localmcp/` or `installer/` from the old `neo_localmcp/wizard/` location).

Change the top-of-file imports from:

```python
import neo_localmcp
from .. import client_setup, config, ollama_client
from ..installer.mcpb import build_mcpb
from ..installer import (
    ManagedPaths,
    Operation,
    OperationContext,
    OperationStatus,
    Reporter,
    confirm_full_wipe,
    configure_models,
    detect_state,
    install,
    reinstall,
    uninstall,
)
from ..installer import clients as clients_mod
from .backend import (
    CLIENT_KEYS,
    CLIENT_LABELS,
    OP_UNINSTALL,
    ClientOption,
    DetectedInfo,
    EmitFn,
    human_size,
    OllamaInfo,
    OperationOutcome,
    StepEvent,
    WizardState,
)
```

to:

```python
import neo_localmcp
from ... import client_setup, config, ollama_client
from .. import (
    ManagedPaths,
    Operation,
    OperationContext,
    OperationStatus,
    Reporter,
    confirm_full_wipe,
    configure_models,
    detect_state,
    install,
    reinstall,
    uninstall,
)
from .. import clients as clients_mod
from ..mcpb import build_mcpb
from .backend import (
    CLIENT_KEYS,
    CLIENT_LABELS,
    OP_UNINSTALL,
    ClientOption,
    DetectedInfo,
    EmitFn,
    human_size,
    OllamaInfo,
    OperationOutcome,
    StepEvent,
    WizardState,
)
```

(`from .. import (...)` now reaches the `installer/` barrel — one level up from `installer/wizard/` is `installer/`, which is exactly right. `from ... import client_setup, config, ollama_client` now reaches `neo_localmcp/` — two levels up from `installer/wizard/` is `installer/`, three levels up is `neo_localmcp/`.)

Then fix the `_dry_run` method's inline import from:

```python
    def _dry_run(self, state: WizardState, emit: EmitFn) -> OperationOutcome:
        from ..installer import cli as installer_cli  # public dry_run_plan() lives here; same repo

        key, plan = installer_cli.dry_run_plan(
```

to:

```python
    def _dry_run(self, state: WizardState, emit: EmitFn) -> OperationOutcome:
        from .. import cli as installer_cli  # public dry_run_plan() lives here; same repo

        key, plan = installer_cli.dry_run_plan(
```

- [x] **Step 4: Rename the class `RealBackend` → `LiveBackend`**

In `neo_localmcp/installer/wizard/live_backend.py`, change:

```python
class RealBackend:
    """Drives real install/reinstall/uninstall + config against the managed root."""
```

to:

```python
class LiveBackend:
    """Drives real install/reinstall/uninstall + config against the managed root."""
```

This is the only place the class is *defined*; every other file that references `RealBackend` is fixed in Task 6.

- [x] **Step 5: Move `console.py` and fix its import depth**

```bash
git mv neo_localmcp/wizard/console.py neo_localmcp/installer/wizard/console.py
```

In `neo_localmcp/installer/wizard/console.py`, the late imports change. Change:

```python
    def _enter_preview_dummy(self) -> None:
        """One-way switch to the FakeBackend for the rest of this process."""
        from .fake_backend import FakeBackend

        self.backend = FakeBackend()
```

to (import path only — `FakeBackend`/`fake_backend` rename happens in Task 4, don't rename it here yet, this step only confirms the `.fake_backend` relative import is unchanged since both files stay siblings inside `installer/wizard/`):

Actually — no change needed here. `console.py` and (still-named) `fake_backend.py` both move into `installer/wizard/` together as siblings, so `from .fake_backend import FakeBackend` (single dot = same package) is unaffected by the move. Leave it as-is for this step; Task 4 renames it.

Similarly, in the module-level `run()` function, change:

```python
def run(argv: list[str] | None = None) -> int:
    ...
    fake = "--fake" in argv
    if fake:
        from .fake_backend import FakeBackend

        backend: WizardBackend = FakeBackend()
    else:
        from .real_backend import RealBackend

        backend = RealBackend()
```

Only the `real_backend`/`RealBackend` half changes in this task (the `fake_backend`/`FakeBackend` half is untouched until Task 4). Change:

```python
    else:
        from .real_backend import RealBackend

        backend = RealBackend()
```

to:

```python
    else:
        from .live_backend import LiveBackend

        backend = LiveBackend()
```

(The exact surrounding line numbers were 645-649 before any edits in this plan; re-locate by searching for `from .real_backend import RealBackend` since line numbers will have shifted from earlier tasks' edits.)

Also update the `from .backend import (...)` import at the top of `console.py` — no change needed, it's already a same-package sibling import (`backend.py` moved alongside `console.py`).

- [x] **Step 6: Update `setup_wizard.py`'s delegation**

In `setup_wizard.py`, change:

```python
def main() -> int:
    # Stdlib-only preflight -- may print, prompt, pip-install, and re-exec.
    from neo_localmcp.wizard.preflight import ensure_dependencies

    ensure_dependencies(REPO_ROOT, sys.argv)

    # Dependencies are guaranteed present past this point.
    from neo_localmcp.wizard.console import run

    return run(sys.argv[1:])
```

to:

```python
def main() -> int:
    # Stdlib-only preflight -- may print, prompt, pip-install, and re-exec.
    from neo_localmcp.installer.wizard.preflight import ensure_dependencies

    ensure_dependencies(REPO_ROOT, sys.argv)

    # Dependencies are guaranteed present past this point.
    from neo_localmcp.installer.wizard.console import run

    return run(sys.argv[1:])
```

- [x] **Step 7: Update `tests/test_mcpb_build.py`'s wizard-hook section**

Finish what Task 1/Step 4 deferred. In `tests/test_mcpb_build.py`, change:

```python
from neo_localmcp.installer import Operation, OperationStatus  # noqa: E402
from neo_localmcp.wizard import real_backend as rb  # noqa: E402
from neo_localmcp.wizard.backend import WizardState  # noqa: E402
```

to:

```python
from neo_localmcp.installer import Operation, OperationStatus  # noqa: E402
from neo_localmcp.installer.wizard import live_backend as rb  # noqa: E402
from neo_localmcp.installer.wizard.backend import WizardState  # noqa: E402
```

And every `rb.RealBackend()` call site (there are 4: in `_run`'s type hint and in `test_wizard_install_surfaces_built_bundle`, `test_wizard_uninstall_does_not_build`, `test_wizard_survives_build_failure`) becomes `rb.LiveBackend()`. The `monkeypatch.setattr(rb, "install", ...)`, `monkeypatch.setattr(rb, "uninstall", ...)`, `monkeypatch.setattr(rb, "build_mcpb", ...)` calls need NO change — they patch attributes on the `rb` module object itself (which still exists, just aliased to `live_backend` now), and those names (`install`, `uninstall`, `build_mcpb`) are still imported into that module's namespace after Task 3's Step 3 edits.

- [x] **Step 8: Run the affected tests**

```bash
python -m pytest -q tests/test_mcpb_build.py
```

Expected: all 7 tests PASS now (the four from Task 1 plus the three wizard-hook tests, now fixed).

```bash
python -m pytest -q tests/installer/ -k "not lifecycle"
```

Expected: all PASS.

Note: `tests/test_wizard.py` will still fail at this point — it's fixed in Task 6.

- [x] **Step 9: Compile-check**

```bash
python -m compileall -q neo_localmcp setup.py
```

- [x] **Step 10: Commit**

```bash
git add -A
git commit -m "refactor(installer): move wizard/ into installer/wizard/, rename real_backend.py to live_backend.py"
```

---

### Task 4: Rename `fake_backend.py` → `preview_backend.py`, `FakeBackend` → `PreviewBackend` ✅ COMPLETE (commit 8e7212c)

**Why:** Consistent with the "preview" naming chosen for the wizard's simulation mode (see Task 5 for the full terminology purge). Splitting this into its own task from Task 5 keeps the file-rename mechanics separate from the pure text/terminology edits inside `console.py`.

**Critical gotcha:** `fake_backend.py`'s `_STATE_DIR = Path(__file__).resolve().parents[2] / ".wizard_preview"`. Today `__file__` = `neo_localmcp/wizard/fake_backend.py` → `parents[0]`=`wizard/`, `parents[1]`=`neo_localmcp/`, `parents[2]`=repo root. After Task 3 already moved this file's *sibling* `console.py` etc. into `installer/wizard/`, and this task moves `fake_backend.py` itself into that same new location (`neo_localmcp/installer/wizard/preview_backend.py`), the depth changes: `parents[0]`=`wizard/`, `parents[1]`=`installer/`, `parents[2]`=`neo_localmcp/` (wrong — one level short). **It must become `parents[3]`** to still reach the repo root. Missing this makes `.wizard_preview/state.json` get created inside `neo_localmcp/` instead of at the repo root — the directory is gitignored either way so it wouldn't break CI, but it would silently break every local `--preview` run's state persistence (each run would look in the wrong place, always reseed instead of round-tripping state) and the round-trip test in Task 6 would fail.

**Files:**
- Move + rename: `neo_localmcp/installer/wizard/fake_backend.py` → `neo_localmcp/installer/wizard/preview_backend.py`, class `FakeBackend` → `PreviewBackend`
- Modify: `neo_localmcp/installer/wizard/backend.py` (docstring only, Step 6 — a gap the Task 3 review surfaced: no other task was scheduled to fix its stale module-path references)

**Interfaces:**
- Produces: `neo_localmcp.installer.wizard.preview_backend.PreviewBackend` (implements `WizardBackend`), module attributes `_STATE_PATH`, `_STATE_DIR`, `_STEP_DELAY` (same names, same meaning, just class/module renamed) — these three names are monkeypatched by `tests/test_wizard.py` (fixed in Task 6), so they must keep their exact names.

- [x] **Step 1: Move and rename**

**Note:** Task 3's file list deliberately excluded `fake_backend.py` — it moved the other six wizard/ files into `installer/wizard/` but left `fake_backend.py` behind at its original top-level location, `neo_localmcp/wizard/fake_backend.py`, specifically for this task to move+rename in one step. Verify with `ls neo_localmcp/wizard/` (should show only `fake_backend.py` and possibly stale `__pycache__/`) before running:

```bash
git mv neo_localmcp/wizard/fake_backend.py neo_localmcp/installer/wizard/preview_backend.py
```

- [x] **Step 2: Fix `_STATE_DIR`'s path depth**

In `neo_localmcp/installer/wizard/preview_backend.py`, change:

```python
_STATE_DIR = Path(__file__).resolve().parents[2] / ".wizard_preview"
```

to:

```python
_STATE_DIR = Path(__file__).resolve().parents[3] / ".wizard_preview"
```

- [x] **Step 3: Rename the class**

Change:

```python
class FakeBackend:
    """A fully navigable, side-effect-free WizardBackend."""
```

to:

```python
class PreviewBackend:
    """A fully navigable, side-effect-free WizardBackend."""
```

- [x] **Step 4: Update `console.py`'s references to the moved/renamed module**

In `neo_localmcp/installer/wizard/console.py`:

Change:

```python
    def _enter_preview_dummy(self) -> None:
        """One-way switch to the FakeBackend for the rest of this process."""
        from .fake_backend import FakeBackend

        self.backend = FakeBackend()
```

to (method rename is part of Task 5's terminology purge — for this task, only fix the module/class reference, keep the method name and docstring as-is; Task 5 finishes the rest):

```python
    def _enter_preview_dummy(self) -> None:
        """One-way switch to the PreviewBackend for the rest of this process."""
        from .preview_backend import PreviewBackend

        self.backend = PreviewBackend()
```

And in the module-level `run()` function, change:

```python
    fake = "--fake" in argv
    if fake:
        from .fake_backend import FakeBackend

        backend: WizardBackend = FakeBackend()
```

to (again, only the module/class reference — the `--fake`/`fake` flag rename is Task 5):

```python
    fake = "--fake" in argv
    if fake:
        from .preview_backend import PreviewBackend

        backend: WizardBackend = PreviewBackend()
```

- [x] **Step 5: Rename the environment variable**

In `neo_localmcp/installer/wizard/preview_backend.py`, change **all three** occurrences of `NEO_LOCALMCP_WIZARD_FAKE_STATE` to `NEO_LOCALMCP_WIZARD_PREVIEW_STATE`: the code in `_seed_state()`, the module docstring near the top of the file, and a comment above `_STATE_DIR`. The code occurrence is in `_seed_state()`:

```python
def _seed_state() -> dict[str, Any]:
    start = os.environ.get("NEO_LOCALMCP_WIZARD_FAKE_STATE", "absent").strip().lower()
```

becomes:

```python
def _seed_state() -> dict[str, Any]:
    start = os.environ.get("NEO_LOCALMCP_WIZARD_PREVIEW_STATE", "absent").strip().lower()
```

Also update the module docstring's mention of this env var (near the top of the file) from `NEO_LOCALMCP_WIZARD_FAKE_STATE` to `NEO_LOCALMCP_WIZARD_PREVIEW_STATE`, and `setup_wizard.py`'s docstring, which also documents this env var:

```python
             NEO_LOCALMCP_WIZARD_FAKE_STATE=healthy to simulate a returning user
```

becomes:

```python
             NEO_LOCALMCP_WIZARD_PREVIEW_STATE=healthy to simulate a returning user
```

- [x] **Step 6: Fix `backend.py`'s stale docstring (review-pass addition, Task 3)**

Task 3's review found that `neo_localmcp/installer/wizard/backend.py`'s module docstring (lines 4-7) was never scheduled for a fix by any task: it names the two backend implementations by their OLD module paths and OLD class names, and nothing else in this plan touches it. Fix it now, in this task, since this is where both renames it needs (`fake_backend`→`preview_backend`, and the `real_backend`→`live_backend` rename Task 3 already did) are both in scope. Change:

```python
"""The seam between the wizard UI and real lifecycle work.

Screens depend only on :class:`WizardBackend` and the plain dataclasses here --
never on ``neo_localmcp.installer`` directly. Two implementations satisfy this
contract: :mod:`neo_localmcp.wizard.fake_backend` (in-memory, side-effect free,
for walking the flow) and :mod:`neo_localmcp.wizard.real_backend` (drives the
actual install lifecycle). Swapping them is a one-line change in ``app.py``.

This module is stdlib-only on purpose so it stays importable everywhere.
"""
```

to:

```python
"""The seam between the wizard UI and real lifecycle work.

Screens depend only on :class:`WizardBackend` and the plain dataclasses here --
never on ``neo_localmcp.installer`` directly. Two implementations satisfy this
contract: :mod:`neo_localmcp.installer.wizard.preview_backend` (in-memory,
side-effect free, for walking the flow) and
:mod:`neo_localmcp.installer.wizard.live_backend` (drives the actual install
lifecycle). Swapping them is a one-line change in ``app.py``.

This module is stdlib-only on purpose so it stays importable everywhere.
"""
```

- [x] **Step 7: Run the affected tests**

```bash
python -m pytest -q tests/test_mcpb_build.py tests/installer/ -k "not lifecycle"
```

Expected: all PASS (nothing here references `fake_backend`/`FakeBackend` yet — `test_wizard.py` is Task 6).

- [x] **Step 8: Compile-check**

```bash
python -m compileall -q neo_localmcp setup.py setup_wizard.py
```

- [x] **Step 9: Commit**

```bash
git add -A
git commit -m "refactor(wizard): rename fake_backend.py to preview_backend.py, FakeBackend to PreviewBackend"
```

---

### Task 5: Purge remaining "dummy" terminology in `console.py` ✅ COMPLETE (commit 13d513d)

**Why:** The UI previously used three inconsistent words for the same concept ("fake" in code, "dummy" in the toggle key/exception, "preview" in the state dir name). Task 4 fixed the file/class names; this task fixes the remaining UI copy, the toggle mechanism, and the `--fake` CLI flag, so "preview" is the only word used anywhere.

**Files:**
- Modify: `neo_localmcp/installer/wizard/console.py` (multiple spots — see below)
- Modify: `setup_wizard.py` (docstring `--fake` mention)
- Modify: `neo_localmcp/installer/wizard/preview_backend.py` (docstring/comment mentions of `--fake`, cosmetic only)
- Modify: `neo_localmcp/installer/wizard/preflight.py` (one comment mentioning `--fake`, cosmetic only)

**Interfaces:** No public interface changes — this task is pure text/identifier renaming inside `console.py`'s private implementation. `run(argv)`'s signature is unchanged; only the flag string it looks for in `argv` changes from `"--fake"` to `"--preview"`.

- [x] **Step 1: Rename the `_ToggleDummy` exception**

In `neo_localmcp/installer/wizard/console.py`, change:

```python
class _ToggleDummy(Exception):
    """Raised by the main-menu prompt when the user types 'd'/'dummy'."""
```

to:

```python
class _TogglePreview(Exception):
    """Raised by the main-menu prompt when the user types 'p'/'preview'."""
```

- [x] **Step 2: Rename the `allow_dummy_toggle` parameter and its usages**

Find the input-primitive method (was around line 166-174) and change:

```python
        allow_dummy_toggle: bool = False,
    ) -> int:
        ...
        toggle_hint = " (or d for preview dummy mode)" if allow_dummy_toggle else ""
        ...
            if allow_dummy_toggle and raw.strip().lower() in {"d", "dummy"}:
                raise _ToggleDummy
```

to:

```python
        allow_preview_toggle: bool = False,
    ) -> int:
        ...
        toggle_hint = " (or p for preview mode)" if allow_preview_toggle else ""
        ...
            if allow_preview_toggle and raw.strip().lower() in {"p", "preview"}:
                raise _TogglePreview
```

Then find every call site passing `allow_dummy_toggle=...` (there is one, in the main-menu loop, was around line 289):

```python
                choice = self._ask_int(1, len(rows), allow_dummy_toggle=not self.fake)
            except _ToggleDummy:
                self._enter_preview_dummy()
```

becomes:

```python
                choice = self._ask_int(1, len(rows), allow_preview_toggle=not self.fake)
            except _TogglePreview:
                self._enter_preview()
```

- [x] **Step 3: Rename `_enter_preview_dummy` → `_enter_preview`**

From Task 4's Step 4, the method currently reads:

```python
    def _enter_preview_dummy(self) -> None:
        """One-way switch to the PreviewBackend for the rest of this process."""
        from .preview_backend import PreviewBackend

        self.backend = PreviewBackend()
```

Change the method name (keep the body from Task 4):

```python
    def _enter_preview(self) -> None:
        """One-way switch to the PreviewBackend for the rest of this process."""
        from .preview_backend import PreviewBackend

        self.backend = PreviewBackend()
```

(The call site fixed in Step 2 above already calls `self._enter_preview()`.)

- [x] **Step 4: Fix the "[Preview Dummy]" UI label**

Find (was around line 107):

```python
            title += "   " + _ansi.yellow("[Preview Dummy]")
```

Change to:

```python
            title += "   " + _ansi.yellow("[Preview Mode]")
```

- [x] **Step 5: Fix the "Preview Dummy is active" warning string**

Find (was around line 501):

```python
                "** Preview Dummy is active -- choosing Yes will show a demo output. "
```

Change to:

```python
                "** Preview mode is active -- choosing Yes will show a demo output. "
```

- [x] **Step 6: Rename the `--fake` flag to `--preview` in `run()`**

Change:

```python
def run(argv: list[str] | None = None) -> int:
    ...
    fake = "--fake" in argv
```

to:

```python
def run(argv: list[str] | None = None) -> int:
    ...
    fake = "--preview" in argv
```

(Keep the local variable named `fake` — it's an internal boolean, not user-facing; renaming it adds churn with zero benefit. Only the *string it matches against* changes.)

- [x] **Step 7: Update `setup_wizard.py`'s docstring**

Change:

```python
Flags:
    --fake   Run against an in-memory simulation. No processes, venvs, network,
             or files are touched -- a safe way to walk the whole flow. Set
             NEO_LOCALMCP_WIZARD_PREVIEW_STATE=healthy to simulate a returning user
             (already-installed) instead of a first-time clone.
```

to:

```python
Flags:
    --preview   Run against an in-memory simulation. No processes, venvs, network,
                or files are touched -- a safe way to walk the whole flow. Set
                NEO_LOCALMCP_WIZARD_PREVIEW_STATE=healthy to simulate a returning user
                (already-installed) instead of a first-time clone.
```

(The env var name here was already fixed in Task 4/Step 5 — this step only changes the flag name in the docstring.)

- [x] **Step 8: Fix cosmetic `--fake` mentions in `preview_backend.py`**

In `neo_localmcp/installer/wizard/preview_backend.py`, the module docstring and one comment mention `--fake`:

```python
walk the whole flow safely (``python setup_wizard.py --fake``).
```

becomes:

```python
walk the whole flow safely (``python setup_wizard.py --preview``).
```

```python
repo root (gitignored), so a later ``--fake`` run sees what a previous
```

becomes:

```python
repo root (gitignored), so a later ``--preview`` run sees what a previous
```

```python
# Persisted simulation state, so a later `--fake` run (or mid-session `d`
# toggle) sees what a previous simulated install/uninstall would have left
```

becomes:

```python
# Persisted simulation state, so a later `--preview` run (or mid-session `p`
# toggle) sees what a previous simulated install/uninstall would have left
```

And the three occurrences of `"This is a simulation (--fake)."` / `"This was a simulation (--fake) - nothing on disk changed."` / `"This was a simulation (--fake)."` in the `run_operation`/`_simulate_install_like`/`_simulate_uninstall` methods all become `--preview` instead of `--fake` (mechanical string replacement, same three spots identified in the file already).

- [x] **Step 9: Fix cosmetic `--fake` mention in `preflight.py`**

In `neo_localmcp/installer/wizard/preflight.py`, change:

```python
    # Re-exec so the freshly-installed packages load in a clean process. Preserve
    # the original arguments (e.g. --fake) across the restart.
```

to:

```python
    # Re-exec so the freshly-installed packages load in a clean process. Preserve
    # the original arguments (e.g. --preview) across the restart.
```

- [x] **Step 10: Run the affected tests**

```bash
python -m pytest -q tests/installer/ -k "not lifecycle"
```

Expected: all PASS (no test yet directly exercises `console.py`'s toggle mechanism — `tests/test_wizard.py` tests the backends, not the console UI, per Task 6's fix).

- [x] **Step 11: Compile-check**

```bash
python -m compileall -q neo_localmcp setup.py setup_wizard.py
```

- [x] **Step 12: Commit**

```bash
git add -A
git commit -m "refactor(wizard): purge 'dummy' terminology from console.py, standardize on 'preview'"
```

---

### Task 6: Update `tests/test_wizard.py` for the new module paths and names ✅ COMPLETE (commit 642d1e4) — INSTALLER/WIZARD CHECKPOINT DONE (Tasks 1-6)

**Why:** This is the one test file directly exercising the wizard backends by name; it must follow every rename from Tasks 3-5.

**Files:**
- Modify: `tests/test_wizard.py` (full rewrite of imports and identifiers, logic unchanged)

**Interfaces:** No production interfaces change here — this task only updates test code to match Tasks 3-5's already-completed renames.

- [x] **Step 1: Update the imports**

Change:

```python
from neo_localmcp.wizard import fake_backend, real_backend
from neo_localmcp.wizard.backend import (
    OP_INSTALL,
    OP_UNINSTALL,
    WizardBackend,
    WizardState,
)
```

to:

```python
from neo_localmcp.installer.wizard import live_backend, preview_backend
from neo_localmcp.installer.wizard.backend import (
    OP_INSTALL,
    OP_UNINSTALL,
    WizardBackend,
    WizardState,
)
```

- [x] **Step 2: Rename the `_isolated_fake_backend` helper and its body**

Change:

```python
def _isolated_fake_backend(tmp_path, monkeypatch):
    # fake_backend persists simulated state to a fixed path relative to the
    # repo checkout (.wizard_preview/state.json), not something callers can
    # parameterize -- redirect it so tests don't read/write a real file in
    # this repo's working tree or leak state between tests (#13: this whole
    # module had zero pytest coverage before this file). Callers set
    # NEO_LOCALMCP_WIZARD_FAKE_STATE themselves (via monkeypatch) before
    # calling this, if they want a seed other than the "absent" default.
    monkeypatch.setattr(fake_backend, "_STATE_PATH", tmp_path / "wizard_state.json")
    return fake_backend.FakeBackend()
```

to:

```python
def _isolated_preview_backend(tmp_path, monkeypatch):
    # preview_backend persists simulated state to a fixed path relative to the
    # repo checkout (.wizard_preview/state.json), not something callers can
    # parameterize -- redirect it so tests don't read/write a real file in
    # this repo's working tree or leak state between tests (#13: this whole
    # module had zero pytest coverage before this file). Callers set
    # NEO_LOCALMCP_WIZARD_PREVIEW_STATE themselves (via monkeypatch) before
    # calling this, if they want a seed other than the "absent" default.
    monkeypatch.setattr(preview_backend, "_STATE_PATH", tmp_path / "wizard_state.json")
    return preview_backend.PreviewBackend()
```

- [x] **Step 3: Rename every test function and call site**

Apply this mechanical rename across the whole file (every occurrence):
- `_isolated_fake_backend` → `_isolated_preview_backend` (all 7 call sites)
- `test_fake_backend_satisfies_wizard_backend_protocol` → `test_preview_backend_satisfies_wizard_backend_protocol`
- `test_real_backend_satisfies_wizard_backend_protocol` → `test_live_backend_satisfies_wizard_backend_protocol`, and inside it, `real_backend.RealBackend()` → `live_backend.LiveBackend()`
- `test_fake_backend_detects_absent_by_default` → `test_preview_backend_detects_absent_by_default`
- `test_fake_backend_detects_healthy_when_seeded` → `test_preview_backend_detects_healthy_when_seeded`, and inside it, `monkeypatch.setenv("NEO_LOCALMCP_WIZARD_FAKE_STATE", "healthy")` → `monkeypatch.setenv("NEO_LOCALMCP_WIZARD_PREVIEW_STATE", "healthy")`
- `test_fake_backend_client_options_cover_every_client_key` → `test_preview_backend_client_options_cover_every_client_key`
- `test_fake_backend_ollama_info_reports_simulated_models` → `test_preview_backend_ollama_info_reports_simulated_models`
- `test_fake_backend_dry_run_install_makes_no_state_change` → `test_preview_backend_dry_run_install_makes_no_state_change`
- `test_fake_backend_install_then_uninstall_round_trips_state` → `test_preview_backend_install_then_uninstall_round_trips_state`, and inside it: `monkeypatch.setattr(fake_backend, "_STEP_DELAY", 0.0)` → `monkeypatch.setattr(preview_backend, "_STEP_DELAY", 0.0)`, and the comment `# the same way a later --fake run would see...` → `# the same way a later --preview run would see...`
- `test_real_backend_apply_client_changes_uses_shared_helper` → `test_live_backend_apply_client_changes_uses_shared_helper`; inside it, change:
  ```python
  from neo_localmcp.installer import clients as clients_mod
  from neo_localmcp.wizard.backend import WizardState
  from neo_localmcp.wizard.real_backend import RealBackend
  ```
  to:
  ```python
  from neo_localmcp.installer import clients as clients_mod
  from neo_localmcp.installer.wizard.backend import WizardState
  from neo_localmcp.installer.wizard.live_backend import LiveBackend
  ```
  and `backend = RealBackend()` → `backend = LiveBackend()`
- `test_real_backend_apply_ollama_config_uses_shared_helper` → `test_live_backend_apply_ollama_config_uses_shared_helper`; inside it, change:
  ```python
  from neo_localmcp import config
  from neo_localmcp.wizard.backend import WizardState
  from neo_localmcp.wizard.real_backend import RealBackend
  ```
  to:
  ```python
  from neo_localmcp import config
  from neo_localmcp.installer.wizard.backend import WizardState
  from neo_localmcp.installer.wizard.live_backend import LiveBackend
  ```
  and `backend = RealBackend()` → `backend = LiveBackend()`

- [x] **Step 4: Run the full wizard test file**

```bash
python -m pytest -q tests/test_wizard.py -v
```

Expected: all 10 tests PASS.

- [x] **Step 5: Run the full fast suite so far**

```bash
python -m pytest -q -m "not slow" --deselect tests/test_distribution.py::test_built_mcpb_embeds_current_package_bytes
```

Expected: all PASS (this is the first point where every test touched by Tasks 1-6 runs together).

**Why the `--deselect`:** `test_built_mcpb_embeds_current_package_bytes` walks `neo_localmcp/` and asserts the committed `packages/claude-desktop/neo-localmcp-v<version>.mcpb` bundle contains every file byte-for-byte. Tasks 1-5 moved files under `neo_localmcp/` without regenerating that build artifact, so it is legitimately stale mid-reorg. It is deselected at every intermediate checkpoint and regenerated + run for real once in Task 14. This test checks bundle freshness, not reorg correctness, so deselecting it here loses no coverage of the actual code moves. (The sibling `test_built_mcpb_contains_valid_manifest` still runs and passes — it only checks the manifest + `server.py` presence, and `__version__` is unchanged, so the bundle filename/manifest still match.)

- [x] **Step 6: Compile-check**

```bash
python -m compileall -q neo_localmcp setup.py setup_wizard.py
```

- [x] **Step 7: Commit**

```bash
git add tests/test_wizard.py
git commit -m "test(wizard): update test_wizard.py for installer/wizard/ move and preview_backend rename"
```

**Checkpoint:** The entire installer/wizard reorg (spec sections "installer" + "wizard") is now complete and independently verified. Tasks 7-13 are unrelated to this work and can proceed independently.

---

### Task 7: Create `neo_localmcp/mcp_commands/_shared.py` ✅ COMPLETE (commit 46ebfc7)

**Why:** Three helpers in `tools.py` are genuinely used across what will become 2+ separate category files (`json_out` by all four; `_format_model_timing`/`_ns_to_seconds` and `_slim_status_for_nesting` by both `memory.py`'s `context_prepare` and `editing.py`'s `summarize_file`/`_summarize_section`). Per the design's rule ("no category imports another category — promote to `_shared.py` instead"), these three live here.

**Files:**
- Create: `neo_localmcp/mcp_commands/__init__.py` (empty — just makes the directory a package)
- Create: `neo_localmcp/mcp_commands/_shared.py`

**Interfaces:**
- Produces: `neo_localmcp.mcp_commands._shared.json_out(data: Any) -> str`, `neo_localmcp.mcp_commands._shared._format_model_timing(result: dict[str, Any] | None) -> dict[str, Any] | None`, `neo_localmcp.mcp_commands._shared._slim_status_for_nesting(status: dict[str, Any] | None) -> dict[str, Any] | None`. Tasks 8-11 consume these via `from ._shared import json_out` etc.

- [x] **Step 1: Create the package directory and empty `__init__.py`**

```bash
mkdir -p neo_localmcp/mcp_commands
touch neo_localmcp/mcp_commands/__init__.py
```

- [x] **Step 2: Write `_shared.py`**

Create `neo_localmcp/mcp_commands/_shared.py` with exactly this content (moved verbatim from `tools.py` lines 25-53 and 1018-1024 — `json_out`, `_ns_to_seconds`, `_format_model_timing`, `_slim_status_for_nesting`):

```python
from __future__ import annotations

import json
from typing import Any


def json_out(data: Any) -> str:
    return json.dumps(data, indent=2, ensure_ascii=False, default=str)


def _ns_to_seconds(ns: Any) -> float | None:
    try:
        if ns is None:
            return None
        return round(float(ns) / 1_000_000_000, 3)
    except Exception:
        return None


def _format_model_timing(result: dict[str, Any] | None) -> dict[str, Any] | None:
    if not result:
        return None
    raw = result.get("raw") or {}
    return {
        "ok": result.get("ok"),
        "model": result.get("model"),
        "total_seconds": _ns_to_seconds(raw.get("total_duration")),
        "eval_seconds": _ns_to_seconds(raw.get("eval_duration")),
        "eval_count": raw.get("eval_count"),
        "elapsed_seconds": result.get("elapsed_seconds"),
        "timeout_seconds": result.get("timeout_seconds"),
        "timed_out": bool(result.get("timed_out")),
        "near_timeout": bool(result.get("near_timeout")),
        "error": result.get("error"),
    }


def _slim_status_for_nesting(status: dict[str, Any] | None) -> dict[str, Any] | None:
    """Drop the verbose installed_models list from a second, nested copy of an Ollama
    status dict. The top-level ollama_status key in the same response keeps the full
    list; embedding it again inside ollama_summary/ollama_ranking is pure duplication."""
    if not status:
        return status
    return {k: v for k, v in status.items() if k != "installed_models"}
```

Do not delete these four definitions from `tools.py` yet — `tools.py` still exists and is still what `server.py`/`cli.py`/etc. import from until Task 12. This task only creates the new file; Task 12 removes the old one.

- [x] **Step 2: Compile-check the new file**

```bash
python -m compileall -q neo_localmcp/mcp_commands
```

- [x] **Step 3: Commit**

```bash
git add neo_localmcp/mcp_commands/__init__.py neo_localmcp/mcp_commands/_shared.py
git commit -m "refactor(mcp-commands): add _shared.py with json_out/_format_model_timing/_slim_status_for_nesting"
```

---

### Task 8: Create `neo_localmcp/mcp_commands/system.py`

**Why:** `init`, `status`, `where`, `model_status`, `doctor`, `repo_index`, `repo_reindex`, `repo_refresh`, `repo_lookup`, `reset_repo`, `reset_all` are the "system/repo management" category — none of them touch context-ranking, summarization, or Ollama configuration beyond a basic reachability ping.

**Files:**
- Create: `neo_localmcp/mcp_commands/system.py`

**Interfaces:**
- Produces: `init() -> str`, `status(repo_root: str = "auto") -> str`, `where(repo_root: str = "auto") -> str`, `model_status() -> str`, `doctor(repo_root: str = "auto") -> str`, `repo_index(repo_root: str = "auto", max_files: int | None = None, force: bool = False) -> str`, `repo_reindex(repo_root: str = "auto", max_files: int | None = None) -> str`, `repo_refresh(repo_root: str = "auto", max_files: int | None = None, force: bool = False) -> str`, `repo_lookup(query: str, repo_root: str = "auto", limit: int = 20) -> str`, `reset_repo(repo_root: str = "auto") -> str`, `reset_all() -> str` — identical signatures to today's `tools.*` equivalents.
- Consumes (from Task 7): `from ._shared import json_out`.

- [ ] **Step 1: Write `system.py`**

Create `neo_localmcp/mcp_commands/system.py`, moving these functions verbatim from `tools.py` (lines 350-427 for `init`/`status`/`where`/`model_status`/`doctor`/`repo_index`/`repo_reindex`/`reset_repo`/`reset_all`, plus lines 501 and 505 for `repo_refresh`/`repo_lookup`):

```python
from __future__ import annotations

from . import repo_memory
from .config import CONFIG_PATH, ensure_config, load_config
from .identity import IDENTITY
from .ollama_client import ping
from .utils import repo_root_or_cwd
from .mcp_commands._shared import json_out
```

Wait — `system.py` lives at `neo_localmcp/mcp_commands/system.py`, so its relative imports to top-level `neo_localmcp/` modules need **one** dot (same depth as `tools.py` had, since both `tools.py` and `mcp_commands/` are direct children of `neo_localmcp/`... no — `tools.py` was directly in `neo_localmcp/`, but `system.py` is in `neo_localmcp/mcp_commands/`, one level deeper). Use exactly this import block instead:

```python
from __future__ import annotations

from .. import repo_memory
from ..config import CONFIG_PATH, ensure_config, load_config
from ..identity import IDENTITY
from ..ollama_client import ping
from ..utils import repo_root_or_cwd
from ._shared import json_out


def init() -> str:
    path = ensure_config()
    return json_out({
        "ok": True,
        "product": IDENTITY.product_name,
        "config_path": str(path),
        "next": [
            "Run client setup once from anywhere: neo-localmcp config clients setup --client all",
            "Then cd into the repo you want analyzed: cd /path/to/your/repo",
            "Index that repo: neo-localmcp index",
            "Ask for context: neo-localmcp context \"debug feature X: KnownSymbol, FileName.cs\"",
        ],
    })


def status(repo_root: str = "auto") -> str:
    return json_out({"product": IDENTITY.as_dict(), "config_path": str(CONFIG_PATH), "repo": repo_memory.status(repo_root), "ollama": ping()})


def where(repo_root: str = "auto") -> str:
    cfg = load_config()
    root = repo_root_or_cwd(repo_root)
    return json_out({
        "product": IDENTITY.product_name,
        "installed_command_hint": "neo-localmcp",
        "config_path": str(CONFIG_PATH),
        "current_repo": str(root),
        "repo_db": str(repo_memory.db_path()),
        "ollama_base_url": cfg.get("ollama", {}).get("base_url"),
        "summary_model": cfg.get("ollama", {}).get("summary_model"),
        "note": "Run index/context from the repo you want analyzed. Client setup (neo-localmcp config clients setup) can be run once from anywhere.",
    })


def model_status() -> str:
    cfg = load_config()
    return json_out({
        "ollama_config": cfg.get("ollama", {}),
        "ollama_ping": ping(),
        "note": "Context is deterministic by default in V1. Use --ollama-rank or use_ollama=true for optional Ollama ranking.",
    })


def doctor(repo_root: str = "auto") -> str:
    from .. import lifecycle
    cfg = load_config()
    checks = {
        "config_exists": CONFIG_PATH.exists(),
        "db_open": True,
        "ollama": ping(),
        "repo": repo_memory.status(repo_root),
        "running_servers": lifecycle.list_servers(prune=True),
        "rules": [
            "neo-localmcp retrieves, indexes, summarizes, ranks, and applies exact approved patches.",
            "neo-localmcp does not generate source code or make engineering decisions.",
            "Claude/Codex reason and create exact patches.",
            "Context lookup is deterministic by default; Ollama ranking is opt-in with --ollama-rank or MCP use_ollama=true.",
            "Run `neo-localmcp --help` for the full, authoritative command inventory.",
        ],
        "config": {"ollama_base_url": cfg.get("ollama", {}).get("base_url"), "summary_model": cfg.get("ollama", {}).get("summary_model"), "db_path": cfg.get("memory", {}).get("db_path")},
    }
    return json_out({"ok": True, **checks})


def repo_index(repo_root: str = "auto", max_files: int | None = None, force: bool = False) -> str:
    return json_out(repo_memory.index_repo(repo_root, max_files=max_files, force=force))


def repo_reindex(repo_root: str = "auto", max_files: int | None = None) -> str:
    return json_out(repo_memory.index_repo(repo_root, max_files=max_files, force=True))


def reset_repo(repo_root: str = "auto") -> str:
    return json_out(repo_memory.reset_repo(repo_root))


def reset_all() -> str:
    return json_out(repo_memory.reset_all())


def repo_refresh(repo_root: str = "auto", max_files: int | None = None, force: bool = False) -> str:
    return json_out(repo_memory.refresh(repo_root, force=force, max_files=max_files))


def repo_lookup(query: str, repo_root: str = "auto", limit: int = 20) -> str:
    return json_out(repo_memory.lookup(query, repo_root, limit=limit))
```

Note: `doctor()`'s `from .. import lifecycle` stays a deferred/inline import exactly as it was in `tools.py` (avoids a module-level dependency cycle risk — preserve this behavior verbatim, don't "clean it up" to a top-level import).

- [ ] **Step 2: Compile-check**

```bash
python -m compileall -q neo_localmcp/mcp_commands
```

- [ ] **Step 3: Commit**

```bash
git add neo_localmcp/mcp_commands/system.py
git commit -m "refactor(mcp-commands): add system.py (init/status/where/doctor/repo_index/reindex/refresh/lookup/reset)"
```

(This file isn't wired into `server.py`/`cli.py` yet — that happens in Task 12, once all four category files exist and `tools.py` is deleted in one atomic step to avoid a half-migrated state.)

---

### Task 9: Create `neo_localmcp/mcp_commands/memory.py`

**Why:** This is the context-retrieval/ranking pipeline — `context_prepare`/`prepare_context` and everything that exists solely to support them (scoring, heading matching, excerpt-range selection, retrieval-boost, determinism testing). It is the largest and highest-risk file in this plan; move it verbatim, do not refactor its internals.

**Files:**
- Create: `neo_localmcp/mcp_commands/memory.py`

**Interfaces:**
- Produces: `context_prepare(...) -> str`, `prepare_context(...) -> str`, `file_context(...) -> str`, `file_excerpts(...) -> str`, `record_change(...) -> str`, `test_determinism(...) -> str` — identical signatures to today's `tools.*` equivalents. Also produces (private, used only within this file): `_render_context_text`, `_mcp_compact_context`, `_mcp_tiny_context_text`, `_format`, `_project_read_first_item`, `_git_summary`, `_sanitize_ollama_advisory`, `_stable_context_projection`, `_stable_hash`, `_resolve_reference`, `_line_hint_from_reason`, `_add_candidate`, `_group_line_hints_for_guidance`, `_agent_guidance`, `_term_score`, `_heading_words`, `_heading_match_score`, `_best_heading_section`, `_score_index_and_symbol_hits`, `_score_batched_search`, `_resolve_explicit_paths`, `_apply_retrieval_boost`, `_select_read_first`, `_build_excerpt_ranges`, `_run_ollama_ranking`, `_hint_sort_key`, `_compact_line_hints`, plus module constants `LINE_HINT_MAX_PER_FILE`, `READ_FIRST_MAX`, `_MILESTONE_RE`, `_MAX_SECTION_LINES`.
- Consumes (from Task 7): `from ._shared import json_out, _format_model_timing, _slim_status_for_nesting`.

- [ ] **Step 1: Write `memory.py`**

Create `neo_localmcp/mcp_commands/memory.py` with this import header:

```python
from __future__ import annotations

import hashlib
import json
import re
import uuid
from pathlib import Path
from typing import Any

from .. import __version__
from .. import repo_memory
from ..query import category_boost, classify_path, extract_file_references, normalize_query, term_key as compute_term_key
from ..utils import repo_root_or_cwd, rg_search
from ._shared import _format_model_timing, _slim_status_for_nesting, json_out
```

Note what is dropped versus `tools.py`'s original header: `chat` (from `ollama_client`) is still needed here (used by `_run_ollama_ranking`) — add it: `from ..ollama_client import chat`. `read_text_file`, `rel`, `safe_path`, `sha256_file`, `run_command` are NOT needed in `memory.py` (only used by `summarize_file`/`apply_unified_patch`, which move to `editing.py` in Task 10). `installer_configure_models`/`ollama_state`/`ensure_ollama`/`start_service`/`stop_service`/`unload_ollama` are NOT needed here (only used by `set_ollama`/`ollama_status`/`ollama_ensure`/`ollama_control`, which move to `ollama.py` in Task 11). `CONFIG_PATH`/`ensure_config`/`load_config`/`save_config`/`IDENTITY` are NOT needed here (used by `system.py`'s functions, already moved in Task 8). So the correct final import block is:

```python
from __future__ import annotations

import hashlib
import json
import re
import uuid
from pathlib import Path
from typing import Any

from .. import __version__
from .. import repo_memory
from ..ollama_client import chat
from ..query import category_boost, classify_path, extract_file_references, normalize_query, term_key as compute_term_key
from ..utils import repo_root_or_cwd, rg_search
from ._shared import _format_model_timing, _slim_status_for_nesting, json_out
```

Then, verbatim (unchanged bodies), the following — moved from `tools.py` at the given original line ranges:

- Module constants (was lines 21-22): `LINE_HINT_MAX_PER_FILE = 5`, `READ_FIRST_MAX = 5`
- `_hint_sort_key` (was lines 56-63)
- `_compact_line_hints` (was lines 66-84)
- `_project_read_first_item` (was lines 87-103)
- `_git_summary` (was lines 106-110)
- `_render_context_text` (was lines 113-166)
- `_mcp_compact_context` (was lines 170-219) — note this function references `IDENTITY.product_name`; since `IDENTITY` is not imported here per the pruned header above, add it: the import block needs `from ..identity import IDENTITY` added. Final import block:

```python
from __future__ import annotations

import hashlib
import json
import re
import uuid
from pathlib import Path
from typing import Any

from .. import __version__
from .. import repo_memory
from ..identity import IDENTITY
from ..ollama_client import chat
from ..query import category_boost, classify_path, extract_file_references, normalize_query, term_key as compute_term_key
from ..utils import repo_root_or_cwd, rg_search
from ._shared import _format_model_timing, _slim_status_for_nesting, json_out
```

- `_sanitize_ollama_advisory` (was lines 222-255)
- `_mcp_tiny_context_text` (was lines 258-338) — references `__version__` (already imported above)
- `_format` (was lines 340-347)
- `_stable_context_projection` (was lines 430-442)
- `_stable_hash` (was lines 445-447)
- `test_determinism` (was lines 450-498) — calls `context_prepare` (defined later in this same file, forward reference is fine since it's called from inside a function body, not at module load time)
- `file_context` (was lines 509-510)
- `file_excerpts` (was lines 513-521)
- `_resolve_reference` (was lines 524-531)
- `_line_hint_from_reason` (was lines 534-542)
- `_add_candidate` (was lines 545-568)
- `_group_line_hints_for_guidance` (was lines 571-573)
- `_agent_guidance` (was lines 576-591)
- `_term_score` (was lines 594-595)
- Module constants `_MILESTONE_RE = re.compile(r"^[A-Za-z]\d+(?:\.\d+)*$")` and `_MAX_SECTION_LINES = 80` (was lines 598-600)
- `_heading_words` (was lines 603-604)
- `_heading_match_score` (was lines 607-622)
- `_best_heading_section` (was lines 625-654)
- `_score_index_and_symbol_hits` (was lines 657-690)
- `_score_batched_search` (was lines 693-724)
- `_resolve_explicit_paths` (was lines 727-743)
- `_apply_retrieval_boost` (was lines 746-766)
- `_select_read_first` (was lines 769-798)
- `_build_excerpt_ranges` (was lines 801-846)
- `_run_ollama_ranking` (was lines 849-876)
- `context_prepare` (was lines 879-987)
- `prepare_context` (was lines 990-992)
- `record_change` (was lines 1133-1134)

Copy every one of these function/constant bodies **exactly** as they appear in the current `neo_localmcp/tools.py` (available to read at that path until Task 12 deletes it) — do not paraphrase, reformat, or "improve" anything. The only allowed change anywhere in this file is the import header above.

- [ ] **Step 2: Compile-check**

```bash
python -m compileall -q neo_localmcp/mcp_commands
```

- [ ] **Step 3: Sanity-check for accidental omissions**

```bash
python -c "
import ast
old = ast.parse(open('neo_localmcp/tools.py').read())
new = ast.parse(open('neo_localmcp/mcp_commands/memory.py').read())
old_names = {n.name for n in ast.walk(old) if isinstance(n, (ast.FunctionDef,))}
new_names = {n.name for n in ast.walk(new) if isinstance(n, (ast.FunctionDef,))}
expected = {
    '_hint_sort_key','_compact_line_hints','_project_read_first_item','_git_summary',
    '_render_context_text','_mcp_compact_context','_sanitize_ollama_advisory',
    '_mcp_tiny_context_text','_format','_stable_context_projection','_stable_hash',
    'test_determinism','file_context','file_excerpts','_resolve_reference',
    '_line_hint_from_reason','_add_candidate','_group_line_hints_for_guidance',
    '_agent_guidance','_term_score','_heading_words','_heading_match_score',
    '_best_heading_section','_score_index_and_symbol_hits','_score_batched_search',
    '_resolve_explicit_paths','_apply_retrieval_boost','_select_read_first',
    '_build_excerpt_ranges','_run_ollama_ranking','context_prepare','prepare_context',
    'record_change',
}
missing = expected - new_names
assert not missing, f'missing from memory.py: {missing}'
print('OK: all', len(expected), 'expected functions present in memory.py')
"
```

Expected output: `OK: all 33 expected functions present in memory.py` (the `expected` set lists 33 names; `_mcp_compact_context`'s nested inner `compact_item` also appears in both `old_names` and `new_names` but is deliberately not in `expected`, so it doesn't affect the `missing` check).

- [ ] **Step 4: Commit**

```bash
git add neo_localmcp/mcp_commands/memory.py
git commit -m "refactor(mcp-commands): add memory.py (context_prepare/prepare_context/file_excerpts/record_change/test_determinism)"
```

(Not wired into `server.py`/`cli.py`/`context_worker.py`/`benchmark.py` yet — Task 12.)

---

### Task 10: Create `neo_localmcp/mcp_commands/editing.py`

**Why:** `summarize_file` and `apply_unified_patch` are both "operate on file content" tools — chosen over the originally-proposed `misc.py` name because "misc" hides what the file does.

**Files:**
- Create: `neo_localmcp/mcp_commands/editing.py`

**Interfaces:**
- Produces: `summarize_file(path: str, repo_root: str = "auto", model: str | None = None, heading: str | None = None) -> str`, `apply_unified_patch(patch_text: str, repo_root: str = "auto", check_only: bool = False) -> str` — identical signatures to today's `tools.*` equivalents. Also produces (private): `_cap_keyword_terms`, `_split_summary_keywords`, `_summarize_section`.
- Consumes (from Task 7): `from ._shared import _format_model_timing, _slim_status_for_nesting, json_out`.

**Test-coupling note:** `tests/test_retrieval_memory.py` does `monkeypatch.setattr(tools, "chat", fake_chat)` thirteen times to intercept `summarize_file`'s internal Ollama call. After this move, that patch target must become `monkeypatch.setattr(editing, "chat", fake_chat)` — fixed in Task 12, Step 5, since that's where the rest of that test file's `tools` references also get updated (keeping all edits to one test file in one task avoids a half-updated intermediate state).

- [ ] **Step 1: Write `editing.py`**

Create `neo_localmcp/mcp_commands/editing.py`:

```python
from __future__ import annotations

import json
import re
import tempfile
from pathlib import Path

from .. import repo_memory
from ..config import load_config
from ..ollama_client import chat
from ..utils import read_text_file, rel, run_command, safe_path, sha256_file
from ._shared import _format_model_timing, _slim_status_for_nesting, json_out


def _cap_keyword_terms(raw: str, max_terms: int = 8) -> str:
    """Cap by comma-separated term count, not character count.

    A generation-length cap (see ollama_client.chat's num_predict) is the primary
    defense against a runaway response, but this is the belt-and-suspenders layer:
    even a response that stays under the token cap could still cram far more than
    the requested "at most 8" terms into a shorter space. Enforce the actual shape
    regardless of what the model produced.
    """
    terms = [t.strip() for t in raw.split(",") if t.strip()]
    return ", ".join(terms[:max_terms])


def _split_summary_keywords(text: str) -> tuple[str, str]:
    """Best-effort split of a 'summary: ...\\nkeywords: ...' style Ollama response."""
    summary_match = re.search(r"summary\s*:\s*(.+?)(?:\n\s*keywords\s*:|\Z)", text, re.IGNORECASE | re.DOTALL)
    keywords_match = re.search(r"keywords\s*:\s*(.+)", text, re.IGNORECASE | re.DOTALL)
    summary = (summary_match.group(1).strip() if summary_match else text.strip())[:2000]
    keywords_raw = (keywords_match.group(1).strip() if keywords_match else "")[:4000]
    keywords = _cap_keyword_terms(keywords_raw)
    return summary, keywords


def _summarize_section(path: str, heading: str, root: Path, model: str | None) -> str:
    sym = repo_memory.find_heading_symbol(path, heading, root)
    if not sym:
        return json_out({"ok": False, "error": f"heading not found: {heading}", "path": path})
    current_hash = sha256_file(safe_path(path, root))
    cached = repo_memory.get_section_summary(path, heading, root)
    if cached and cached.get("source_hash") == current_hash and (not model or cached.get("model") == model):
        return json_out({
            "file": cached.get("file_path"), "heading": heading, "cached": True,
            "start_line": cached.get("start_line"), "end_line": cached.get("end_line"),
            "summary": cached.get("summary"), "keywords": cached.get("keywords"),
            "model": cached.get("model"), "prompt_version": cached.get("prompt_version"),
        })
    excerpt_data = repo_memory.file_excerpts([{"path": path, "start_line": sym["start_line"], "end_line": sym["end_line"]}], root, max_chars=12_000)
    section_text = ((excerpt_data.get("excerpts") or [{}])[0]).get("text", "")
    prompt = f"""
Summarize this single document section for repository working context. Do not write or suggest source code.
Return exactly two labeled parts:
summary: one or two factual sentences describing what this section covers
keywords: a short comma-separated list of section-specific terms, at most 8

Section heading: {sym.get('signature') or heading}
Section text:
{section_text}
""".strip()
    num_predict = int(load_config().get("ollama", {}).get("section_summary_num_predict", 400))
    result = chat(prompt, model=model, purpose="summary", num_predict=num_predict)
    eval_count = (result.get("raw") or {}).get("eval_count")
    # Ollama stops generation at exactly num_predict tokens when the cap is what ended
    # the response rather than the model choosing to stop -- a reliable runaway signal.
    truncated = bool(result.get("ok") and eval_count is not None and int(eval_count) >= num_predict)
    stored = None
    if result.get("ok") and result.get("response") and not truncated:
        summary_text, keywords_text = _split_summary_keywords(str(result["response"]))
        stored = repo_memory.store_section_summary(path, heading, int(sym["start_line"]), int(sym["end_line"]), summary_text, keywords_text, str(result.get("model") or model or ""), "section-summary-v1", root)
    full_status = result.get("ollama_status")
    result_for_nesting = {**result, "ollama_status": _slim_status_for_nesting(full_status)}
    return json_out({
        "file": stored.get("path") if stored else path, "heading": heading, "cached": False, "truncated": truncated,
        "start_line": sym["start_line"], "end_line": sym["end_line"],
        "ollama_summary": result_for_nesting, "ollama_timing": _format_model_timing(result), "ollama_status": full_status,
        "stored": stored,
    })


def summarize_file(path: str, repo_root: str = "auto", model: str | None = None, heading: str | None = None) -> str:
    from ..utils import repo_root_or_cwd
    root = repo_root_or_cwd(repo_root)
    if heading:
        # P6 (1.0.6): section-scoped enrichment. This never determines a heading's
        # line boundaries -- those stay authoritative from the deterministic
        # extractor -- it only adds cached, keyword-searchable summary text.
        return _summarize_section(path, heading, root, model)
    p = safe_path(path, root)
    ctx = repo_memory.file_context(rel(p, root), root)
    text = read_text_file(p, int(load_config().get("repo", {}).get("summary_max_chars", 80_000)))
    prompt = f"""
Summarize this file for repository working context. Do not write or suggest source code.
Return:
- purpose
- important symbols
- external dependencies
- likely related files
- risk areas
- confidence

File context:
{json.dumps(ctx, indent=2, default=str)[:20000]}

Current source file:
{text}
""".strip()
    result = chat(prompt, model=model, purpose="summary")
    if result.get("ok") and result.get("response"):
        repo_memory.store_summary(rel(p, root), result["response"], str(result.get("model") or model or ""), "file-summary-v1", root)
    full_status = result.get("ollama_status")
    result_for_nesting = {**result, "ollama_status": _slim_status_for_nesting(full_status)}
    return json_out({"file": rel(p, root), "context": ctx, "ollama_summary": result_for_nesting, "ollama_timing": _format_model_timing(result), "ollama_status": full_status})


def apply_unified_patch(patch_text: str, repo_root: str = "auto", check_only: bool = False) -> str:
    from ..utils import repo_root_or_cwd
    root = repo_root_or_cwd(repo_root)
    if not patch_text.strip():
        return json_out({"ok": False, "error": "patch_text is empty"})
    with tempfile.NamedTemporaryFile("w", delete=False, suffix=".patch", encoding="utf-8", newline="") as tmp:
        tmp.write(patch_text)
        patch_path = Path(tmp.name)
    try:
        check = run_command(["git", "apply", "--check", str(patch_path)], cwd=root, timeout=30)
        if check["returncode"] != 0:
            return json_out({"ok": False, "stage": "check", "stdout": check["stdout"], "stderr": check["stderr"]})
        if check_only:
            return json_out({"ok": True, "check_only": True, "message": "Patch applies cleanly. No files changed."})
        apply_result = run_command(["git", "apply", str(patch_path)], cwd=root, timeout=30)
        if apply_result["returncode"] != 0:
            return json_out({"ok": False, "stage": "apply", "stdout": apply_result["stdout"], "stderr": apply_result["stderr"]})
        changed = run_command(["git", "diff", "--name-only"], cwd=root, timeout=20)
        paths = [p.strip() for p in changed["stdout"].splitlines() if p.strip()]
        update = repo_memory.record_change("Applied exact approved unified patch", paths, root)
        return json_out({"ok": True, "changed_paths": paths, "memory_update": update})
    finally:
        try:
            patch_path.unlink(missing_ok=True)
        except Exception:
            pass
```

Note: `repo_root_or_cwd` is imported inline inside `summarize_file`/`apply_unified_patch` above rather than at module top — clean this up by moving it to the top-level import instead, since there's no cycle risk here (unlike `doctor()`'s `lifecycle` import in Task 8, which has a documented reason to stay inline). Use this top-level import line instead, and drop both inline imports:

```python
from ..utils import read_text_file, rel, repo_root_or_cwd, run_command, safe_path, sha256_file
```

- [ ] **Step 2: Compile-check**

```bash
python -m compileall -q neo_localmcp/mcp_commands
```

- [ ] **Step 3: Commit**

```bash
git add neo_localmcp/mcp_commands/editing.py
git commit -m "refactor(mcp-commands): add editing.py (summarize_file/apply_unified_patch)"
```

---

### Task 11: Create `neo_localmcp/mcp_commands/ollama.py`

**Why:** `set_ollama`, `ollama_status`, `ollama_ensure`, `ollama_control` are the MCP/CLI-facing Ollama surface — distinct from `ollama_client.py` (daemon RPC primitives) and `installer/ollama.py` (lifecycle-scoped config), per the design doc's disambiguation.

**Files:**
- Create: `neo_localmcp/mcp_commands/ollama.py`

**Interfaces:**
- Produces: `set_ollama(base_url: str | None = None, summary_model: str | None = None, fast_model: str | None = None, num_ctx: int | None = None) -> str`, `ollama_status(model: str | None = None, purpose: str = "ranking") -> str`, `ollama_ensure(model: str | None = None, purpose: str = "ranking") -> str`, `ollama_control(action: str, model: str | None = None, purpose: str = "ranking") -> str` — identical signatures to today's `tools.*` equivalents.
- Consumes (from Task 7): `from ._shared import json_out`.

- [ ] **Step 1: Write `ollama.py`**

Create `neo_localmcp/mcp_commands/ollama.py`:

```python
from __future__ import annotations

from ..installer import configure_models as installer_configure_models
from ..ollama_client import chat, ensure as ensure_ollama, start_service, status as ollama_state, stop_service, unload as unload_ollama, warm as warm_ollama
from ._shared import json_out


def set_ollama(base_url: str | None = None, summary_model: str | None = None, fast_model: str | None = None, num_ctx: int | None = None) -> str:
    ollama_cfg = installer_configure_models(
        base_url=base_url, fast_model=fast_model, summary_model=summary_model, num_ctx=num_ctx,
    )
    return json_out({"ok": True, "ollama": ollama_cfg, "status": ollama_state()})


def ollama_status(model: str | None = None, purpose: str = "ranking") -> str:
    return json_out(ollama_state(model, purpose))


def ollama_ensure(model: str | None = None, purpose: str = "ranking") -> str:
    return json_out(ensure_ollama(model, purpose))


def ollama_control(action: str, model: str | None = None, purpose: str = "ranking") -> str:
    actions = {
        "status": lambda: ollama_state(model, purpose),
        "ensure": lambda: ensure_ollama(model, purpose),
        "start": start_service,
        "warm": lambda: warm_ollama(model, purpose),
        "unload": lambda: unload_ollama(model, purpose),
        "stop": stop_service,
        "test": lambda: chat("Reply with exactly: ok", model=model, purpose=purpose),
    }
    if action not in actions:
        return json_out({"ok": False, "error": f"unknown Ollama action: {action}"})
    return json_out(actions[action]())
```

- [ ] **Step 2: Compile-check**

```bash
python -m compileall -q neo_localmcp/mcp_commands
```

- [ ] **Step 3: Commit**

```bash
git add neo_localmcp/mcp_commands/ollama.py
git commit -m "refactor(mcp-commands): add ollama.py (set_ollama/ollama_status/ollama_ensure/ollama_control)"
```

---

### Task 12: Delete `tools.py`, repoint every caller

**Why:** All four category files now exist with verified-complete content (Tasks 8-11); this task is the atomic cutover — delete the monolith and fix every real caller in one pass so there is never a half-migrated state where some code imports `tools` and other code imports `mcp_commands`.

**Files:**
- Delete: `neo_localmcp/tools.py`
- Modify: `neo_localmcp/server.py` (imports + 8 call sites)
- Modify: `neo_localmcp/cli.py` (imports + 19 call sites)
- Modify: `neo_localmcp/context_worker.py` (import + 1 call site)
- Modify: `neo_localmcp/benchmark.py` (import + 6 call sites)
- Modify: `neo_localmcp/installer/verification.py` (one string literal, `_DOCTOR_SNIPPET`)
- Modify: `tests/test_context.py` (import + all call sites)
- Modify: `tests/test_retrieval_memory.py` (import + all call sites + monkeypatch targets)

**Interfaces:** No new interfaces — this task only repoints existing callers to the functions Tasks 8-11 already produced.

- [ ] **Step 1: Delete `tools.py`**

```bash
git rm neo_localmcp/tools.py
```

(Before running this, double check every function it contained has a home: cross-reference against the "Interfaces: Produces" lists in Tasks 8, 9, 10, 11 — 11 + 32 + 2 + 4 = every public function accounted for, plus the 3 shared helpers from Task 7. If anything is missing, stop and add it to the appropriate category file before proceeding.)

- [ ] **Step 2: Fix `server.py`**

Change the import (was line 15):

```python
from . import tools
```

to:

```python
from .mcp_commands import editing, memory, ollama, system
```

Then update each call site:

| Line (was) | Old | New |
|---|---|---|
| 145 | `return tools.file_excerpts(ranges, root, max_chars, retrieval_id)` | `return memory.file_excerpts(ranges, root, max_chars, retrieval_id)` |
| 152 | `return tools.repo_lookup(query, await _resolve_repo_root(repo_root, ctx), limit)` | `return system.repo_lookup(query, await _resolve_repo_root(repo_root, ctx), limit)` |
| 159 | `return tools.record_change(summary, paths, await _resolve_repo_root(repo_root, ctx))` | `return memory.record_change(summary, paths, await _resolve_repo_root(repo_root, ctx))` |
| 166 | `return tools.status(await _resolve_repo_root(repo_root, ctx))` | `return system.status(await _resolve_repo_root(repo_root, ctx))` |
| 173 | `return tools.doctor(await _resolve_repo_root(repo_root, ctx))` | `return system.doctor(await _resolve_repo_root(repo_root, ctx))` |
| 180 | `return tools.repo_refresh(await _resolve_repo_root(repo_root, ctx), max_files, force)` | `return system.repo_refresh(await _resolve_repo_root(repo_root, ctx), max_files, force)` |
| 187 | `return tools.summarize_file(path, await _resolve_repo_root(repo_root, ctx), model, heading)` | `return editing.summarize_file(path, await _resolve_repo_root(repo_root, ctx), model, heading)` |
| 194 | `return tools.apply_unified_patch(patch_text, await _resolve_repo_root(repo_root, ctx), check_only)` | `return editing.apply_unified_patch(patch_text, await _resolve_repo_root(repo_root, ctx), check_only)` |
| 200 | `return tools.ollama_status(model, purpose)` | `return ollama.ollama_status(model, purpose)` |
| 206 | `return tools.ollama_ensure(model, purpose)` | `return ollama.ollama_ensure(model, purpose)` |

(`_context_prepare_worker` at line 81 does not call `tools.*` directly — it subprocess-invokes `neo_localmcp.context_worker`, fixed separately in Step 4 below — no change needed in `server.py` for that function.)

- [ ] **Step 3: Fix `cli.py`**

Change the import (was line 8):

```python
from . import tools
```

to:

```python
from .mcp_commands import editing, memory, ollama, system
```

And (was line 9):

```python
from .benchmark import run_benchmark
```

stays as-is for now (Task 13 renames `benchmark.py` → `benchmarker/`; don't touch this line in this task).

Then update each call site:

| Function (was line) | Old | New |
|---|---|---|
| `cmd_init` (20) | `tools.init()` | `system.init()` |
| `cmd_status` (25) | `tools.status(args.repo_root)` | `system.status(args.repo_root)` |
| `cmd_doctor` (30) | `tools.doctor(args.repo_root)` | `system.doctor(args.repo_root)` |
| `cmd_where` (35) | `tools.where(args.repo_root)` | `system.where(args.repo_root)` |
| `cmd_model_status` (40) | `tools.model_status()` | `system.model_status()` |
| `cmd_set_ollama` (109) | `tools.set_ollama(args.base_url, args.summary_model, args.fast_model, args.num_ctx)` | `ollama.set_ollama(args.base_url, args.summary_model, args.fast_model, args.num_ctx)` |
| `cmd_index` (114) | `tools.repo_index(args.repo_root, max_files=args.max_files, force=args.force)` | `system.repo_index(args.repo_root, max_files=args.max_files, force=args.force)` |
| `cmd_refresh` (119) | `tools.repo_refresh(args.repo_root, max_files=args.max_files, force=args.force)` | `system.repo_refresh(args.repo_root, max_files=args.max_files, force=args.force)` |
| `cmd_reindex` (124) | `tools.repo_reindex(args.repo_root, max_files=args.max_files)` | `system.repo_reindex(args.repo_root, max_files=args.max_files)` |
| `cmd_reset_repo` (132) | `tools.reset_repo(args.repo_root)` | `system.reset_repo(args.repo_root)` |
| `cmd_reset_all` (140) | `tools.reset_all()` | `system.reset_all()` |
| `cmd_test_determinism` (145) | `tools.test_determinism(...)` | `memory.test_determinism(...)` |
| `cmd_lookup` (160) | `tools.repo_lookup(args.query, args.repo_root, args.limit)` | `system.repo_lookup(args.query, args.repo_root, args.limit)` |
| `cmd_file` (165) | `tools.file_context(args.path, args.repo_root, args.around_line, args.context_lines)` | `memory.file_context(args.path, args.repo_root, args.around_line, args.context_lines)` |
| `cmd_context` (171) | `tools.prepare_context(...)` | `memory.prepare_context(...)` |
| `cmd_ollama` (176) | `tools.ollama_control(args.ollama_action, getattr(args, "model", None), getattr(args, "purpose", "ranking"))` | `ollama.ollama_control(args.ollama_action, getattr(args, "model", None), getattr(args, "purpose", "ranking"))` |
| `cmd_summarize` (181) | `tools.summarize_file(args.path, args.repo_root, args.model, args.heading)` | `editing.summarize_file(args.path, args.repo_root, args.model, args.heading)` |
| `cmd_apply_patch` (187) | `tools.apply_unified_patch(patch_text, args.repo_root, check_only=args.check_only)` | `editing.apply_unified_patch(patch_text, args.repo_root, check_only=args.check_only)` |
| `cmd_record_change` (192) | `tools.record_change(args.summary, args.paths, args.repo_root)` | `memory.record_change(args.summary, args.paths, args.repo_root)` |

- [ ] **Step 4: Fix `context_worker.py`**

Change:

```python
from . import tools
```

to:

```python
from .mcp_commands import memory
```

And:

```python
        result = tools.context_prepare(
```

to:

```python
        result = memory.context_prepare(
```

- [ ] **Step 5: Fix `benchmark.py`**

Change:

```python
from . import repo_memory, tools
```

to:

```python
from . import repo_memory
from .mcp_commands import memory, ollama, system
```

Then update each call site:

| Function | Old | New |
|---|---|---|
| `_sys_checks` | `tools.doctor(root_str)`, `tools.status(root_str)`, `tools.where(root_str)`, `tools.model_status()` | `system.doctor(root_str)`, `system.status(root_str)`, `system.where(root_str)`, `system.model_status()` |
| `_run_query_check` | `tools.test_determinism(task, str(root), runs=5, record=False)` | `memory.test_determinism(task, str(root), runs=5, record=False)` |
| `_run_query_check` | `tools.prepare_context(task, str(root), output_format="json", record=False)` | `memory.prepare_context(task, str(root), output_format="json", record=False)` |
| `_ollama_checks` | `tools.ollama_status()` | `ollama.ollama_status()` |
| `_ollama_checks` | `tools.ollama_ensure()` | `ollama.ollama_ensure()` |

Also update the comment at (was line 397): `"# same precedent as mcpb_build.py's _next_free_path"` → `"# same precedent as installer/mcpb.py's _next_free_path"` (accuracy, since Task 1 already moved that file).

- [ ] **Step 6: Fix `installer/verification.py`'s embedded snippet**

Change:

```python
_DOCTOR_SNIPPET = "import sys; from neo_localmcp.tools import doctor; sys.stdout.write(doctor())"
```

to:

```python
_DOCTOR_SNIPPET = "import sys; from neo_localmcp.mcp_commands.system import doctor; sys.stdout.write(doctor())"
```

This snippet is executed as a subprocess script inside a candidate/managed venv during post-install verification (`_check_doctor`) — it must reference the doctor() function's real new location, or every install's verification step would start failing with an `ImportError`.

- [ ] **Step 7: Fix `tests/test_context.py`**

Change:

```python
from neo_localmcp import repo_memory, tools
```

to:

```python
from neo_localmcp import repo_memory
from neo_localmcp.mcp_commands import memory
```

Then replace every `tools.prepare_context(...)` and `tools.test_determinism(...)` call site with `memory.prepare_context(...)` / `memory.test_determinism(...)` respectively (mechanical prefix swap; there are ~15 call sites, all `tools.prepare_context(` or `tools.test_determinism(`).

- [ ] **Step 8: Fix `tests/test_retrieval_memory.py`**

Change:

```python
from neo_localmcp import repo_memory, tools
```

to:

```python
from neo_localmcp import repo_memory
from neo_localmcp.mcp_commands import editing, memory
```

Then:
- Every `tools.prepare_context(...)` → `memory.prepare_context(...)`
- Every `tools.file_excerpts(...)` → `memory.file_excerpts(...)`
- Every `tools.test_determinism(...)` → `memory.test_determinism(...)`
- Every `tools.summarize_file(...)` → `editing.summarize_file(...)`
- Every `monkeypatch.setattr(tools, "chat", ...)` → `monkeypatch.setattr(editing, "chat", ...)` (13 occurrences — these intercept `summarize_file`'s internal Ollama call, which now lives in `editing.py`)
- The comment `# structural milestone boost defined in tools.py` → `# structural milestone boost defined in mcp_commands/memory.py`

- [ ] **Step 9: Run the full affected test surface**

```bash
python -m pytest -q tests/test_context.py tests/test_retrieval_memory.py tests/test_benchmark.py -v
```

Expected: all PASS.

```bash
python -m pytest -q tests/installer/test_verification.py -v
```

Expected: all PASS (this exercises the `_DOCTOR_SNIPPET` fix from Step 6).

```bash
python -m pytest -q -m "not slow" --deselect tests/test_distribution.py::test_built_mcpb_embeds_current_package_bytes
```

Expected: full fast suite PASSES. (The `--deselect` skips the stale-bundle byte-check, regenerated + run for real in Task 14 — see the same note in Task 6, Step 5. The bundle is still stale here because Tasks 7-12 changed `neo_localmcp/` further.)

- [ ] **Step 10: Compile-check**

```bash
python -m compileall -q neo_localmcp setup.py setup_wizard.py
```

- [ ] **Step 11: Manual smoke test (per `CLAUDE.md`)**

```bash
python -m neo_localmcp.cli doctor
python -m neo_localmcp.cli context "debug repository indexing: index_repo, refresh" --repo-root . --token-budget 1000
```

Expected: both commands produce their normal JSON/text output with no `ImportError`/`ModuleNotFoundError`.

- [ ] **Step 12: Commit**

```bash
git add -A
git commit -m "refactor(mcp-commands): delete tools.py, repoint server/cli/context_worker/benchmark/tests to mcp_commands/*"
```

**Checkpoint:** The `tools.py` split (spec section "MCP tool/command layer") is now complete and verified. Task 13 (benchmarker rename) is independent and can proceed next.

---

### Task 13: Rename `benchmark.py` + `benchmark_queries/` → `benchmarker/`

**Why:** Colocates the runner with its fixture data under one package name, matching the "benchmarker" concept from the design.

**Critical gotcha:** `benchmark.py`'s `_default_queries_path()` does `Path(__file__).resolve().parent / "benchmark_queries" / "default.jsonl"`. Since both the file and its data directory move down one level together (from `neo_localmcp/` to `neo_localmcp/benchmarker/`), the *relative* relationship is unchanged — only the folder name string changes, no `.parent` depth change needed.

**Files:**
- Move: `neo_localmcp/benchmark.py` → `neo_localmcp/benchmarker/__init__.py`
- Move: `neo_localmcp/benchmark_queries/` → `neo_localmcp/benchmarker/queries/`
- Modify: `neo_localmcp/cli.py` (import line only)
- Modify: `tests/test_benchmark.py` (import line only)
- Modify: `pyproject.toml` (`package-data` glob)

**Interfaces:**
- Produces: `neo_localmcp.benchmarker.run_benchmark(groups: list[str], repo_root: str = "auto", out_dir: str | None = None, queries_path: str | None = None) -> dict[str, Any]`, `.resolve_groups(requested: list[str]) -> list[str]`, `.GROUPS: dict[str, Callable[[Path, dict[str, Any]], list[CheckResult]]]` — identical to today's `neo_localmcp.benchmark` equivalents, just at the new module path.

- [ ] **Step 1: Move the directory and file**

```bash
mkdir -p neo_localmcp/benchmarker
git mv neo_localmcp/benchmark.py neo_localmcp/benchmarker/__init__.py
git mv neo_localmcp/benchmark_queries neo_localmcp/benchmarker/queries
```

- [ ] **Step 2: Update the queries-path folder name**

In `neo_localmcp/benchmarker/__init__.py`, change:

```python
def _default_queries_path() -> Path:
    return Path(__file__).resolve().parent / "benchmark_queries" / "default.jsonl"
```

to:

```python
def _default_queries_path() -> Path:
    return Path(__file__).resolve().parent / "queries" / "default.jsonl"
```

- [ ] **Step 3: Update `benchmarker/__init__.py`'s own imports for the new depth**

This file was `neo_localmcp/benchmark.py` (direct child of `neo_localmcp/`); it is now `neo_localmcp/benchmarker/__init__.py` (one level deeper). Its imports change from:

```python
from . import repo_memory
from .mcp_commands import memory, ollama, system
from .utils import git_info, repo_root_or_cwd
```

to:

```python
from .. import repo_memory
from ..mcp_commands import memory, ollama, system
from ..utils import git_info, repo_root_or_cwd
```

(This assumes Task 12 already repointed this file's `tools` import to `mcp_commands` — confirm that edit is present before making this depth change; if Task 12 was somehow skipped, do both edits together here.)

- [ ] **Step 4: Update `cli.py`'s import**

Change:

```python
from .benchmark import run_benchmark
```

to:

```python
from .benchmarker import run_benchmark
```

- [ ] **Step 5: Update `tests/test_benchmark.py`'s import**

Change:

```python
from neo_localmcp import benchmark
```

to:

```python
from neo_localmcp import benchmarker as benchmark
```

(Aliasing to `benchmark` keeps every call site in this test file — `benchmark.resolve_groups(...)`, `benchmark.GROUPS`, etc. — unchanged, since only the import needs to move; there's no value in touching 20+ call sites in this file for a pure rename.)

- [ ] **Step 6: Update `pyproject.toml`'s package-data glob**

Change:

```toml
[tool.setuptools.package-data]
neo_localmcp = ["neo.toml", "templates/claude-code/commands/neo-localmcp/*.md", "benchmark_queries/*.jsonl"]
```

to:

```toml
[tool.setuptools.package-data]
neo_localmcp = ["neo.toml", "templates/claude-code/commands/neo-localmcp/*.md", "benchmarker/queries/*.jsonl"]
```

- [ ] **Step 7: Run the affected tests**

```bash
python -m pytest -q tests/test_benchmark.py -v
```

Expected: all PASS.

```bash
python -m neo_localmcp.cli benchmark sys --repo-root .
```

Expected: runs to completion, writes a report under `./neo-localmcp_benchmarks/`, no `ImportError`.

- [ ] **Step 8: Compile-check**

```bash
python -m compileall -q neo_localmcp setup.py setup_wizard.py
```

- [ ] **Step 9: Full fast suite**

```bash
python -m pytest -q -m "not slow" --deselect tests/test_distribution.py::test_built_mcpb_embeds_current_package_bytes
```

Expected: all PASS. (Last checkpoint with the stale-bundle deselect — Task 14 regenerates the bundle and runs this test for real. This is the final `neo_localmcp/` change, so the bundle rebuilt in Task 14 will be current.)

- [ ] **Step 10: Commit**

```bash
git add -A
git commit -m "refactor(benchmark): rename benchmark.py + benchmark_queries/ to benchmarker/ package"
```

---

### Task 14: Update `CLAUDE.md`'s module map + final full verification

**Why:** `CLAUDE.md`'s "Module map" section is the authoritative documented structure per this repo's own convention — it must match reality, or it goes stale the moment this PR merges.

**Files:**
- Modify: `CLAUDE.md` (Module map section)
- Modify: `README.md:196-199` (review-pass addition — see Step 2)
- Modify: `PROJECT_STATUS.md` (one status line, per repo convention: "should be updated at the end of any session that changes verified behavior")
- Modify: `PROJECT_NOTES.md` (append one dated entry — do not edit prior entries, it's an append-only log)

**Interfaces:** Documentation only, no code interfaces.

- [ ] **Step 1: Rewrite `CLAUDE.md`'s Module map section**

Read the current "## Module map (`neo_localmcp/`)" section and replace it with an accurate description of the post-reorg layout: `server.py` (unchanged description), `mcp_commands/system.py`+`memory.py`+`ollama.py`+`editing.py`+`_shared.py` (replacing the single `tools.py` bullet, one line per file describing its category), `cli.py` (unchanged description, note it's now unambiguous since the installer CLI moved), `repo_memory.py`/`ollama_client.py`/`lifecycle.py`/`client_setup.py`/`config.py` (unchanged descriptions — these did not move), `installer/` (new bullet: the lifecycle package, now also home to `cli.py` — the installer CLI frontend — and `wizard/` — the interactive UI frontend, plus `mcpb.py`), `benchmarker/` (new bullet, replacing any `benchmark.py` mention if one exists), `query.py`/`identity.py` (unchanged). Do not describe internal `installer/` submodules exhaustively here — that level of detail belongs in the design spec, not this always-loaded file; keep each bullet to 1-2 sentences, matching the existing style of every other bullet in this section.

- [ ] **Step 2: Fix `README.md`'s stale `--fake` mention (review-pass addition, Task 5)**

Task 5's review found this line was never scheduled for a fix by any task — it's the one genuinely live, user-facing doc reference to the old flag/env-var names (as opposed to `PROJECT_STATUS.md`'s several `--fake` mentions, which are dated historical status entries describing verification that happened on 2026-07-03 under the old flag name, and are correctly left untouched per this repo's convention against rewriting historical log entries). `README.md` lines 196-199 currently read:

```markdown
The wizard is plain-stdlib — there is no UI toolkit to install. If you run
`python setup_wizard.py` on a bare clone and `psutil` is missing, the wizard
detects it and offers to install it for you before starting. Add `--fake` to walk
the entire flow as a safe simulation that touches nothing on disk
(`NEO_LOCALMCP_WIZARD_FAKE_STATE=healthy` simulates a returning, already-installed
user).
```

Change to:

```markdown
The wizard is plain-stdlib — there is no UI toolkit to install. If you run
`python setup_wizard.py` on a bare clone and `psutil` is missing, the wizard
detects it and offers to install it for you before starting. Add `--preview` to walk
the entire flow as a safe simulation that touches nothing on disk
(`NEO_LOCALMCP_WIZARD_PREVIEW_STATE=healthy` simulates a returning, already-installed
user).
```

- [ ] **Step 3: Update `PROJECT_STATUS.md`**

Add one sentence noting the reorg is complete: version-appropriate phrasing following the existing "Current phase" paragraph's style (see the file's existing entries for tone/format — dated, factual, references what changed). Do not edit the existing dated `--fake`/`FakeBackend` mentions elsewhere in this file (lines ~30-33) — those describe verification that happened under the old names and are historical record, not current documentation.

- [ ] **Step 4: Append one entry to `PROJECT_NOTES.md`**

Add a single dated bullet (today's date) summarizing: installer's two frontends (`cli.py`, `wizard/`) consolidated under `installer/`; `tools.py` split into `mcp_commands/{system,memory,ollama,editing}.py` + `_shared.py`; `benchmark.py`+`benchmark_queries/` renamed to `benchmarker/`; wizard "dummy"/"fake" terminology standardized to "preview". Do not edit any existing entry above it — this file is append-only per `CLAUDE.md`'s own stated convention.

- [ ] **Step 5: Regenerate the `.mcpb` bundle**

Every task since Task 1 changed `neo_localmcp/`'s contents, so the committed `packages/claude-desktop/neo-localmcp-v<version>.mcpb` is stale (this is why the intermediate checkpoints deselected `test_built_mcpb_embeds_current_package_bytes`). Regenerate it now, from the source checkout, using the moved builder (`neo_localmcp.installer.mcpb`, relocated in Task 1). Delete the stale file first — `build_mcpb`'s `_next_free_path` never overwrites, so without the delete it would write `neo-localmcp-v<version>-2.mcpb` instead of regenerating the canonical name:

```bash
python3 -c "
import os
from neo_localmcp import __version__
from neo_localmcp.installer.mcpb import build_mcpb
stale = f'packages/claude-desktop/neo-localmcp-v{__version__}.mcpb'
if os.path.exists(stale):
    os.remove(stale)
print('rebuilt:', build_mcpb('.', __version__))
"
```

Expected: prints `rebuilt: packages/claude-desktop/neo-localmcp-v<version>.mcpb`. (The `__version__` is unchanged by this refactor — `1.1.1` is still unreleased per `PROJECT_STATUS.md`, so this reorg folds into it with no version bump; the bundle filename is the same, only its contents are regenerated.)

- [ ] **Step 6: Full final verification (bundle byte-check now runs for real)**

```bash
python -m pytest -q
```

Expected: full suite (including `-m slow` lifecycle tests AND the previously-deselected `test_built_mcpb_embeds_current_package_bytes`, which now passes against the freshly-rebuilt bundle) PASSES. This is the first point in the whole plan where the slow `tests/installer/test_*_lifecycle.py` tests run — they build a real venv and exercise the real `install`/`reinstall`/`uninstall` path end-to-end through the new `installer/cli.py`, which is the highest-value regression check for Tasks 1-6's path-arithmetic fixes. If `test_built_mcpb_embeds_current_package_bytes` still fails here, the bundle rebuild in Step 5 did not take — re-run Step 5 and confirm no stray `.DS_Store` or `-2.mcpb` file was created.

```bash
python -m compileall -q neo_localmcp setup.py setup_wizard.py
```

Expected: no output (success).

- [ ] **Step 7: Verify no stray references remain**

```bash
grep -rn "neo_localmcp\.wizard\b\|neo_localmcp\.setup_cli\b\|neo_localmcp\.mcpb_build\b\|neo_localmcp\.tools\b\|neo_localmcp\.benchmark\b\|FakeBackend\|RealBackend\|allow_dummy_toggle\|_ToggleDummy\|NEO_LOCALMCP_WIZARD_FAKE_STATE\|--fake\|wizard/real_backend\|wizard/fake_backend\|tools\.set_ollama\|tools\.doctor\|tools\.prepare_context\|tools\.summarize_file\|tools\.apply_unified_patch\|\btools\.py\b" neo_localmcp tests setup.py setup_wizard.py pyproject.toml CLAUDE.md README.md 2>/dev/null
```

Expected: no output. If anything matches, it is a missed call site OR a stale docstring/comment from an earlier task — fix it and re-run this grep before proceeding. This step's original pattern only matched dotted module paths (`neo_localmcp.wizard`, `neo_localmcp.tools`, etc.); Task 6's review found two production-code docstrings that reference the old layout in prose that wouldn't match those patterns: `neo_localmcp/installer/ollama.py`'s docstring says `` ``wizard/real_backend.py`` `` (slash-style path, not a dotted import) and `` ``tools.set_ollama`` `` (bare `tools.`, no `neo_localmcp.` prefix — also now wrong regardless of path style, since Task 12 deletes `tools.py` and moves `set_ollama` to `mcp_commands/ollama.py`). The added patterns (`wizard/real_backend`, `wizard/fake_backend`, and the specific `tools.<function>`/`tools.py` prose mentions) are a backstop for this class of docstring-prose staleness, not just code-level imports. When you find a match, fix the prose to name the actual current location (e.g. `` ``installer/wizard/live_backend.py`` ``, `` ``mcp_commands/ollama.set_ollama`` ``) rather than deleting the cross-reference. (`NEO_LOCALMCP_WIZARD_FAKE_STATE` is in this grep specifically because Task 4 renames all of its occurrences in `preview_backend.py` — code, docstring, and comment — plus one in `setup_wizard.py`; `--fake` is included because of the `README.md` fix in Step 2; this is the backstop that catches anything either missed. `PROJECT_STATUS.md` is deliberately NOT in this grep's file list — its `--fake` mentions are dated historical status entries left untouched per Step 3's note.)

- [ ] **Step 8: Commit (includes the regenerated bundle from Step 5)**

```bash
git add CLAUDE.md README.md PROJECT_STATUS.md PROJECT_NOTES.md "packages/claude-desktop/neo-localmcp-v*.mcpb"
git commit -m "docs: update module map and status/notes for installer/mcp-command reorg; rebuild .mcpb"
```

---

## Self-review notes (per writing-plans skill)

- **Spec coverage:** All spec sections implemented — installer frontends consolidated (Tasks 2, 3), `mcpb.py` moved (Task 1), wizard renamed with full terminology purge (Tasks 3-5), MCP command split into 4 categories + shared (Tasks 7-11), `tools.py` deleted with all callers repointed (Task 12), `benchmarker/` rename (Task 13), docs updated (Task 14).
- **Two real bugs the spec's prose flagged were traced to exact fixes:** `setup_cli.py`'s `_source_root()` path depth (Task 2), `fake_backend.py`'s `_STATE_DIR` path depth (Task 4) — both verified against actual `__file__`-relative arithmetic, not assumed.
- **One bug not in the original spec, found only by reading `installer/verification.py` directly:** the `_DOCTOR_SNIPPET` string literal embedding `neo_localmcp.tools` as executable subprocess code (Task 12, Step 6) — this would have silently broken every future install's post-install verification if missed, since it's a string, not a static import a simple grep for `from neo_localmcp.tools` would necessarily catch on the first pass (a grep for the bare word "tools" was needed).
- **One dependency not in the original spec, found only by reading `context_worker.py` directly:** it imports `tools.context_prepare` and is invoked as a subprocess by `server.py` — fixed in Task 12, Step 4.
- **One dead import found while tracing `tools.py`:** `save_config` is imported but never called directly in `tools.py`'s body (`set_ollama` goes through `installer_configure_models`, which calls `save_config` itself, in a different file) — correctly dropped rather than carried into any new file.
- **Review-pass catch (blocker, now fixed):** `tests/test_distribution.py::test_built_mcpb_embeds_current_package_bytes` byte-checks the committed `.mcpb` against `neo_localmcp/`; it is unmarked, so it runs in every `-m "not slow"`. The reorg staled the bundle, so the intermediate full-suite checkpoints (Tasks 6, 12, 13) now `--deselect` it and Task 14 Step 4 regenerates the bundle (via the moved `neo_localmcp.installer.mcpb.build_mcpb`, deleting the stale file first) before running the full suite un-deselected. Only ~6 of 32 test files are import-affected by the whole reorg; the rest stay green throughout and are the behavior-preservation net — a broad red suite mid-reorg would signal a real regression, not expected churn.
