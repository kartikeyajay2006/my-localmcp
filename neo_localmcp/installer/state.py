"""Installation-state detection and atomic lifecycle metadata transitions."""

from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path
from typing import Any

from .paths import ManagedPaths
from .types import DetectedState, InstallStateKind, Operation

METADATA_SCHEMA_VERSION = 1
RUNTIME_PROBE_TIMEOUT_SECONDS = 5.0


class MetadataError(RuntimeError):
    """Base class for lifecycle metadata failures."""


class MetadataCorruptError(MetadataError):
    """Raised when existing metadata cannot be trusted or replaced safely."""


class MetadataMissingError(MetadataError):
    """Raised when a transition has no operation metadata to update."""


def _read_metadata(paths: ManagedPaths) -> dict[str, Any] | None:
    # no file -> None (not an error); unparseable/wrong-schema/bad-status -> MetadataCorruptError, never silently trusted
    if not paths.install_metadata.exists():
        return None
    try:
        payload = json.loads(paths.install_metadata.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise MetadataCorruptError(f"Cannot read install metadata: {exc}") from exc
    if not isinstance(payload, dict):
        raise MetadataCorruptError("Install metadata must be a JSON object")
    if payload.get("schema_version") != METADATA_SCHEMA_VERSION:
        raise MetadataCorruptError(
            f"Unsupported install metadata schema: {payload.get('schema_version')!r}"
        )
    if payload.get("status") not in {"in_progress", "succeeded", "failed"}:
        raise MetadataCorruptError(
            f"Invalid install metadata status: {payload.get('status')!r}"
        )
    return payload


def _write_metadata(paths: ManagedPaths, payload: dict[str, Any]) -> dict[str, Any]:
    # tmp-write + os.replace -> atomic; a crash mid-write can never leave half-written metadata
    paths.install_metadata.parent.mkdir(parents=True, exist_ok=True)
    temporary = paths.install_metadata.with_suffix(".json.tmp")
    temporary.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, paths.install_metadata)
    return payload


def begin_operation(
    paths: ManagedPaths,
    operation: Operation,
    *,
    source_version: str,
    runtime_python: Path | None = None,
    clients: tuple[str, ...] = (),
    now: float | None = None,
) -> dict[str, Any]:
    # writes a fresh "in_progress" record; validates any existing metadata first (raises if corrupt) before overwriting it
    if paths.install_metadata.exists():
        _read_metadata(paths)
    started_at = time.time() if now is None else float(now)
    payload: dict[str, Any] = {
        "schema_version": METADATA_SCHEMA_VERSION,
        "operation": operation.value,
        "status": "in_progress",
        "source_version": str(source_version),
        "started_at": started_at,
        "completed_at": None,
        "runtime_python": str(runtime_python) if runtime_python is not None else None,
        "clients": list(clients),
        "error": None,
    }
    return _write_metadata(paths, payload)


def complete_operation(
    paths: ManagedPaths,
    *,
    runtime_python: Path | None = None,
    clients: tuple[str, ...] | None = None,
    now: float | None = None,
) -> dict[str, Any]:
    # in_progress -> succeeded; requires an existing begin_operation record, raises MetadataMissingError otherwise
    payload = _read_metadata(paths)
    if payload is None:
        raise MetadataMissingError("No install operation metadata exists")
    payload["status"] = "succeeded"
    payload["completed_at"] = time.time() if now is None else float(now)
    payload["error"] = None
    if runtime_python is not None:
        payload["runtime_python"] = str(runtime_python)
    if clients is not None:
        payload["clients"] = list(clients)
    return _write_metadata(paths, payload)


def fail_operation(
    paths: ManagedPaths,
    *,
    error: str,
    now: float | None = None,
) -> dict[str, Any]:
    # in_progress -> failed, with the error recorded; same missing-record guard as complete_operation
    payload = _read_metadata(paths)
    if payload is None:
        raise MetadataMissingError("No install operation metadata exists")
    payload["status"] = "failed"
    payload["completed_at"] = time.time() if now is None else float(now)
    payload["error"] = str(error)
    return _write_metadata(paths, payload)


def _legacy_paths(paths: ManagedPaths) -> tuple[str, ...]:
    # any pre-managed-root layout artifacts (old flat files/dirs, versioned venv dirs) still present under root
    candidates = (
        "config.yaml",
        "repo-context.sqlite",
        "repo-context.sqlite-wal",
        "repo-context.sqlite-shm",
        "venvs",
        "bin",
        "current-venv.txt",
        "servers",
        "ollama-supervisor.json",
        "ollama-supervisor.lock",
        "neo-localmcp.mcpb",
    )
    found = [name for name in candidates if (paths.root / name).exists()]
    found.extend(
        child.name
        for child in sorted(paths.root.glob(".venv-nlm-v*"))
        if child.exists()
    )
    return tuple(sorted(set(found)))


def _runtime_imports(paths: ManagedPaths) -> bool:
    # spawns the managed venv's python and tries `import neo_localmcp`; any failure (missing binary, bad venv, timeout) -> False, never raises
    try:
        result = subprocess.run(
            [
                str(paths.python_executable),
                "-c",
                "import neo_localmcp; print(neo_localmcp.__version__)",
            ],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=RUNTIME_PROBE_TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return result.returncode == 0


def detect_state(paths: ManagedPaths) -> DetectedState:
    # root missing -> ABSENT; corrupt/in-progress metadata -> PARTIAL_OPERATION; legacy artifacts -> LEGACY_LAYOUT
    # venv present -> probe python+import -> BROKEN_RUNTIME or HEALTHY; else durable dirs present -> DATA_ONLY; else -> ABSENT (empty root)
    if not paths.root.exists():
        return DetectedState(
            kind=InstallStateKind.ABSENT,
            details={"reason": "root_missing"},
        )

    try:
        metadata = _read_metadata(paths)
    except MetadataCorruptError as exc:
        return DetectedState(
            kind=InstallStateKind.PARTIAL_OPERATION,
            details={"reason": "metadata_corrupt", "warning": str(exc)},
        )

    if metadata and metadata.get("status") == "in_progress":
        return DetectedState(
            kind=InstallStateKind.PARTIAL_OPERATION,
            details={
                "reason": "metadata_in_progress",
                "operation": metadata.get("operation"),
                "started_at": metadata.get("started_at"),
            },
        )

    legacy = _legacy_paths(paths)
    if legacy:
        return DetectedState(
            kind=InstallStateKind.LEGACY_LAYOUT,
            details={"reason": "legacy_paths_present", "legacy_paths": legacy},
        )

    if paths.venv.exists():
        if not paths.python_executable.is_file():
            return DetectedState(
                kind=InstallStateKind.BROKEN_RUNTIME,
                details={"reason": "python_missing"},
            )
        if not _runtime_imports(paths):
            return DetectedState(
                kind=InstallStateKind.BROKEN_RUNTIME,
                details={"reason": "package_missing"},
            )
        return DetectedState(
            kind=InstallStateKind.HEALTHY,
            details={"reason": "runtime_probe_succeeded"},
        )

    if any(directory.exists() for directory in paths.durable_directories):
        return DetectedState(
            kind=InstallStateKind.DATA_ONLY,
            details={"reason": "venv_missing"},
        )

    return DetectedState(
        kind=InstallStateKind.ABSENT,
        details={"reason": "root_empty"},
    )
