# CLI/wizard option parity — design

**Date:** 2026-07-06
**Status:** approved (brainstorming), pending implementation plan

## Origin

While tracing the full call chain from `setup_wizard.py` and `setup.py` down to
`installer/operations.py` (see conversation), it surfaced that the wizard
(`neo_localmcp/wizard/console.py`) exposes five operations — install, reinstall,
uninstall, **Configure Ollama models**, and **Manage connected clients** — while
the lifecycle CLI (`neo_localmcp/setup_cli.py`, invoked via `python setup.py`)
only exposes three: install, reinstall, uninstall. The other two only exist
through a third, unrelated entry point: `neo_localmcp/cli.py`'s `set-ollama` and
`config clients setup/remove` subcommands (the separate, already-installed
`neo-localmcp` runtime command).

Owner's stated goal: the wizard and `setup.py`'s CLI should be **1:1** — whatever
one can do, the other can do. Where the wizard's per-operation logic currently
lives only inside `wizard/real_backend.py`, it should instead live in
`installer/`, called identically by both front doors, matching how
install/reinstall/uninstall already work today (both `setup_cli.py` and
`real_backend.py` already call the same `installer/operations.py` functions,
with no dependency between the two).

## Decisions (owner-confirmed)

1. **Call graph: parallel siblings, not a chain.** `real_backend.py` and
   `setup_cli.py` each call the same shared functions in `installer/`
   independently. No dependency is introduced between the wizard and
   `setup_cli.py` (rejected alternative: wizard calling into `setup_cli.py`
   directly, which would require reshaping its argparse-bound functions into a
   general API and tangles CLI-parsing concerns with programmatic reuse).
2. **`real_backend.py` stays in `wizard/`, but gets thin.** Every method becomes
   a short call into an `installer/` function plus StepEvent translation — no
   policy of its own. (Rejected alternative: physically relocating
   `real_backend.py` into `installer/`, to make `wizard/` contain literally only
   UI code. Owner chose to keep it in place since it is still UI-adjacent glue.)
3. **No file renames in this change.** A separate rename pass for
   `installer/`'s confusingly-named files (`clients.py` vs. top-level
   `client_setup.py`, `output.py`, `runtime.py`, `state.py`, `ollama.py` vs.
   top-level `ollama_client.py`, plus `setup_cli.py` vs. `cli.py`) was proposed
   and deferred — out of scope here, may be revisited as its own
   `type:refactor` PR later.
4. **Full 3-way dedup for the Ollama-config logic.** Owner explicitly asked for
   full deduplication rather than leaving `tools.py`'s `set_ollama` (used by the
   separate `neo-localmcp set-ollama` runtime command) as a third
   near-duplicate. All three surfaces call one function.
5. **No dedup target exists for client management beyond two callers.**
   `cli.py`'s `config clients setup/remove` do explicit add-only/remove-only
   actions — a different shape than "reconcile registrations to this exact
   target set" — so there is nothing to fold in there. Two callers
   (`real_backend.py`, `setup_cli.py`) is the natural scope.

## Components

### `installer/ollama.py` (existing file, no rename)

New function, named to pair with the module's existing `configured_models()`
getter:

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
    block.
    """
```

Requires adding `save_config` to this file's existing
`from ..config import load_config` import line.

Three callers, all converging on this one function:

| Caller | Passes `num_ctx`? | Notes |
|---|---|---|
| `neo_localmcp/tools.py::set_ollama()` | yes (its own param) | Used by `cli.py`'s `set-ollama` (the runtime CLI). Refactored from its own load/set/save to a single call + `json_out` wrap. |
| `wizard/real_backend.py::_write_ollama_config()` | no (wizard never asks) | Refactored to call it, then emit one `StepEvent("action", ...)`. |
| `setup_cli.py`'s new `config-ollama` subcommand | no (matches wizard) | New. |

### `installer/clients.py` (existing file, no rename)

New dataclass + function:

```python
@dataclass(frozen=True)
class ClientChangeOutcome:
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
    """Reconcile live client registrations to match ``target``."""
```

Diffs `target` against currently-registered clients (via the existing
`read_registrations`), connects newly-selected surfaces via the existing
`client_setup.setup_client`, disconnects deselected ones via
`client_setup.remove_client`, and persists via the existing
`record_selection`. `on_event(level, message)` uses the same level vocabulary
`installer/output.py::Reporter` already does (`"info"`/`"action"`/`"warning"`/`"error"`),
so callers can pipe it into either a `Reporter` or the wizard's `StepEvent`
stream without this module knowing about either. Exported from
`installer/__init__.py` alongside the existing public surface.

Two callers: `real_backend.py::apply_client_changes()` (refactored to call it
and translate the outcome into `StepEvent`s + `OperationOutcome`) and the new
`setup_cli.py` `manage-clients` subcommand.

### `setup_cli.py` (existing file, no rename)

Two new subparsers added to `build_parser()`:

- `config-ollama --base-url --fast-model --summary-model` (all optional; a
  given flag overrides, an omitted one keeps the current value). No
  `--dry-run` — matches the wizard, which only offers dry-run for
  install/reinstall.
- `manage-clients --client {claude-code,codex,claude-desktop}` (repeatable;
  represents the desired **target set** of connected clients, not an
  incremental add — matches the wizard's multi-select semantics exactly).
  Omitting `--client` entirely means "disconnect everything," identical to
  how `install`'s existing `--client` flag already documents "omit to
  register none." No `--dry-run`, same reasoning as above.

Both dispatch before `Operation(args.operation)` is constructed in `main()`,
since the `Operation` enum (`installer/types.py`) only has
`INSTALL`/`REINSTALL`/`UNINSTALL` members — these two new operations are
config-only side operations, not lifecycle operations in that enum's sense
(mirroring how the wizard already keeps `OP_CONFIG_OLLAMA`/`OP_MANAGE_CLIENTS`
as separate string constants in `wizard/backend.py`, outside the installer's
`Operation` enum).

## Testing

- `installer/ollama.py::configure_models` and `installer/clients.py::apply_client_selection`
  get direct unit tests (existing fixtures: `isolated_config`, `isolated_app_home`,
  `client_home`).
- `real_backend.py`'s two refactored methods get characterization tests that
  must pass **before** the refactor (proving they describe current behavior)
  and **after** (proving the refactor is behavior-preserving).
- The two new `setup_cli.py` subcommands get subprocess-level tests matching
  the existing pattern in `tests/installer/test_setup_cli.py`.
- `tools.set_ollama`'s refactor is checked by the full existing suite (no
  test currently asserts on its internals — confirmed by grep — so this is a
  regression check, not new coverage).

## Out of scope

- The file renames discussed and deferred (see Decision 3).
- Any change to `cli.py`'s `config clients setup/remove` (the add-only/remove-only
  runtime commands) — untouched.
- `--dry-run` for the two new operations — the wizard doesn't offer it for
  these either, so neither does the CLI.
