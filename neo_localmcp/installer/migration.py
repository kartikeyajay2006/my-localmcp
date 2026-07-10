"""Explicit, rollback-safe migration from recognized legacy install layouts."""

from __future__ import annotations

import json
import os
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from .paths import ManagedPaths

ActionKind = Literal["move", "discard"]


@dataclass(frozen=True)
class MigrationAction:
    kind: ActionKind
    category: str
    source: Path
    destination: Path


@dataclass(frozen=True)
class MigrationConflict:
    source: Path
    destination: Path
    reason: str


@dataclass(frozen=True)
class MigrationPlan:
    paths: ManagedPaths
    actions: tuple[MigrationAction, ...]
    conflicts: tuple[MigrationConflict, ...]
    unknown_paths: tuple[Path, ...]
    recognized_paths: tuple[Path, ...]
    requires_process_stop: bool


@dataclass(frozen=True)
class MigrationResult:
    applied: bool
    moved: tuple[tuple[str, str], ...]
    discarded: tuple[str, ...]
    conflicts: tuple[MigrationConflict, ...]
    warnings: tuple[str, ...]
    rolled_back: bool
    error: str | None


def _move_path(source: Path, destination: Path) -> None:
    # os.replace -> same-filesystem atomic rename; used for both forward moves and rollback (called in reverse)
    destination.parent.mkdir(parents=True, exist_ok=True)
    os.replace(source, destination)


def _add_action(
    actions: list[MigrationAction],
    recognized: set[Path],
    *,
    kind: ActionKind,
    category: str,
    source: Path,
    destination: Path,
) -> None:
    # source doesn't exist -> no-op, nothing to plan; else -> mark recognized (excludes it from unknown_paths) and queue the action
    if not source.exists():
        return
    recognized.add(source)
    actions.append(
        MigrationAction(
            kind=kind,
            category=category,
            source=source,
            destination=destination,
        )
    )


def plan_migration(paths: ManagedPaths) -> MigrationPlan:
    # legacy flat files/dirs -> move actions into the managed layout; old runtime artifacts (venvs, bin, current-venv.txt) -> discard actions into a trash dir
    # also computes destination-exists conflicts, unrecognized leftover paths, and whether any queued action needs processes stopped first
    if not paths.root.exists():
        return MigrationPlan(paths, (), (), (), (), False)

    actions: list[MigrationAction] = []
    recognized: set[Path] = {
        directory
        for directory in (
            paths.venv,
            paths.memory,
            paths.sqlite,
            paths.config,
            paths.clients,
            paths.logs,
            paths.cache,
        )
        if directory.exists()
    }

    _add_action(
        actions,
        recognized,
        kind="move",
        category="config",
        source=paths.root / "config.yaml",
        destination=paths.config / "config.yaml",
    )
    for name in (
        "repo-context.sqlite",
        "repo-context.sqlite-wal",
        "repo-context.sqlite-shm",
    ):
        _add_action(
            actions,
            recognized,
            kind="move",
            category="sqlite",
            source=paths.root / name,
            destination=paths.sqlite / name,
        )
    _add_action(
        actions,
        recognized,
        kind="move",
        category="process-state",
        source=paths.root / "servers",
        destination=paths.process_registry / "servers",
    )
    _add_action(
        actions,
        recognized,
        kind="move",
        category="ollama-state",
        source=paths.root / "ollama-supervisor.json",
        destination=paths.cache / "ollama" / "supervisor.json",
    )
    _add_action(
        actions,
        recognized,
        kind="move",
        category="ollama-state",
        source=paths.root / "ollama-supervisor.lock",
        destination=paths.cache / "ollama" / "supervisor.lock",
    )
    _add_action(
        actions,
        recognized,
        kind="move",
        category="package-cache",
        source=paths.root / "neo-localmcp.mcpb",
        destination=paths.cache / "packages" / "neo-localmcp.mcpb",
    )

    trash = paths.cache / "migration-trash"
    runtime_sources = [
        *sorted(paths.root.glob(".venv-nlm-v*")),
        paths.root / "venvs",
        paths.root / "bin",
        paths.root / "current-venv.txt",
    ]
    for source in runtime_sources:
        _add_action(
            actions,
            recognized,
            kind="discard",
            category="runtime",
            source=source,
            destination=trash / source.name,
        )

    conflicts = tuple(
        MigrationConflict(
            source=action.source,
            destination=action.destination,
            reason="destination_exists",
        )
        for action in actions
        if action.destination.exists()
    )
    unknown = tuple(
        sorted(
            child
            for child in paths.root.iterdir()
            if child not in recognized
        )
    )
    requires_stop = any(
        action.category in {"sqlite", "process-state", "runtime"}
        for action in actions
    )
    return MigrationPlan(
        paths=paths,
        actions=tuple(actions),
        conflicts=conflicts,
        unknown_paths=unknown,
        recognized_paths=tuple(sorted(recognized)),
        requires_process_stop=requires_stop,
    )


def _normalize_migrated_config(paths: ManagedPaths) -> tuple[bytes | None, str | None]:
    # a moved config.yaml may still point memory.db_path at the old root-level sqlite path -- rewrite it to the new managed location if so
    # returns (original bytes for rollback, warning) -- original is None when nothing needed rewriting, not on success
    destination = paths.config / "config.yaml"
    if not destination.exists():
        return None, None
    original = destination.read_bytes()
    try:
        payload = json.loads(original.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None, "Migrated config was preserved but its database path could not be normalized."
    if not isinstance(payload, dict):
        return None, "Migrated config was preserved but is not a JSON object."
    memory = payload.get("memory")
    if not isinstance(memory, dict):
        return None, None
    legacy_db = str(paths.root / "repo-context.sqlite")
    if str(memory.get("db_path") or "") != legacy_db:
        return None, None
    memory["db_path"] = str(paths.sqlite / "repo-context.sqlite")
    temporary = destination.with_suffix(".yaml.tmp")
    temporary.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, destination)
    return original, None


def apply_migration(
    plan: MigrationPlan,
    *,
    processes_stopped: bool = False,
) -> MigrationResult:
    # conflicts present, or a required process-stop hasn't happened yet -> refuse without touching disk
    # else: move/discard each action, journaling as it goes; any exception -> restore original config bytes + reverse-replay the journal, reporting rolled_back=True
    if plan.conflicts:
        return MigrationResult(
            applied=False,
            moved=(),
            discarded=(),
            conflicts=plan.conflicts,
            warnings=(),
            rolled_back=False,
            error="migration_conflicts",
        )
    if plan.requires_process_stop and not processes_stopped:
        return MigrationResult(
            applied=False,
            moved=(),
            discarded=(),
            conflicts=(),
            warnings=(),
            rolled_back=False,
            error="processes_must_be_stopped",
        )
    if not plan.actions:
        return MigrationResult(True, (), (), (), (), False, None)

    plan.paths.validate_destructive_root()
    journal: list[MigrationAction] = []
    original_config: bytes | None = None
    warnings: list[str] = []
    try:
        for action in plan.actions:
            _move_path(action.source, action.destination)
            journal.append(action)
        original_config, config_warning = _normalize_migrated_config(plan.paths)
        if config_warning:
            warnings.append(config_warning)
    except Exception as exc:
        config_destination = plan.paths.config / "config.yaml"
        if original_config is not None and config_destination.exists():
            config_destination.write_bytes(original_config)
        rollback_errors: list[str] = []
        for action in reversed(journal):
            if not action.destination.exists():
                continue
            try:
                _move_path(action.destination, action.source)
            except OSError as rollback_exc:
                rollback_errors.append(str(rollback_exc))
        if rollback_errors:
            warnings.append("Rollback errors: " + "; ".join(rollback_errors))
        return MigrationResult(
            applied=False,
            moved=(),
            discarded=(),
            conflicts=(),
            warnings=tuple(warnings),
            rolled_back=True,
            error=str(exc),
        )

    trash = plan.paths.cache / "migration-trash"
    if trash.exists():
        try:
            shutil.rmtree(trash)
        except OSError as exc:
            warnings.append(f"Disposable migration trash remains at {trash}: {exc}")

    moved = tuple(
        (str(action.source), str(action.destination))
        for action in plan.actions
        if action.kind == "move"
    )
    discarded = tuple(
        str(action.source) for action in plan.actions if action.kind == "discard"
    )
    return MigrationResult(
        applied=True,
        moved=moved,
        discarded=discarded,
        conflicts=(),
        warnings=tuple(warnings),
        rolled_back=False,
        error=None,
    )
