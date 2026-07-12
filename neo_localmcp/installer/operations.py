"""Compose approved installer primitives into safe lifecycle operations.

This module adds no new policy of its own for paths, process ownership, runtime
building, or deletion targets. It *sequences* the already-approved primitives in
:mod:`neo_localmcp.installer` into the exact install / reinstall / uninstall /
clean-install / full-wipe semantics the 1.0.10 setup-v2 plan requires.

Every side-effecting collaborator is reachable through :class:`OperationContext`
so the ordered-call, semantic-matrix, and confirmation tests can drive the whole
flow against fakes -- without real venvs, processes, networks, or homes.

Semantic matrix (see the task brief):

======================  ============  =============  ======================  ======================
Operation               Removes venv  Recreates venv  Preserves durable data  Active clients after
======================  ============  =============  ======================  ======================
install                 if replacing  yes             yes                     restored / selected
reinstall               yes           yes             yes                     restored
uninstall               yes           no              yes                     removed
install --clean         yes           yes             no                      newly selected only
uninstall --delete-mem  yes           no              no                      removed
======================  ============  =============  ======================  ======================

The single most dangerous line in the codebase lives here:
:func:`delete_managed_root` re-validates ``paths.validate_destructive_root()``
*immediately* before ``shutil.rmtree`` and only ever deletes that exact resolved
path -- never a parent, never an unvalidated ``paths.root``. Default uninstall
and reinstall have no code path that reaches it.
"""

from __future__ import annotations

import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Sequence

from . import clients as clients_mod
from . import migration as migration_mod
from . import ollama as ollama_mod
from . import processes as processes_mod
from . import runtime as runtime_mod
from . import verification as verification_mod
from .output import Reporter, confirm_full_wipe
from .paths import ManagedPaths
from .state import (
    begin_operation,
    complete_operation,
    detect_state,
    fail_operation,
)
from .types import (
    DetectedState,
    InstallStateKind,
    Operation,
    OperationResult,
    OperationStatus,
)


# --------------------------------------------------------------------------- #
# Default seam adapters
#
# These thin wrappers give the context concrete defaults that call the real
# primitives. Tests replace any of them with fakes to assert call order and
# semantics without real side effects.
# --------------------------------------------------------------------------- #


class SourceValidationError(RuntimeError):
    """Raised when the source tree / Python executable cannot be used to build."""


def _real_validate_source(context: "OperationContext") -> None:
    # source root must exist and look like an installable package; python executable, if absolute, must exist (a bare name resolves later in the subprocess)
    source_root = Path(context.source_root)
    if not source_root.exists():
        raise SourceValidationError(f"Source root does not exist: {source_root}")
    if not (source_root / "pyproject.toml").exists():
        raise SourceValidationError(
            f"Source root is not an installable package (missing pyproject.toml): {source_root}"
        )
    python = Path(context.python_executable)
    # A bare name (e.g. "python3") is allowed -- resolution happens in the
    # subprocess; only reject an absolute path that is clearly absent.
    if python.is_absolute() and not python.exists():
        raise SourceValidationError(f"Python executable not found: {python}")


def _real_list_registrations(paths: ManagedPaths) -> tuple[dict[str, Any], ...]:
    # broken/unreadable registry -> empty tuple, never abort a lifecycle op over it
    from .. import mcp_server_lifecycle as lifecycle

    try:
        return tuple(lifecycle.list_servers())
    except Exception:  # noqa: BLE001 - a broken registry must never abort a lifecycle op
        return ()


def _real_remove_runtime(paths: ManagedPaths, **kwargs: Any) -> runtime_mod.RemovalResult:
    return runtime_mod.remove_runtime(paths, **kwargs)


def _real_verify_installation(
    paths: ManagedPaths,
    expected_version: str | None,
    expected_clients: Sequence[str] = (),
    **kwargs: Any,
) -> verification_mod.VerificationReport:
    return verification_mod.verify_installation(
        paths, expected_version, expected_clients, **kwargs
    )


def delete_managed_root(paths: ManagedPaths) -> Path:
    # the one destructive line in the module: re-validates immediately before shutil.rmtree, only ever deletes that exact resolved path -- never a parent, never an unvalidated root
    resolved = paths.validate_destructive_root()
    if resolved.exists():
        shutil.rmtree(resolved)
    return resolved


# --------------------------------------------------------------------------- #
# Operation context
# --------------------------------------------------------------------------- #


@dataclass
class OperationContext:
    # everything an operation needs, plus injectable seams (the _fn fields below) so tests can drive the whole flow against fakes
    # selected_clients are the surfaces an explicit fresh/clean install registers; installing over an existing layout uses the on-disk snapshot instead
    paths: ManagedPaths
    source_root: Path
    python_executable: Path
    reporter: Reporter
    source_version: str
    selected_clients: list[str] = field(default_factory=list)
    client_selection_explicit: bool = False
    process_provider: Any | None = None
    clock: Callable[[], float] = time.time
    confirm: Callable[..., bool] = confirm_full_wipe

    # Injectable collaborators (default to the real primitives).
    validate_source_fn: Callable[["OperationContext"], None] = _real_validate_source
    detect_state_fn: Callable[[ManagedPaths], DetectedState] = detect_state
    list_registrations_fn: Callable[[ManagedPaths], tuple] = _real_list_registrations
    discover_processes_fn: Callable[..., tuple] = processes_mod.discover_owned_processes
    stop_processes_fn: Callable[..., Any] = processes_mod.stop_owned_processes
    unload_models_fn: Callable[..., tuple] = ollama_mod.unload_neo_models
    snapshot_clients_fn: Callable[[ManagedPaths], tuple] = clients_mod.snapshot_clients
    record_selection_fn: Callable[[ManagedPaths, list[str]], tuple] = clients_mod.record_selection
    remove_active_registrations_fn: Callable[..., tuple] = clients_mod.remove_active_registrations
    restore_clients_fn: Callable[..., tuple] = clients_mod.restore_recorded_registrations
    apply_client_selection_fn: Callable[..., Any] = clients_mod.apply_client_selection
    delete_registrations_fn: Callable[[ManagedPaths], None] = clients_mod.delete_registrations
    plan_migration_fn: Callable[[ManagedPaths], Any] = migration_mod.plan_migration
    apply_migration_fn: Callable[..., Any] = migration_mod.apply_migration
    build_candidate_fn: Callable[..., Any] = runtime_mod.build_candidate
    promote_candidate_fn: Callable[..., runtime_mod.PromotionResult] = runtime_mod.promote_candidate
    remove_runtime_fn: Callable[..., runtime_mod.RemovalResult] = _real_remove_runtime
    delete_root_fn: Callable[[ManagedPaths], Path] = delete_managed_root
    verify_installation_fn: Callable[..., Any] = _real_verify_installation


# --------------------------------------------------------------------------- #
# Internal helpers
# --------------------------------------------------------------------------- #


def _result(
    operation: Operation,
    status: OperationStatus,
    actions: Sequence[str],
    warnings: Sequence[str],
) -> OperationResult:
    return OperationResult(
        operation=operation,
        status=status,
        actions=tuple(actions),
        warnings=tuple(warnings),
    )


def _neo_config_path(paths: ManagedPaths) -> Path:
    return paths.config / "config.yaml"


def _stop_owned_processes(ctx: OperationContext, actions: list[str], warnings: list[str]) -> bool:
    # discover -> stop -> True if all stopped (or none were owned)
    registrations = ctx.list_registrations_fn(ctx.paths)
    owned = ctx.discover_processes_fn(
        ctx.paths, registrations, provider=ctx.process_provider
    )
    result = ctx.stop_processes_fn(
        owned, registrations, provider=ctx.process_provider
    )
    for warning in getattr(result, "warnings", ()) or ():
        warnings.append(str(warning))
        ctx.reporter.warn(str(warning))
    if getattr(result, "ok", True):
        actions.append("stopped-owned-processes")
        return True
    warnings.append("Some owned processes could not be stopped.")
    return False


def _run_migration(ctx: OperationContext, actions: list[str], warnings: list[str]) -> bool:
    # no legacy actions planned -> no-op, True; else applies the plan (processes already stopped by this point) -> True iff it applied cleanly
    plan = ctx.plan_migration_fn(ctx.paths)
    if not getattr(plan, "actions", ()):
        return True
    result = ctx.apply_migration_fn(plan, processes_stopped=True)
    for warning in getattr(result, "warnings", ()) or ():
        warnings.append(str(warning))
        ctx.reporter.warn(str(warning))
    if getattr(result, "applied", False):
        actions.append("migrated-legacy-layout")
        return True
    ctx.reporter.error(
        f"Legacy layout migration failed: {getattr(result, 'error', 'unknown error')}."
    )
    return False


def _remove_active_clients(
    ctx: OperationContext, actions: list[str], warnings: list[str],
    *, selected_clients: Sequence[str] | None = None, forget: bool = False,
) -> bool:
    # removes automated registrations; an automated failure stops the lifecycle before runtime/root removal
    # Claude Desktop is intentionally manual-only -- its result is a visible warning, never a blocking failure
    try:
        results = ctx.remove_active_registrations_fn(
            ctx.paths, selected_clients=selected_clients, forget=forget,
        )
    except Exception as exc:  # noqa: BLE001 - convert cleanup crashes to lifecycle failure
        message = f"Client registration removal failed: {exc}"
        warnings.append(message)
        ctx.reporter.error(message)
        return False

    failed = False
    for result in results or ():
        client = str(result.get("client") or "unknown client")
        if result.get("manual_removal_required"):
            instructions = str(result.get("instructions") or "Manual removal is required.")
            message = f"{client}: {instructions}"
            warnings.append(message)
            ctx.reporter.warn(message)
            continue
        if result.get("ok", True) is False:
            detail = str(result.get("error") or "automated removal did not complete")
            message = f"{client} registration removal failed: {detail}"
            warnings.append(message)
            ctx.reporter.error(message)
            failed = True

    if failed:
        return False
    actions.append("removed-client-registrations")
    return True


def _build_and_promote(
    ctx: OperationContext, actions: list[str], warnings: list[str]
) -> runtime_mod.PromotionResult | None:
    # build candidate -> build failed -> None (no promotion attempted); else -> transactionally promote it, returning the promotion result either way
    operation_id = runtime_mod.new_operation_id()
    candidate = ctx.build_candidate_fn(
        ctx.paths,
        ctx.source_root,
        ctx.python_executable,
        operation_id=operation_id,
        reporter=ctx.reporter,
    )
    if not getattr(candidate, "build_ok", False):
        ctx.reporter.error(
            f"Candidate runtime build failed: {getattr(candidate, 'error', 'unknown error')}."
        )
        return None
    promotion = ctx.promote_candidate_fn(
        ctx.paths,
        candidate,
        expected_version=ctx.source_version,
        reporter=ctx.reporter,
    )
    for warning in getattr(promotion, "warnings", ()) or ():
        warnings.append(str(warning))
        ctx.reporter.warn(str(warning))
    if getattr(promotion, "ok", False):
        actions.append("promoted-runtime")
    return promotion


def _expected_client_keys(ctx: OperationContext) -> tuple[str, ...]:
    # currently-recorded client keys, used both to restore and as verify_installation's expected set
    return tuple(record.client for record in clients_mod.read_registrations(ctx.paths))


def _restore_and_verify(
    ctx: OperationContext, operation: Operation, actions: list[str], warnings: list[str]
) -> OperationResult:
    # only called after a successful runtime promotion: restore client registrations -> verify_installation -> finalize metadata
    # restore failure -> runtime is kept (not rolled back), operation fails visibly with recovery instructions
    server_command = ctx.paths.server_executable
    try:
        if ctx.client_selection_explicit:
            outcome = ctx.apply_client_selection_fn(
                ctx.paths, ctx.selected_clients, server_command=server_command,
            )
            if not outcome.ok:
                raise RuntimeError("; ".join(outcome.failures) or "client reconciliation failed")
            actions.append("reconciled-client-selection")
        else:
            ctx.restore_clients_fn(
                ctx.paths,
                server_command=server_command,
                neo_config_path=_neo_config_path(ctx.paths),
            )
            actions.append("restored-clients")
    except Exception as exc:  # noqa: BLE001 - restore failure is a recoverable, visible failure
        message = f"Client registration restore failed after runtime promotion: {exc}"
        ctx.reporter.error(message)
        ctx.reporter.warn(
            "Recovery: the managed runtime is intact and was NOT removed. "
            f"Re-run `python3 setup.py {operation.value}` to reconnect clients, "
            f"or point clients manually at {server_command}."
        )
        warnings.append(message)
        fail_operation(ctx.paths, error=message, now=ctx.clock())
        return _result(operation, OperationStatus.FAILED, actions, warnings)

    expected_clients = _expected_client_keys(ctx)
    report = ctx.verify_installation_fn(
        ctx.paths,
        ctx.source_version,
        expected_clients,
        reporter=ctx.reporter,
    )
    if not getattr(report, "ok", False):
        failed = getattr(report, "failed_required", ())
        detail = ", ".join(check.name for check in failed) or "unknown check"
        message = f"Installation verification failed: {detail}."
        ctx.reporter.error(message)
        for check in failed:
            if getattr(check, "recovery", ""):
                ctx.reporter.warn(f"Recovery ({check.name}): {check.recovery}")
        warnings.append(message)
        fail_operation(ctx.paths, error=message, now=ctx.clock())
        return _result(operation, OperationStatus.FAILED, actions, warnings)

    actions.append("verified-installation")
    complete_operation(
        ctx.paths,
        runtime_python=ctx.paths.python_executable,
        clients=expected_clients,
        now=ctx.clock(),
    )
    ctx.reporter.summary(
        f"{operation.value} succeeded",
        {"actions": ", ".join(actions), "clients": ", ".join(expected_clients) or "none"},
    )
    return _result(operation, OperationStatus.SUCCEEDED, actions, warnings)


def _record_client_intent(
    ctx: OperationContext, state: DetectedState, *, fresh: bool
) -> None:
    # fresh/clean install (or an absent root) -> record the explicit selection; else -> snapshot whatever's already registered on disk
    if ctx.client_selection_explicit or fresh or state.kind is InstallStateKind.ABSENT:
        ctx.record_selection_fn(ctx.paths, list(ctx.selected_clients))
    else:
        ctx.snapshot_clients_fn(ctx.paths)


def _install_like(
    ctx: OperationContext,
    operation: Operation,
    *,
    clean: bool,
) -> OperationResult:
    # shared install/reinstall spine: validate source -> stop owned processes -> unload models -> (clean: wipe root first) -> record client intent -> migrate legacy layout -> build+promote -> restore+verify
    actions: list[str] = []
    warnings: list[str] = []

    ctx.validate_source_fn(ctx)
    actions.append("validated-source")

    state = ctx.detect_state_fn(ctx.paths)

    begin_operation(
        ctx.paths,
        operation,
        source_version=ctx.source_version,
        clients=tuple(ctx.selected_clients),
        now=ctx.clock(),
    )

    # Stop owned processes before touching the runtime.
    if not _stop_owned_processes(ctx, actions, warnings):
        message = "Could not stop owned processes; aborting to avoid a partial replacement."
        ctx.reporter.error(message)
        warnings.append(message)
        fail_operation(ctx.paths, error=message, now=ctx.clock())
        return _result(operation, OperationStatus.FAILED, actions, warnings)

    ctx.unload_models_fn()
    actions.append("unloaded-models")

    # Clean install: destroy the whole validated root, then re-create the layout
    # and record the freshly-selected surfaces (never reuse deleted records).
    if clean:
        if not _remove_active_clients(ctx, actions, warnings):
            message = "Could not remove active client registrations; clean install aborted."
            warnings.append(message)
            fail_operation(ctx.paths, error=message, now=ctx.clock())
            return _result(operation, OperationStatus.FAILED, actions, warnings)
        ctx.delete_registrations_fn(ctx.paths)
        ctx.delete_root_fn(ctx.paths)
        actions.append("wiped-managed-root")
        ctx.paths.ensure_directories()
        begin_operation(
            ctx.paths,
            operation,
            source_version=ctx.source_version,
            clients=tuple(ctx.selected_clients),
            now=ctx.clock(),
        )

    # Client records: explicit selection for fresh/clean, snapshot otherwise.
    _record_client_intent(ctx, state, fresh=clean)

    # Migrate a recognized legacy layout (no-op otherwise). Never on a clean root.
    if not clean and not _run_migration(ctx, actions, warnings):
        message = "Legacy layout migration failed; aborting."
        warnings.append(message)
        fail_operation(ctx.paths, error=message, now=ctx.clock())
        return _result(operation, OperationStatus.FAILED, actions, warnings)

    promotion = _build_and_promote(ctx, actions, warnings)
    if promotion is None or not getattr(promotion, "ok", False):
        # promote_candidate already restored the previous runtime on failure; we
        # restore the prior registrations so a rolled-back install leaves working
        # client configs, then fail visibly.
        if promotion is not None and getattr(promotion, "rolled_back", False):
            try:
                ctx.restore_clients_fn(
                    ctx.paths,
                    server_command=ctx.paths.server_executable,
                    neo_config_path=_neo_config_path(ctx.paths),
                )
                actions.append("restored-prior-clients")
            except Exception as exc:  # noqa: BLE001 - best-effort restoration
                warnings.append(f"Could not restore prior client registrations: {exc}")
        message = "Runtime promotion failed; previous runtime restored."
        ctx.reporter.error(message)
        warnings.append(message)
        fail_operation(ctx.paths, error=message, now=ctx.clock())
        return _result(operation, OperationStatus.FAILED, actions, warnings)

    return _restore_and_verify(ctx, operation, actions, warnings)


# --------------------------------------------------------------------------- #
# Public operations
# --------------------------------------------------------------------------- #


def install(
    context: OperationContext,
    *,
    clean: bool = False,
    assume_yes: bool = False,
) -> OperationResult:
    # default: preserves durable data, restores (or registers, for a fresh root) clients
    # clean=True: destroys the whole managed root first (gated behind confirmation), rebuilds from the selected surfaces only
    if clean:
        if not context.confirm(context.paths, assume_yes=assume_yes):
            context.reporter.warn("Clean install cancelled; no data was deleted.")
            return _result(Operation.INSTALL, OperationStatus.CANCELLED, (), ())
    return _install_like(context, Operation.INSTALL, clean=clean)


def reinstall(context: OperationContext) -> OperationResult:
    # transactional runtime replacement; never deletes a durable directory -- build+promote only ever removes/recreates venv/
    return _install_like(context, Operation.REINSTALL, clean=False)


def uninstall(
    context: OperationContext,
    *,
    delete_memory: bool = False,
    assume_yes: bool = False,
) -> OperationResult:
    # default: removes venv/ only, after client cleanup; preserves all durable data, does not recreate
    # delete_memory=True: full wipe of the entire validated root (gated behind confirmation), no reinstall
    ctx = context
    if ctx.client_selection_explicit and ctx.selected_clients:
        actions: list[str] = []
        warnings: list[str] = []
        if not _remove_active_clients(
            ctx, actions, warnings, selected_clients=ctx.selected_clients, forget=True,
        ):
            return _result(Operation.UNINSTALL, OperationStatus.FAILED, actions, warnings)
        ctx.reporter.summary(
            "client detach succeeded",
            {"clients": ", ".join(ctx.selected_clients), "note": "runtime and durable memory/data preserved"},
        )
        return _result(Operation.UNINSTALL, OperationStatus.SUCCEEDED, actions, warnings)
    if delete_memory:
        if not ctx.confirm(ctx.paths, assume_yes=assume_yes):
            ctx.reporter.warn("Full wipe cancelled; no data was deleted.")
            return _result(Operation.UNINSTALL, OperationStatus.CANCELLED, (), ())

    actions: list[str] = []
    warnings: list[str] = []

    state = ctx.detect_state_fn(ctx.paths)

    begin_operation(
        ctx.paths,
        Operation.UNINSTALL,
        source_version=ctx.source_version,
        now=ctx.clock(),
    )

    if not _stop_owned_processes(ctx, actions, warnings):
        message = "Could not stop owned processes; aborting uninstall."
        ctx.reporter.error(message)
        warnings.append(message)
        fail_operation(ctx.paths, error=message, now=ctx.clock())
        return _result(Operation.UNINSTALL, OperationStatus.FAILED, actions, warnings)

    ctx.unload_models_fn()
    actions.append("unloaded-models")

    # Remove live client registrations (records retained for a later reinstall
    # unless this is a full wipe, which deletes the whole root anyway).
    if not _remove_active_clients(ctx, actions, warnings):
        message = "Could not remove active client registrations; uninstall aborted."
        warnings.append(message)
        fail_operation(ctx.paths, error=message, now=ctx.clock())
        return _result(Operation.UNINSTALL, OperationStatus.FAILED, actions, warnings)

    if delete_memory:
        # Full wipe: delete the entire validated root. No venv-only step, no
        # reinstall. delete_root_fn re-validates the destructive root itself.
        ctx.delete_root_fn(ctx.paths)
        actions.append("wiped-managed-root")
        ctx.reporter.summary(
            "uninstall --delete-memory succeeded",
            {"actions": ", ".join(actions)},
        )
        # Metadata lived under the now-deleted root; nothing left to complete.
        return _result(Operation.UNINSTALL, OperationStatus.SUCCEEDED, actions, warnings)

    # Default uninstall: remove ONLY venv/. Durable directories untouched.
    removal = ctx.remove_runtime_fn(ctx.paths, reporter=ctx.reporter)
    if not getattr(removal, "ok", False):
        message = f"Could not remove managed runtime: {getattr(removal, 'error', 'unknown error')}."
        ctx.reporter.error(message)
        warnings.append(message)
        fail_operation(ctx.paths, error=message, now=ctx.clock())
        return _result(Operation.UNINSTALL, OperationStatus.FAILED, actions, warnings)
    actions.append("removed-runtime")

    complete_operation(ctx.paths, runtime_python=None, clients=(), now=ctx.clock())
    ctx.reporter.summary(
        "uninstall succeeded",
        {"actions": ", ".join(actions), "note": "durable memory/data preserved"},
    )
    return _result(Operation.UNINSTALL, OperationStatus.SUCCEEDED, actions, warnings)
