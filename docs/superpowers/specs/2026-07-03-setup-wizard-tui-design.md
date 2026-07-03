# Setup Wizard TUI — Design

Date: 2026-07-03
Branch: `feature/setup-wizard-tui`

## Origin

`setup.py` already implements a full, correct install/reinstall/uninstall
lifecycle (see `neo_localmcp/installer/`), but it is flag-driven
(`--client codex --client claude-code`, `--clean --yes`, ...). A first-time
user cloning the repo has to already know which flags exist, what each client
surface means, and what OS-specific paths get written. This session also
found (and fixed, via README doc) that a genuinely bare interpreter needs
`psutil` importable before `setup.py` can even run at all — a second thing a
new user has no way to know up front.

This spec covers a new, optional, friendlier front door: `setup_wizard.py`, a
terminal UI with keyboard arrow navigation that walks a user through the same
install lifecycle, explaining every option and OS-specific path as it goes.
It does not replace `setup.py`; both remain supported entrypoints.

## Non-goals

- Not a general TUI shell over the whole CLI (indexing, context queries,
  doctor, etc.) — install/reinstall/uninstall/clean lifecycle plus Ollama
  endpoint/model configuration only.
- Not a rewrite of `neo_localmcp/installer/` lifecycle logic — the wizard is
  a new caller of existing operations, never a fork of them.
- Not responsible for installing Ollama itself, or any model — only for
  configuring the base-url/model names neo-localmcp will use once Ollama is
  present, same scope as `neo-localmcp set-ollama` today.

## Dependency bootstrap

`setup_wizard.py` needs `psutil` (same requirement `setup.py` has) and
`textual` (the TUI library) importable before it can draw anything. Primary
documented path, added as a new `pyproject.toml` optional-dependencies group:

```bash
pip install -e ".[wizard]"
python setup_wizard.py
```

`setup_wizard.py` itself starts with a **stdlib-only preflight** (no import of
`textual`/`psutil`/anything from `neo_localmcp` beyond what stdlib needs):
it attempts `import textual` and `import psutil`; if either fails, it cannot
draw the real UI, so it falls back to a plain `input()`-based prompt that
prints the exact `pip install -e ".[wizard]"` command and offers to run it via
subprocess for the user. Once dependencies are satisfied, it proceeds into the
real Textual app. This means `python setup_wizard.py`, run cold off a bare
clone with nothing pip-installed, still works end to end.

## Architecture

New package `neo_localmcp/wizard/`, mirroring the existing separation of
concerns in `neo_localmcp/installer/`:

- **`preflight.py`** — stdlib-only. Dependency detection + the fallback
  plain-text bootstrap offer described above. Never imports `textual` or
  `psutil` itself; only checks whether they're importable.
- **`backend.py`** — a `WizardBackend` `Protocol` defining every action a
  screen can invoke: `detect_state()`, `run_install(...)`,
  `run_reinstall(...)`, `run_uninstall(...)`, `client_paths_preview(...)`,
  `get_ollama_config()`, `set_ollama_config(...)`. This is the sole seam
  between the UI and real lifecycle logic — screens depend only on this
  Protocol, never on `neo_localmcp.installer` directly.
- **`fake_backend.py`** — Phase 1 deliverable. In-memory implementation of
  `WizardBackend`: no subprocesses, no filesystem writes, simulated
  results/delays, canned client-path previews for both Windows and macOS so
  the OS-specific text can be validated without switching machines.
- **`real_backend.py`** — Phase 2 deliverable. Wraps the existing
  `neo_localmcp.installer` operations (`install`/`reinstall`/`uninstall`,
  `build_candidate`/`promote_candidate`, `snapshot_clients`,
  `record_selection`, Ollama config helpers in `config.py`). No lifecycle
  logic is duplicated here, only called.
- **`app.py`** — the Textual `App` subclass; owns which backend
  (`fake_backend` or `real_backend`) is constructed and wires the screen flow.
- **`screens/`** — one module per wizard step (see Screen flow below).

Screens never import `neo_localmcp.installer` directly and never import
`fake_backend`/`real_backend` directly — only the `WizardBackend` protocol
type and whatever instance `app.py` hands them. This makes Phase 1 → Phase 2
a one-line change in `app.py` (which backend class gets constructed), with
zero screen-code changes.

## Screen flow

1. **Welcome** — shows detected OS, Python version, and current install
   state (absent / data-only / installed) via `WizardBackend.detect_state()`.
   "Press any key to continue."
2. **Operation select** (`OptionList`, single-select, arrow-key nav) — the
   five real operations `setup_cli.py` supports, not a flattened summary:
   Install / Install (full wipe first) / Reinstall / Uninstall (keep data) /
   Uninstall (full wipe, no reinstall). A separate "Preview only, make no
   changes" checkbox toggle sits below the list — `--dry-run` is orthogonal
   to every operation in the real CLI (each has its own entry in
   `_DRY_RUN_PLANS`), so it must not be presented as a sixth same-tier
   choice. Each operation's dim-grey explainer text is the existing wording
   from `setup_cli.py`'s `_DRY_RUN_PLANS` / `operation_explanation()` —
   reused verbatim, not re-authored, so wizard and CLI never describe the
   same operation differently.
3. **Client selection** (checklist, multi-select) — Claude Code / Codex /
   Claude Desktop / none. Each shows the OS-specific config path that will
   actually be written as its dim subtext (e.g. `%APPDATA%\Claude\...` on
   Windows vs `~/Library/Application Support/Claude/...` on macOS), sourced
   from `WizardBackend.client_paths_preview()` (same data `neo-localmcp
   clients` already computes via `client_setup.py`).
4. **Ollama config** (skippable, collapsed by default) — base-url /
   fast_model / summary_model / num_ctx text inputs, pre-filled with current
   config values from `WizardBackend.get_ollama_config()`. Dim text under
   each field explains its role (ranking vs. summarization).
5. **Confirm** — single summary screen of every choice made so far; nothing
   has executed yet. Destructive choices (full wipe / `--delete-memory`) get
   the same typed-`DELETE`-style confirmation gate the CLI already enforces
   — the wizard must not be a softer path to a destructive action.
6. **Progress / result** — live checklist as each ordered lifecycle step
   completes, reusing the existing `Reporter` event stream `setup.py` already
   emits (info/action/error events). Ends in a clear success/failure panel
   with the lifecycle log path and a suggested next command
   (e.g. `neo-localmcp doctor`).

## Data flow

Screens accumulate a single in-memory `WizardSelections` dataclass
(operation, clients, Ollama overrides, confirmation flags) as the user
navigates. Nothing is executed until the Confirm screen makes exactly one
call into the backend — mirroring `setup_cli.py`'s existing "collect
everything, then invoke exactly one operation" shape.

## Error handling

Any backend-reported failure surfaces as an error panel on the Progress
screen, showing the same `PromotionResult`/`OperationResult` error and
warning fields, plus the lifecycle log path (`runtime-build-<op-id>.log`),
that the CLI already surfaces. No partial or silent failure — this matches
`installer/runtime.py`'s existing rollback-on-failure guarantee, which the
wizard inherits for free by calling the same operations.

## Testing

- **Phase 1 (fake backend)**: Textual's own testing utilities (`Pilot`)
  drive every screen transition against `fake_backend`, asserting
  navigation order, that dim-grey description text renders under each
  option, and that destructive-operation confirmation gates block
  progression without confirmation. Fast, no subprocesses; runs in normal CI.
- **Phase 2 (real backend)**: mock-based tests assert the wizard calls
  `WizardBackend` methods with correct arguments derived from user
  selections (no real subprocess spawns in these tests), plus one real
  "slow" end-to-end lifecycle test per supported platform, following the
  existing `tests/installer/test_windows_lifecycle.py` /
  `test_macos_lifecycle.py` pattern.

## Phasing

- **Phase 1 — dummy prototype**: `preflight.py`, `backend.py` (Protocol),
  `fake_backend.py`, `app.py`, all `screens/`, wired end-to-end against the
  fake backend only. Fully navigable and demoable; nothing it does is real.
  Textual `Pilot` tests cover every screen.
- **Phase 2 — real wiring**: `real_backend.py` implementing the same
  `WizardBackend` Protocol against `neo_localmcp.installer`; swap it in as
  `app.py`'s default; add the mock-based and real lifecycle tests described
  above; update `README.md` with the new `pip install -e ".[wizard]"` +
  `python setup_wizard.py` entrypoint alongside the existing `setup.py` docs.

## Deferred

- Any TUI coverage of non-setup commands (indexing, context queries,
  doctor, model status, etc.).
- Auto-detecting/installing Ollama itself.
- Remembering wizard selections between runs (e.g. a saved answers file) —
  not requested, would need its own design if wanted later.
