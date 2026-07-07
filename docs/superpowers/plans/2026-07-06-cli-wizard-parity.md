# CLI/Wizard Option Parity Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give `setup.py` (via `neo_localmcp/setup_cli.py`) the same five operations the wizard (`neo_localmcp/wizard/console.py`) already exposes — `install`, `reinstall`, `uninstall`, **`config-ollama`**, and **`manage-clients`** — with the underlying logic for the last two living in `installer/` and shared by every caller (including the previously-separate `neo_localmcp/cli.py` / `tools.py` runtime surface for Ollama config), not duplicated per front door.

**Architecture:** Full design rationale and rejected alternatives are in `docs/superpowers/specs/2026-07-06-cli-wizard-parity-design.md` — read that first if anything below is unclear. Summary: `installer/ollama.py` gains `configure_models()` (paired with its existing `configured_models()` getter) and `installer/clients.py` gains `apply_client_selection()` + `ClientChangeOutcome`. `wizard/real_backend.py` is refactored to call both (behavior-preserving, no policy of its own left). `setup_cli.py` grows two new subcommands calling the exact same functions. `tools.py`'s `set_ollama()` (the separate `neo-localmcp set-ollama` runtime command) is refactored to call `configure_models()` too, for full 3-way dedup. No files are renamed in this change.

**Tech Stack:** Python 3.12+, stdlib `argparse`, existing `pytest` suite (`isolated_config` / `isolated_app_home` / `client_home` fixtures already in `tests/conftest.py` / `tests/installer/`).

## Global Constraints

- Python 3.12+ floor applies to every file touched (repo-wide).
- `setup.py` remains the sole lifecycle policy surface for macOS/Windows — the two new subcommands go in `setup_cli.py`, not `neo_localmcp/cli.py`.
- No new third-party dependencies. (`psutil` is already an unconditional base dependency in `pyproject.toml`, so `tools.py` importing from `neo_localmcp.installer` — which transitively imports `installer/processes.py`'s `psutil` usage — introduces no new install requirement, only earlier import-time cost.)
- No file renames — deferred per the design doc's Decision 3.
- Run `python -m pytest -q` and `python -m compileall -q neo_localmcp setup.py` after every task; both must be clean before moving to the next task.
- This work happens on its own branch, lands via its own PR — **do not merge the PR**, leave it open for review.
- Issue/PR title format is `type(area): description`. This spans `neo_localmcp/tools.py`, `neo_localmcp/installer/ollama.py`, `neo_localmcp/installer/clients.py`, `neo_localmcp/wizard/real_backend.py`, and `neo_localmcp/setup_cli.py` — label `type:feat` with **both** `area:installer` and `area:wizard`.
- Every function that performs a state-changing action degrades to a visible warning/failure, never raises uncaught (matches the existing `# noqa: BLE001` convention throughout `real_backend.py` and `installer/operations.py`) — preserve that pattern in all new code.

---

### Task 1: `configure_models()` in `installer/ollama.py`, deduplicating all three Ollama-config callers

**Files:**
- Modify: `neo_localmcp/installer/ollama.py` (add function + `save_config` import)
- Modify: `neo_localmcp/installer/__init__.py` (re-export the new name)
- Modify: `neo_localmcp/tools.py:1136-1147` (`set_ollama` delegates to the new function)
- Test: `tests/installer/test_ollama.py` (new file)

**Interfaces:**
- Produces: `configure_models(*, base_url: str | None = None, fast_model: str | None = None, summary_model: str | None = None, num_ctx: int | None = None) -> dict[str, Any]`, importable as `from neo_localmcp.installer import configure_models`. Only given (truthy) fields are changed; omitted fields keep their current persisted value. Returns the updated `ollama` config block.

- [ ] **Step 1: Write the failing tests**

```python
# tests/installer/test_ollama.py
from __future__ import annotations

from neo_localmcp import config
from neo_localmcp.installer import configure_models


def test_configure_models_sets_only_given_fields(isolated_config):
    config.save_config({**config.load_config(), "ollama": {
        "base_url": "http://127.0.0.1:11434", "fast_model": "old-fast", "summary_model": "old-summary",
    }})

    result = configure_models(fast_model="new-fast")

    assert result["fast_model"] == "new-fast"
    assert result["summary_model"] == "old-summary"
    assert result["base_url"] == "http://127.0.0.1:11434"


def test_configure_models_persists_to_disk(isolated_config):
    configure_models(base_url="http://example:1234/", summary_model="big-model")

    reloaded = config.load_config()["ollama"]
    assert reloaded["base_url"] == "http://example:1234"  # trailing slash stripped
    assert reloaded["summary_model"] == "big-model"


def test_configure_models_sets_num_ctx(isolated_config):
    result = configure_models(num_ctx=8192)

    assert result["num_ctx"] == 8192


def test_configure_models_with_nothing_given_is_a_noop(isolated_config):
    before = configure_models(fast_model="fast-a", summary_model="summary-a")

    after = configure_models()

    assert after == before
```

(`isolated_config` is defined in `tests/conftest.py` and monkeypatches `config.APP_DIR`/`config.CONFIG_PATH`/`config.DEFAULT_CONFIG` — confirm it's picked up automatically by pytest's fixture discovery; no import needed in the test file.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/installer/test_ollama.py -v`
Expected: FAIL with `ImportError: cannot import name 'configure_models' from 'neo_localmcp.installer'`

- [ ] **Step 3: Implement `configure_models`**

In `neo_localmcp/installer/ollama.py`, change the import line:

```python
from ..config import load_config, save_config
```

Then add, after `configured_models()`:

```python
def configure_models(
    *,
    base_url: str | None = None,
    fast_model: str | None = None,
    summary_model: str | None = None,
    num_ctx: int | None = None,
) -> dict[str, Any]:
    """Merge non-empty overrides into the persisted Ollama config and save it.

    Only fields that are given (truthy) are changed; omitted fields keep
    their current persisted value. Returns the updated ``ollama`` config
    block. Shared by ``tools.set_ollama`` (the ``neo-localmcp set-ollama``
    runtime command), the wizard's "Configure Ollama models" operation
    (``wizard/real_backend.py``), and ``setup.py config-ollama`` -- the three
    surfaces that let a user change these settings -- so there is exactly one
    place that decides what "setting the Ollama config" means.
    """
    cfg = load_config()
    ollama_cfg = cfg.setdefault("ollama", {})
    if base_url:
        ollama_cfg["base_url"] = base_url.rstrip("/")
    if fast_model:
        ollama_cfg["fast_model"] = fast_model
    if summary_model:
        ollama_cfg["summary_model"] = summary_model
    if num_ctx:
        ollama_cfg["num_ctx"] = int(num_ctx)
    save_config(cfg)
    return ollama_cfg
```

Add `from typing import Any` to this file's imports if not already present (check with `grep -n "^from typing" neo_localmcp/installer/ollama.py` first — the existing `ModelUnloadResult` dataclass doesn't use `Any`, so this import is likely missing).

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/installer/test_ollama.py -v`
Expected: PASS (4 passed)

- [ ] **Step 5: Export from `installer/__init__.py`**

Extend the existing `from .ollama import (...)` block (currently lines 45-50) to add `configure_models`, and add it to the `__all__` list.

- [ ] **Step 6: Refactor `tools.set_ollama` to delegate (behavior-preserving)**

In `neo_localmcp/tools.py`, replace lines 1136-1147:

```python
def set_ollama(base_url: str | None = None, summary_model: str | None = None, fast_model: str | None = None, num_ctx: int | None = None) -> str:
    ollama_cfg = installer_configure_models(
        base_url=base_url, fast_model=fast_model, summary_model=summary_model, num_ctx=num_ctx,
    )
    return json_out({"ok": True, "ollama": ollama_cfg, "status": ollama_state()})
```

Add the import near the top of `tools.py`, right after the existing `from .config import ...` line (line 13):

```python
from .installer import configure_models as installer_configure_models
```

(Aliased to `installer_configure_models` to avoid any confusion with this file's own `set_ollama` at the call site — `tools.py` has no other `installer` import today, so this is the first one.)

- [ ] **Step 7: Run the full test suite for regressions**

Run: `python -m pytest -q`
Expected: PASS, no new failures (confirmed by `grep -rn "set_ollama" tests/` returning nothing before this task — no existing test asserted on `tools.set_ollama`'s internals)

- [ ] **Step 8: Compile check**

Run: `python -m compileall -q neo_localmcp setup.py`
Expected: clean

- [ ] **Step 9: Commit**

```bash
git add neo_localmcp/installer/ollama.py neo_localmcp/installer/__init__.py neo_localmcp/tools.py tests/installer/test_ollama.py
git commit -m "feat(installer): add configure_models shared by tools/wizard/CLI"
```

---

### Task 2: `apply_client_selection()` in `installer/clients.py`

**Files:**
- Modify: `neo_localmcp/installer/clients.py` (add dataclass + function after `restore_recorded_registrations`, ~line 321; extend `typing` import)
- Modify: `neo_localmcp/installer/__init__.py` (re-export the two new names)
- Test: `tests/installer/test_clients.py` (append)

**Interfaces:**
- Consumes: `client_setup.setup_client(client: str, apply: bool, *, server_command) -> dict`, `client_setup.remove_client(client: str, apply: bool) -> dict` (already imported in this module as `client_setup`), `read_registrations(paths) -> tuple[ClientRegistrationRecord, ...]`, `record_selection(paths, clients: list[str]) -> tuple[ClientRegistrationRecord, ...]` (both already defined in this file), module constants `CLAUDE_CODE`, `CODEX`, `CLAUDE_DESKTOP` (already defined in this file).
- Produces:
  - `ClientChangeOutcome` frozen dataclass: `ok: bool`, `connected: tuple[str, ...]`, `added: tuple[str, ...]`, `removed: tuple[str, ...]`, `manual: tuple[str, ...]`, `failures: tuple[str, ...]`.
  - `apply_client_selection(paths: ManagedPaths, target: Sequence[str], *, server_command: str | Path, on_event: Callable[[str, str], None] | None = None) -> ClientChangeOutcome`. `on_event`, when given, is called with `(level, message)` for every action/warning/error/info line — `level` is one of `"info"`, `"action"`, `"warning"`, `"error"` (the same vocabulary `installer/output.py::Reporter` already uses).
  - Both importable as `from neo_localmcp.installer import apply_client_selection, ClientChangeOutcome`.

- [ ] **Step 1: Check existing fixtures/imports in the target test file**

Run: `grep -n "^from\|^import\|^def client_home\|^@pytest.fixture" tests/installer/test_clients.py | head -20`

This confirms the exact name and location of the `client_home` fixture and whether `ManagedPaths` is already imported at module level, before writing Step 2's tests (do not guess — read the actual output).

- [ ] **Step 2: Write the failing tests**

Append to `tests/installer/test_clients.py`:

```python
def test_apply_client_selection_connects_and_disconnects(client_home, tmp_path, monkeypatch):
    from neo_localmcp.installer import clients

    paths = ManagedPaths.from_environment()
    clients.record_selection(paths, ["claude-code"])

    calls = []
    monkeypatch.setattr(
        clients.client_setup, "setup_client",
        lambda client, apply=True, **kw: calls.append(("setup", client)) or {"client": client, "ok": True},
    )
    monkeypatch.setattr(
        clients.client_setup, "remove_client",
        lambda client, apply=True, **kw: calls.append(("remove", client)) or {"client": client, "ok": True},
    )

    events = []
    outcome = clients.apply_client_selection(
        paths, ["codex"], server_command="neo-localmcp-server",
        on_event=lambda level, message: events.append((level, message)),
    )

    assert outcome.ok
    assert outcome.added == ("codex",)
    assert outcome.removed == ("claude-code",)
    assert ("setup", "codex") in calls
    assert ("remove", "claude-code") in calls
    assert any(level == "action" for level, _ in events)


def test_apply_client_selection_records_failures_without_raising(client_home, tmp_path, monkeypatch):
    from neo_localmcp.installer import clients

    paths = ManagedPaths.from_environment()

    def _boom(client, apply=True, **kw):
        raise RuntimeError("simulated failure")

    monkeypatch.setattr(clients.client_setup, "setup_client", _boom)

    outcome = clients.apply_client_selection(paths, ["claude-code"], server_command="neo-localmcp-server")

    assert not outcome.ok
    assert "claude-code" in outcome.failures[0]


def test_apply_client_selection_with_no_changes_still_records_target(client_home, tmp_path, monkeypatch):
    from neo_localmcp.installer import clients

    paths = ManagedPaths.from_environment()
    clients.record_selection(paths, ["claude-code"])

    outcome = clients.apply_client_selection(paths, ["claude-code"], server_command="neo-localmcp-server")

    assert outcome.ok
    assert outcome.added == ()
    assert outcome.removed == ()
```

If Step 1 showed `ManagedPaths` is not already imported at module level in this test file, add `from neo_localmcp.installer.paths import ManagedPaths` to its top-level imports.

- [ ] **Step 3: Run tests to verify they fail**

Run: `python -m pytest tests/installer/test_clients.py -k apply_client_selection -v`
Expected: FAIL with `AttributeError: module 'neo_localmcp.installer.clients' has no attribute 'apply_client_selection'`

- [ ] **Step 4: Implement `ClientChangeOutcome` and `apply_client_selection`**

Add to `neo_localmcp/installer/clients.py` immediately after `restore_recorded_registrations`:

```python
@dataclass(frozen=True)
class ClientChangeOutcome:
    """Result of reconciling live client registrations to a target set."""

    ok: bool
    connected: tuple[str, ...]
    added: tuple[str, ...]
    removed: tuple[str, ...]
    manual: tuple[str, ...]
    failures: tuple[str, ...]


def apply_client_selection(
    paths: ManagedPaths,
    target: Sequence[str],
    *,
    server_command: str | Path,
    on_event: Callable[[str, str], None] | None = None,
) -> ClientChangeOutcome:
    """Reconcile live client registrations to match ``target``.

    Diffs ``target`` against the currently recorded clients, connects newly
    selected surfaces via :func:`neo_localmcp.client_setup.setup_client`,
    disconnects deselected ones via
    :func:`neo_localmcp.client_setup.remove_client`, and persists the new
    target via :func:`record_selection`. Used by both the wizard's "Manage
    connected clients" operation and ``setup.py manage-clients`` so both
    surfaces reconcile client registrations identically.
    """

    def emit(level: str, message: str) -> None:
        if on_event is not None:
            on_event(level, message)

    known = {CLAUDE_CODE, CODEX, CLAUDE_DESKTOP}
    current = {r.client for r in read_registrations(paths) if r.client in known}
    target_list = list(dict.fromkeys(target))  # de-dupe, preserve order
    add = [c for c in target_list if c not in current]
    remove = [c for c in current if c not in target_list]
    failures: list[str] = []
    manual: list[str] = []

    for client in add:
        emit("action", f"Connecting {client} ...")
        try:
            result = client_setup.setup_client(client, apply=True, server_command=server_command)
            if isinstance(result, dict) and result.get("manual_install_required"):
                note = str(result.get("instructions") or "Manual install required.")
                emit("warning", f"  {note}")
                manual.append(f"{client}: {note}")
        except Exception as exc:  # noqa: BLE001 - surfaced as a failure, never raised
            failures.append(f"{client}: {exc}")
            emit("error", f"  failed: {exc}")

    for client in remove:
        emit("action", f"Disconnecting {client} ...")
        try:
            client_setup.remove_client(client, apply=True)
        except Exception as exc:  # noqa: BLE001
            failures.append(f"{client}: {exc}")
            emit("error", f"  failed: {exc}")

    if not add and not remove:
        emit("info", "No client changes to apply.")

    try:
        record_selection(paths, target_list)
    except Exception as exc:  # noqa: BLE001 - registration record is best-effort
        emit("warning", f"Could not update registration record: {exc}")

    return ClientChangeOutcome(
        ok=not failures,
        connected=tuple(target_list),
        added=tuple(add),
        removed=tuple(remove),
        manual=tuple(manual),
        failures=tuple(failures),
    )
```

Extend this file's existing `from typing import Any` import to `from typing import Any, Callable, Sequence`.

- [ ] **Step 5: Run tests to verify they pass**

Run: `python -m pytest tests/installer/test_clients.py -k apply_client_selection -v`
Expected: PASS (3 passed)

- [ ] **Step 6: Export from `installer/__init__.py`**

Extend the existing `from .clients import (...)` block (currently lines 51-63) to add `ClientChangeOutcome` and `apply_client_selection`, and add both names to `__all__`.

- [ ] **Step 7: Run the full test suite for regressions**

Run: `python -m pytest -q`
Expected: PASS

- [ ] **Step 8: Commit**

```bash
git add neo_localmcp/installer/clients.py neo_localmcp/installer/__init__.py tests/installer/test_clients.py
git commit -m "feat(installer): add apply_client_selection shared by wizard and CLI"
```

---

### Task 3: Refactor `real_backend.py` to use both shared helpers

**Files:**
- Modify: `neo_localmcp/wizard/real_backend.py:328-405` (`_write_ollama_config` and `apply_client_changes`)
- Test: `tests/test_wizard.py` (append)

**Interfaces:**
- Consumes: `installer.configure_models(...)` (Task 1), `installer.clients.apply_client_selection(...)` + `ClientChangeOutcome` (Task 2) — both already imported into this file's namespace as `config`/`clients_mod` per its existing top-of-file imports (`from .. import client_setup, config, ollama_client` and `from ..installer import clients as clients_mod`) — check `grep -n "^from \.\.\|^import" neo_localmcp/wizard/real_backend.py` to confirm exact current names before editing, since `configure_models` needs to be reachable too (either via the existing `from ..installer import (...)` block, extended, or a new `from .. import installer` style import — prefer extending the existing block).
- Produces: no new public interface — this task is a behavior-preserving refactor. The existing `WizardBackend.apply_ollama_config` / `apply_client_changes` signatures and `OperationOutcome` shapes are unchanged.

- [ ] **Step 1: Write the failing/characterization tests**

Append to `tests/test_wizard.py` (this file already imports `real_backend` and uses `isolated_app_home` — see `test_real_backend_satisfies_wizard_backend_protocol`):

```python
def test_real_backend_apply_client_changes_uses_shared_helper(isolated_app_home, monkeypatch):
    from neo_localmcp.installer import clients as clients_mod
    from neo_localmcp.wizard.backend import WizardState
    from neo_localmcp.wizard.real_backend import RealBackend

    calls = []
    monkeypatch.setattr(
        clients_mod.client_setup, "setup_client",
        lambda client, apply=True, **kw: calls.append(client) or {"client": client, "ok": True},
    )

    backend = RealBackend()
    state = WizardState(operation="manage-clients", selected_clients=["claude-code"])
    events = []
    outcome = backend.apply_client_changes(state, lambda event: events.append(event))

    assert outcome.ok
    assert calls == ["claude-code"]
    assert any(e.level == "action" for e in events)


def test_real_backend_apply_ollama_config_uses_shared_helper(isolated_app_home):
    from neo_localmcp import config
    from neo_localmcp.wizard.backend import WizardState
    from neo_localmcp.wizard.real_backend import RealBackend

    backend = RealBackend()
    state = WizardState(
        operation="config-ollama", fast_model="new-fast", summary_model="new-summary",
        ollama_base_url="http://127.0.0.1:11434",
    )
    outcome = backend.apply_ollama_config(state, lambda event: None)

    assert outcome.ok
    assert config.load_config()["ollama"]["fast_model"] == "new-fast"
    assert config.load_config()["ollama"]["summary_model"] == "new-summary"
```

- [ ] **Step 2: Run tests against the pre-refactor code to confirm they already pass**

Run: `python -m pytest tests/test_wizard.py -k "apply_client_changes_uses_shared_helper or apply_ollama_config_uses_shared_helper" -v`
Expected: PASS (these characterize *current* behavior — both tests must pass **before** the refactor too, since the refactor must not change behavior. If either fails here, the test itself is wrong, not the code — fix the test before proceeding.)

- [ ] **Step 3: Refactor `_write_ollama_config`**

In `neo_localmcp/wizard/real_backend.py`, replace the `_write_ollama_config` method (lines 328-340):

```python
    def _write_ollama_config(self, state: WizardState, emit: EmitFn) -> None:
        ollama_cfg = installer_mod.configure_models(
            base_url=state.ollama_base_url or None,
            fast_model=state.fast_model or None,
            summary_model=state.summary_model or None,
        )
        emit(StepEvent("action",
                       f"Saved Ollama config: fast={ollama_cfg.get('fast_model')}, "
                       f"summary={ollama_cfg.get('summary_model')}"))
```

Add `from .. import installer as installer_mod` to this file's imports (alongside the existing `from ..installer import (...)` block — keep both, the named-import block for the existing `ManagedPaths`/`Operation`/etc. symbols and this module-level one for `installer_mod.configure_models`, OR simply add `configure_models` to the existing named-import block and call it unqualified as `configure_models(...)` instead — pick whichever reads more consistently with this file's existing style; the named-import-block approach is what the rest of this file already does, so prefer adding `configure_models` there and dropping the `installer_mod.` prefix above).

- [ ] **Step 4: Refactor `apply_client_changes`**

Replace the `apply_client_changes` method (lines 363-405):

```python
    def apply_client_changes(self, state: WizardState, emit: EmitFn) -> OperationOutcome:
        outcome = clients_mod.apply_client_selection(
            self._paths,
            state.selected_clients,
            server_command=self._paths.server_executable,
            on_event=lambda level, message: emit(StepEvent(level, message)),
        )
        if not outcome.ok:
            return OperationOutcome(
                ok=False, status="failed", title="Some client changes failed.",
                detail_lines=tuple(outcome.failures) + outcome.manual,
            )
        details = [f"Connected: {', '.join(outcome.connected) or 'none'}"]
        details.extend(outcome.manual)
        return OperationOutcome(
            ok=True, status="succeeded", title="Client connections updated.",
            detail_lines=tuple(details),
        )
```

- [ ] **Step 5: Run tests to verify they still pass**

Run: `python -m pytest tests/test_wizard.py -v`
Expected: PASS, no regressions

- [ ] **Step 6: Run the full test suite**

Run: `python -m pytest -q`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add neo_localmcp/wizard/real_backend.py tests/test_wizard.py
git commit -m "refactor(wizard): real_backend delegates to shared installer helpers"
```

---

### Task 4: `config-ollama` and `manage-clients` subcommands in `setup_cli.py`

**Files:**
- Modify: `neo_localmcp/setup_cli.py` (parser additions in `build_parser()`, two new dispatch helpers, `main()` restructure)
- Test: `tests/installer/test_setup_cli.py` (append)

**Interfaces:**
- Consumes: `installer.configure_models(...)` (Task 1), `installer.clients.apply_client_selection(...)` + `ClientChangeOutcome` (Task 2).
- Produces: two new argparse subcommands, `config-ollama` and `manage-clients`, exit codes `EXIT_SUCCESS`/`EXIT_FAILURE` matching the existing convention.

- [ ] **Step 1: Write the failing tests**

Append to `tests/installer/test_setup_cli.py` (uses the file's existing `_run`/`_isolated_env` helpers defined at its top):

```python
def test_config_ollama_help_exits_zero() -> None:
    result = _run(["config-ollama", "--help"])
    assert result.returncode == 0
    assert "--fast-model" in result.stdout


def test_manage_clients_help_exits_zero() -> None:
    result = _run(["manage-clients", "--help"])
    assert result.returncode == 0
    assert "--client" in result.stdout


def test_config_ollama_writes_only_given_fields(tmp_path: Path) -> None:
    env = _isolated_env(tmp_path)
    result = _run(["config-ollama", "--fast-model", "test-fast-model"], env=env)
    assert result.returncode == 0
    assert "test-fast-model" in result.stdout


def test_manage_clients_with_no_flags_disconnects_everything(tmp_path: Path) -> None:
    env = _isolated_env(tmp_path)
    result = _run(["manage-clients"], env=env)
    # No clients were ever registered in this fresh isolated home, so this is a no-op,
    # not a failure -- exercises the "target = []" default path.
    assert result.returncode == 0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/installer/test_setup_cli.py -k "config_ollama or manage_clients" -v`
Expected: FAIL with argparse `error: argument operation: invalid choice: 'config-ollama'` (exit code 2)

- [ ] **Step 3: Add the two subparsers**

In `neo_localmcp/setup_cli.py`, inside `build_parser()`, immediately before the final `return parser` (after the existing `uninstall_parser` block):

```python
    config_ollama_parser = sub.add_parser(
        "config-ollama",
        help="Set the Ollama base URL and/or fast/summary models. Omitted flags keep their current value.",
    )
    config_ollama_parser.add_argument("--base-url")
    config_ollama_parser.add_argument("--fast-model")
    config_ollama_parser.add_argument("--summary-model")
    config_ollama_parser.set_defaults(operation="config-ollama")

    manage_clients_parser = sub.add_parser(
        "manage-clients",
        help="Reconcile which clients are connected to the currently installed runtime.",
    )
    manage_clients_parser.add_argument(
        "--client",
        action="append",
        choices=("claude-code", "codex", "claude-desktop"),
        default=[],
        help="Client that should stay connected. Repeat for multiple; omit entirely to disconnect all.",
    )
    manage_clients_parser.set_defaults(operation="manage-clients")
```

- [ ] **Step 4: Add the two dispatch helpers**

Add these functions right before `main()`:

```python
def _run_config_ollama(args: argparse.Namespace, reporter: Reporter) -> int:
    from neo_localmcp.installer import configure_models

    ollama_cfg = configure_models(
        base_url=args.base_url, fast_model=args.fast_model, summary_model=args.summary_model,
    )
    reporter.action(
        f"Saved Ollama config: fast={ollama_cfg.get('fast_model')}, "
        f"summary={ollama_cfg.get('summary_model')}, base_url={ollama_cfg.get('base_url')}"
    )
    return EXIT_SUCCESS


_LEVEL_METHODS = {
    "info": Reporter.info,
    "warning": Reporter.warn,
    "error": Reporter.error,
    "action": Reporter.action,
}


def _run_manage_clients(
    args: argparse.Namespace, context: OperationContext, reporter: Reporter,
) -> int:
    from neo_localmcp.installer import apply_client_selection

    outcome = apply_client_selection(
        context.paths,
        args.client,
        server_command=context.paths.server_executable,
        on_event=lambda level, message: _LEVEL_METHODS[level](reporter, message),
    )
    if not outcome.ok:
        return EXIT_FAILURE
    reporter.summary(
        "manage-clients succeeded",
        {"connected": ", ".join(outcome.connected) or "none"},
    )
    return EXIT_SUCCESS
```

- [ ] **Step 5: Wire the dispatch into `main()`**

In `main()`, immediately after `context = build_context(reporter)` and before the existing `if args.operation == "install":` line, insert:

```python
    if args.operation == "config-ollama":
        return _run_config_ollama(args, reporter)
    if args.operation == "manage-clients":
        return _run_manage_clients(args, context, reporter)
```

(These two operations never construct `Operation(args.operation)` — that enum only has `INSTALL`/`REINSTALL`/`UNINSTALL` members, so they must return before the `reporter.info(operation_explanation(Operation(args.operation)))` line further down, which this insertion point guarantees.)

- [ ] **Step 6: Run tests to verify they pass**

Run: `python -m pytest tests/installer/test_setup_cli.py -v`
Expected: PASS, no regressions in the pre-existing tests in this file

- [ ] **Step 7: Run the full test suite and compileall**

Run: `python -m pytest -q && python -m compileall -q neo_localmcp setup.py`
Expected: both clean

- [ ] **Step 8: Commit**

```bash
git add neo_localmcp/setup_cli.py tests/installer/test_setup_cli.py
git commit -m "feat(installer): add config-ollama and manage-clients to setup.py CLI"
```

---

### Task 5: Docs

**Files:**
- Modify: `README.md:212-217` (setup.py usage examples)
- Modify: `PROJECT_STATUS.md` (status line)
- Modify: `PROJECT_NOTES.md` (dated entry)

**Interfaces:** none (docs only).

- [ ] **Step 1: Add two example lines to README.md**

After line 216 (`python3 setup.py uninstall --delete-memory --yes ...`), add:

```
python3 setup.py config-ollama --fast-model qwen2.5:3b --summary-model qwen2.5-coder:7b
python3 setup.py manage-clients --client claude-code --client codex   # disconnects any client not listed
```

- [ ] **Step 2: Update PROJECT_STATUS.md**

Add a line noting `setup.py` now exposes all five wizard operations (install/reinstall/uninstall/config-ollama/manage-clients), and that the underlying Ollama-config logic is shared across the wizard, `setup.py`, and the separate `neo-localmcp set-ollama` runtime command (one implementation, three callers).

- [ ] **Step 3: Append a dated entry to PROJECT_NOTES.md**

One or two lines, dated with today's date, describing the change and why (CLI/wizard option parity, discovered while tracing the full setup lifecycle call chain; full 3-way dedup of the Ollama-config write path).

- [ ] **Step 4: Commit**

```bash
git add README.md PROJECT_STATUS.md PROJECT_NOTES.md
git commit -m "docs: document setup.py config-ollama and manage-clients"
```

---

### Task 6: End-to-end verification

**Files:** none modified — verification only.

- [ ] **Step 1: Full test suite**

Run: `python -m pytest -q`
Expected: all pass, zero new failures relative to the pre-plan baseline.

- [ ] **Step 2: Compile check**

Run: `python -m compileall -q neo_localmcp setup.py`
Expected: clean, no syntax errors.

- [ ] **Step 3: Manual smoke test against an isolated home**

```bash
export NEO_LOCALMCP_HOME=/tmp/neo-localmcp-parity-smoke
python3 setup.py config-ollama --fast-model qwen2.5:3b --summary-model qwen2.5-coder:7b
python3 setup.py manage-clients --client claude-code
python3 setup.py manage-clients   # no --client at all -> disconnects claude-code again
```

Expected: each command exits 0 and prints an `ACTION:`/`SUMMARY:` line describing what changed; no traceback.

- [ ] **Step 4: Confirm option parity by inspection**

Run: `python3 setup.py --help` and `python3 setup_wizard.py --fake` (choose each of the 5 menu items, answer "b"/back out before confirming to avoid mutating anything) — confirm the same five operations are reachable from both entry points, with no operation exclusive to one side.

- [ ] **Step 5: Push branch and open the PR (do not merge)**

```bash
git push -u origin <branch-name>
gh pr create --title "feat(installer): setup.py/wizard option parity for Ollama config and client management" --body "$(cat <<'EOF'
## Summary
- Add `installer/ollama.py::configure_models()` and `installer/clients.py::apply_client_selection()` (+ `ClientChangeOutcome`), each with its own unit tests.
- Refactor `tools.set_ollama()` and `wizard/real_backend.py`'s `_write_ollama_config`/`apply_client_changes` to call them instead of duplicating the logic (behavior-preserving, characterized by tests that pass before and after).
- Add `config-ollama` and `manage-clients` subcommands to `setup.py` (via `setup_cli.py`), matching the wizard's five operations 1:1. No file renames in this change (deferred separately).

See `docs/superpowers/specs/2026-07-06-cli-wizard-parity-design.md` for the full design rationale and rejected alternatives.

## Test plan
- [ ] `python -m pytest -q` passes
- [ ] `python -m compileall -q neo_localmcp setup.py` passes
- [ ] Manual smoke test: `setup.py config-ollama` and `setup.py manage-clients` against an isolated `NEO_LOCALMCP_HOME`
- [ ] `setup.py --help` and the wizard's menu expose the same five operations
EOF
)"
```

Leave the PR open for review — do not merge it as part of this plan.
