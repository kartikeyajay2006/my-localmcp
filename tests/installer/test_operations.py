"""Tests for the setup-v2 lifecycle composition layer (``operations.py``).

These tests exercise ``install``/``reinstall``/``uninstall`` against *fakes* for
every side-effecting primitive so the ordered-call sequence, the semantic matrix
(state x operation), and the confirmation/cancellation gates can be asserted
without touching real venvs, processes, networks, or the user's home directory.

Where a test asserts real destructive behavior (whole-root deletion, venv-only
removal), it operates on a temporary ``allow_test_root=True`` managed root and
lets the real filesystem primitives run.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from neo_localmcp.installer import operations
from neo_localmcp.installer.operations import OperationContext, install, reinstall, uninstall
from neo_localmcp.installer.output import Reporter
from neo_localmcp.installer.paths import ManagedPaths
from neo_localmcp.installer.runtime import (
    PromotionResult,
    RuntimeValidation,
    RemovalResult,
)
from neo_localmcp.installer.state import complete_operation, detect_state
from neo_localmcp.installer.types import (
    DetectedState,
    InstallStateKind,
    Operation,
    OperationStatus,
)
from neo_localmcp.installer.verification import VerificationCheck, VerificationReport


REPO_ROOT = Path(__file__).resolve().parents[2]
EXPECTED_VERSION = "1.0.9"


def _paths(tmp_path: Path) -> ManagedPaths:
    return ManagedPaths(
        root=tmp_path / ".neo-localmcp",
        platform="posix",
        home=tmp_path,
        allow_test_root=True,
    )


# --------------------------------------------------------------------------- #
# Fake collaborators
# --------------------------------------------------------------------------- #


@dataclass
class FakeCandidate:
    build_ok: bool = True
    error: str | None = None
    location: Any = None


@dataclass
class Recorder:
    """Central call ledger + configurable fake outcomes for every seam."""

    calls: list[str] = field(default_factory=list)

    # Configurable outcomes
    state: InstallStateKind = InstallStateKind.ABSENT
    state_details: dict[str, Any] = field(default_factory=dict)
    build_ok: bool = True
    promotion_ok: bool = True
    promotion_rolled_back: bool = False
    verify_ok: bool = True
    migration_actions: tuple = ()
    remove_runtime_ok: bool = True
    restore_raises: bool = False
    stop_ok: bool = True
    client_removal_results: tuple[dict[str, Any], ...] = ()

    # Side-effect trackers
    venv_removed: bool = False
    root_deleted: bool = False
    registrations_deleted: bool = False
    restored: bool = False
    active_removed: bool = False
    recorded_selection: tuple[str, ...] = ()
    snapshotted: bool = False

    def record(self, name: str) -> None:
        self.calls.append(name)

    # --- validate source/python ---
    def validate_source(self, ctx: Any) -> None:
        self.record("validate_source")

    # --- detect ---
    def detect_state(self, paths: ManagedPaths) -> DetectedState:
        self.record("detect_state")
        return DetectedState(kind=self.state, details=dict(self.state_details))

    # --- process provider is passed through; discover/stop are seams ---
    def list_registrations(self, paths: ManagedPaths) -> tuple:
        self.record("list_registrations")
        return ()

    def discover_processes(self, paths, registrations, *, provider=None) -> tuple:
        self.record("discover_processes")
        return ()

    def stop_processes(self, owned, registrations, *, provider=None, **kwargs):
        self.record("stop_processes")

        @dataclass(frozen=True)
        class _R:
            ok: bool
            timed_out: tuple = ()
            warnings: tuple = ()

        return _R(ok=self.stop_ok)

    # --- ollama ---
    def unload_models(self, timeout_per_model: float = 5.0) -> tuple:
        self.record("unload_models")
        return ()

    # --- clients ---
    def snapshot_clients(self, paths: ManagedPaths) -> tuple:
        self.record("snapshot_clients")
        self.snapshotted = True
        return ()

    def record_selection(self, paths: ManagedPaths, clients: list[str]) -> tuple:
        self.record("record_selection")
        self.recorded_selection = tuple(clients)
        return ()

    def remove_active_registrations(self, paths: ManagedPaths, *, apply: bool = True) -> tuple:
        self.record("remove_active_registrations")
        self.active_removed = True
        return self.client_removal_results

    def restore_clients(self, paths, *, server_command, neo_config_path, apply=True) -> tuple:
        self.record("restore_clients")
        if self.restore_raises:
            raise RuntimeError("client restore boom")
        self.restored = True
        return ()

    def delete_registrations(self, paths: ManagedPaths) -> None:
        self.record("delete_registrations")
        self.registrations_deleted = True

    # --- migration ---
    def plan_migration(self, paths: ManagedPaths):
        self.record("plan_migration")

        @dataclass(frozen=True)
        class _Plan:
            actions: tuple = ()
            conflicts: tuple = ()
            requires_process_stop: bool = False

        return _Plan(actions=self.migration_actions)

    def apply_migration(self, plan, *, processes_stopped: bool = False):
        self.record("apply_migration")

        @dataclass(frozen=True)
        class _MR:
            applied: bool = True
            warnings: tuple = ()
            error: str | None = None

        return _MR()

    # --- runtime ---
    def build_candidate(self, paths, source_root, python_executable, *, operation_id=None, **kwargs):
        self.record("build_candidate")
        return FakeCandidate(build_ok=self.build_ok, error=None if self.build_ok else "build failed")

    def promote_candidate(self, paths, candidate, *, expected_version=None, **kwargs):
        self.record("promote_candidate")
        return PromotionResult(
            ok=self.promotion_ok,
            promoted=self.promotion_ok,
            rolled_back=self.promotion_rolled_back,
            previous_runtime_removed=False,
            validation=RuntimeValidation(ok=self.promotion_ok, version=EXPECTED_VERSION),
            error=None if self.promotion_ok else "promotion failed",
        )

    def remove_runtime(self, paths, **kwargs) -> RemovalResult:
        self.record("remove_runtime")
        self.venv_removed = True
        return RemovalResult(ok=self.remove_runtime_ok, removed=True, error=None)

    def delete_root(self, paths: ManagedPaths) -> Path:
        self.record("delete_root")
        self.root_deleted = True
        return paths.root

    # --- verification ---
    def verify_installation(self, paths, expected_version, expected_clients=(), **kwargs):
        self.record("verify_installation")
        checks = ()
        if not self.verify_ok:
            checks = (
                VerificationCheck(
                    name="mcp-initialize-handshake",
                    required=True,
                    ok=False,
                    details="handshake failed",
                    recovery="reinstall",
                ),
            )
        return VerificationReport(ok=self.verify_ok, checks=checks, version=EXPECTED_VERSION)


def _context(
    tmp_path: Path,
    recorder: Recorder,
    *,
    confirm=None,
    selected_clients=("claude-code",),
) -> OperationContext:
    paths = _paths(tmp_path)
    paths.ensure_directories()
    reporter = Reporter(output_fn=lambda _msg: None)
    return OperationContext(
        paths=paths,
        source_root=REPO_ROOT,
        python_executable=Path("/usr/bin/python3"),
        reporter=reporter,
        source_version=EXPECTED_VERSION,
        selected_clients=list(selected_clients),
        process_provider=object(),
        clock=lambda: 1000.0,
        confirm=confirm if confirm is not None else (lambda *a, **k: True),
        # injected seams
        validate_source_fn=recorder.validate_source,
        detect_state_fn=recorder.detect_state,
        list_registrations_fn=recorder.list_registrations,
        discover_processes_fn=recorder.discover_processes,
        stop_processes_fn=recorder.stop_processes,
        unload_models_fn=recorder.unload_models,
        snapshot_clients_fn=recorder.snapshot_clients,
        record_selection_fn=recorder.record_selection,
        remove_active_registrations_fn=recorder.remove_active_registrations,
        restore_clients_fn=recorder.restore_clients,
        delete_registrations_fn=recorder.delete_registrations,
        plan_migration_fn=recorder.plan_migration,
        apply_migration_fn=recorder.apply_migration,
        build_candidate_fn=recorder.build_candidate,
        promote_candidate_fn=recorder.promote_candidate,
        remove_runtime_fn=recorder.remove_runtime,
        delete_root_fn=recorder.delete_root,
        verify_installation_fn=recorder.verify_installation,
    )


# --------------------------------------------------------------------------- #
# Step 1: Ordered-call test
# --------------------------------------------------------------------------- #


def test_install_ordered_sequence(tmp_path):
    # A non-empty migration plan ensures apply_migration participates in the spine.
    recorder = Recorder(state=InstallStateKind.HEALTHY, migration_actions=("legacy",))
    ctx = _context(tmp_path, recorder)

    result = install(ctx)

    assert result.status is OperationStatus.SUCCEEDED
    # Required ordered spine from the brief. Filter to the spine names so that
    # incidental helper calls do not make this brittle, but the RELATIVE order of
    # the spine must match exactly.
    spine = [
        "validate_source",
        "detect_state",
        "stop_processes",
        "unload_models",
        "snapshot_clients",
        "apply_migration",
        "build_candidate",
        "promote_candidate",
        "restore_clients",
        "verify_installation",
    ]
    filtered = [c for c in recorder.calls if c in spine]
    assert filtered == spine, recorder.calls
    # Metadata completed (succeeded) at the end.
    state = detect_state(ctx.paths)
    assert state.kind is not InstallStateKind.PARTIAL_OPERATION


def test_install_absent_records_selection_not_snapshot(tmp_path):
    recorder = Recorder(state=InstallStateKind.ABSENT)
    ctx = _context(tmp_path, recorder, selected_clients=("claude-code", "codex"))

    install(ctx)

    # A fresh install records the explicit selection rather than probing disk.
    assert recorder.recorded_selection == ("claude-code", "codex")
    assert "record_selection" in recorder.calls
    assert "snapshot_clients" not in recorder.calls


# --------------------------------------------------------------------------- #
# Step 2: Semantic matrix (executable, parametrized over state x operation)
# --------------------------------------------------------------------------- #

_ALL_STATES = [
    InstallStateKind.ABSENT,
    InstallStateKind.DATA_ONLY,
    InstallStateKind.HEALTHY,
    InstallStateKind.BROKEN_RUNTIME,
]


@pytest.mark.parametrize("state", _ALL_STATES)
def test_matrix_install(tmp_path, state):
    recorder = Recorder(state=state)
    ctx = _context(tmp_path, recorder)
    result = install(ctx)

    assert result.status is OperationStatus.SUCCEEDED
    # install: recreates venv (promote), preserves durable data (never deletes
    # root), active clients restored/selected.
    assert "promote_candidate" in recorder.calls  # recreates venv
    assert not recorder.root_deleted  # durable data preserved
    assert not recorder.registrations_deleted
    # clients end active (restored or selected+restored)
    assert recorder.restored


@pytest.mark.parametrize("state", _ALL_STATES)
def test_matrix_reinstall(tmp_path, state):
    recorder = Recorder(state=state)
    ctx = _context(tmp_path, recorder)
    result = reinstall(ctx)

    assert result.status is OperationStatus.SUCCEEDED
    assert "promote_candidate" in recorder.calls  # removes+recreates venv transactionally
    assert not recorder.root_deleted  # never deletes durable directories
    assert not recorder.registrations_deleted
    assert recorder.restored  # clients restored


@pytest.mark.parametrize("state", _ALL_STATES)
def test_matrix_uninstall(tmp_path, state):
    recorder = Recorder(state=state)
    ctx = _context(tmp_path, recorder)
    result = uninstall(ctx)

    assert result.status is OperationStatus.SUCCEEDED
    assert recorder.venv_removed  # removes venv
    assert "promote_candidate" not in recorder.calls  # does NOT recreate venv
    assert "build_candidate" not in recorder.calls
    assert not recorder.root_deleted  # preserves durable data
    assert recorder.active_removed  # clients removed
    assert not recorder.restored


@pytest.mark.parametrize("state", _ALL_STATES)
def test_matrix_install_clean(tmp_path, state):
    recorder = Recorder(state=state)
    ctx = _context(tmp_path, recorder, confirm=lambda *a, **k: True)
    result = install(ctx, clean=True, assume_yes=True)

    assert result.status is OperationStatus.SUCCEEDED
    assert recorder.root_deleted  # does NOT preserve durable data
    assert recorder.registrations_deleted  # deleted records not reused
    assert "record_selection" in recorder.calls  # newly selected only
    assert "promote_candidate" in recorder.calls  # recreates venv
    assert recorder.restored  # newly selected clients active


@pytest.mark.parametrize("state", _ALL_STATES)
def test_matrix_uninstall_delete_memory(tmp_path, state):
    recorder = Recorder(state=state)
    ctx = _context(tmp_path, recorder, confirm=lambda *a, **k: True)
    result = uninstall(ctx, delete_memory=True, assume_yes=True)

    assert result.status is OperationStatus.SUCCEEDED
    assert recorder.root_deleted  # full wipe: does NOT preserve durable data
    assert recorder.active_removed  # clients removed
    assert "promote_candidate" not in recorder.calls  # does not reinstall
    assert not recorder.restored


# --------------------------------------------------------------------------- #
# Step 3: Confirmation / cancellation
# --------------------------------------------------------------------------- #


def test_clean_install_refuses_non_interactive_without_yes(tmp_path):
    recorder = Recorder(state=InstallStateKind.HEALTHY)
    # confirm returns False (would-be interactive prompt declined / not answered)
    ctx = _context(tmp_path, recorder, confirm=lambda *a, **k: False)

    result = install(ctx, clean=True, assume_yes=False)

    assert result.status is OperationStatus.CANCELLED
    assert not recorder.root_deleted  # no destructive call
    assert not recorder.registrations_deleted
    assert "delete_root" not in recorder.calls


def test_delete_memory_refuses_without_confirmation(tmp_path):
    recorder = Recorder(state=InstallStateKind.HEALTHY)
    ctx = _context(tmp_path, recorder, confirm=lambda *a, **k: False)

    result = uninstall(ctx, delete_memory=True, assume_yes=False)

    assert result.status is OperationStatus.CANCELLED
    assert not recorder.root_deleted
    assert "delete_root" not in recorder.calls


def test_clean_install_interactive_cancel_records_cancelled(tmp_path):
    recorder = Recorder(state=InstallStateKind.HEALTHY)
    calls = {"n": 0}

    def _decline(*a, **k):
        calls["n"] += 1
        return False

    ctx = _context(tmp_path, recorder, confirm=_decline)
    result = install(ctx, clean=True, assume_yes=False)

    assert calls["n"] == 1  # confirmation was actually consulted
    assert result.status is OperationStatus.CANCELLED
    assert not recorder.root_deleted


# --------------------------------------------------------------------------- #
# Step 6: Recovery paths
# --------------------------------------------------------------------------- #


def test_client_restore_failure_keeps_runtime_and_fails_visibly(tmp_path):
    recorder = Recorder(state=InstallStateKind.HEALTHY, restore_raises=True)
    messages: list[str] = []
    ctx = _context(tmp_path, recorder)
    ctx.reporter._output_fn = messages.append  # type: ignore[attr-defined]

    result = install(ctx)

    assert result.status is OperationStatus.FAILED
    # runtime was promoted and is NOT removed on client-restore failure
    assert "promote_candidate" in recorder.calls
    assert not recorder.venv_removed
    assert not recorder.root_deleted
    # recovery guidance surfaced, and points at the real setup entrypoint
    # rather than the removed `neo-localmcp setup` command.
    joined = "\n".join(messages).lower()
    assert "recover" in joined or "recovery" in joined
    assert "setup.py" in joined
    assert "neo-localmcp setup" not in joined
    # failure recorded in metadata
    state = detect_state(ctx.paths)
    assert state.kind is not InstallStateKind.PARTIAL_OPERATION


@pytest.mark.parametrize(
    ("operation", "destructive_call"),
    [
        (lambda ctx: uninstall(ctx), "remove_runtime"),
        (lambda ctx: uninstall(ctx, delete_memory=True, assume_yes=True), "delete_root"),
        (lambda ctx: install(ctx, clean=True, assume_yes=True), "delete_root"),
    ],
)
def test_failed_automated_client_removal_aborts_before_destructive_step(
    tmp_path, operation, destructive_call
):
    recorder = Recorder(
        state=InstallStateKind.HEALTHY,
        client_removal_results=(
            {"client": "claude-code", "ok": False, "error": "remove command failed"},
        ),
    )
    ctx = _context(tmp_path, recorder)

    result = operation(ctx)

    assert result.status is OperationStatus.FAILED
    assert destructive_call not in recorder.calls
    assert "removed-client-registrations" not in result.actions
    assert any("claude-code" in warning for warning in result.warnings)


def test_manual_client_removal_is_a_warning_not_a_lifecycle_failure(tmp_path):
    recorder = Recorder(
        state=InstallStateKind.HEALTHY,
        client_removal_results=(
            {
                "client": "claude-desktop",
                "ok": False,
                "manual_removal_required": True,
                "instructions": "Remove the extension in Claude Desktop.",
            },
        ),
    )
    ctx = _context(tmp_path, recorder)

    result = uninstall(ctx)

    assert result.status is OperationStatus.SUCCEEDED
    assert "remove_runtime" in recorder.calls
    assert any("claude-desktop" in warning for warning in result.warnings)


def test_promotion_failure_restores_registrations_and_fails(tmp_path):
    recorder = Recorder(
        state=InstallStateKind.HEALTHY,
        promotion_ok=False,
        promotion_rolled_back=True,
    )
    ctx = _context(tmp_path, recorder)

    result = install(ctx)

    assert result.status is OperationStatus.FAILED
    # never advanced to verification on promotion failure
    assert "verify_installation" not in recorder.calls
    # runtime restoration is promote_candidate's job; we must not have deleted data
    assert not recorder.root_deleted


def test_verification_failure_marks_operation_failed(tmp_path):
    recorder = Recorder(state=InstallStateKind.HEALTHY, verify_ok=False)
    ctx = _context(tmp_path, recorder)

    result = install(ctx)

    assert result.status is OperationStatus.FAILED
    assert "verify_installation" in recorder.calls


# --------------------------------------------------------------------------- #
# Step 5: Real destructive-root deletion safety (real filesystem)
# --------------------------------------------------------------------------- #


def test_delete_managed_root_targets_only_validated_root(tmp_path):
    paths = _paths(tmp_path)
    paths.ensure_directories()
    # a durable file to prove it gets deleted
    (paths.memory / "keep.db").write_text("x", encoding="utf-8")
    sibling = tmp_path / "sibling"
    sibling.mkdir()
    (sibling / "safe.txt").write_text("safe", encoding="utf-8")

    resolved = operations.delete_managed_root(paths)

    assert resolved == paths.root.resolve()
    assert not paths.root.exists()
    # nothing outside the validated root was touched
    assert (sibling / "safe.txt").exists()


def test_delete_managed_root_refuses_unvalidated_root(tmp_path):
    from neo_localmcp.installer.paths import UnsafeManagedRoot

    # allow_test_root=False + non ".neo-localmcp" name => must refuse
    paths = ManagedPaths(root=tmp_path / "not-managed", platform="posix", home=tmp_path)
    (tmp_path / "not-managed").mkdir()
    with pytest.raises(UnsafeManagedRoot):
        operations.delete_managed_root(paths)
    assert (tmp_path / "not-managed").exists()


def test_default_uninstall_never_reaches_delete_root(tmp_path):
    recorder = Recorder(state=InstallStateKind.HEALTHY)
    ctx = _context(tmp_path, recorder)
    uninstall(ctx, delete_memory=False)
    assert "delete_root" not in recorder.calls
    assert not recorder.root_deleted


def test_reinstall_never_reaches_delete_root(tmp_path):
    recorder = Recorder(state=InstallStateKind.HEALTHY)
    ctx = _context(tmp_path, recorder)
    reinstall(ctx)
    assert "delete_root" not in recorder.calls
    assert not recorder.registrations_deleted


def test_real_default_uninstall_removes_only_venv(tmp_path):
    """End-to-end with real filesystem removal for the venv-only guarantee."""
    paths = _paths(tmp_path)
    paths.ensure_directories()
    paths.venv.mkdir(parents=True, exist_ok=True)
    (paths.venv / "marker").write_text("v", encoding="utf-8")
    (paths.memory / "keep.db").write_text("db", encoding="utf-8")

    reporter = Reporter(output_fn=lambda _m: None)
    recorder = Recorder(state=InstallStateKind.HEALTHY)
    ctx = _context(tmp_path, recorder)
    # use the REAL remove_runtime + REAL delete-root guard for this test
    ctx.remove_runtime_fn = operations._real_remove_runtime  # type: ignore[attr-defined]

    uninstall(ctx, delete_memory=False)

    assert not paths.venv.exists()
    assert (paths.memory / "keep.db").exists()
    assert paths.root.exists()
