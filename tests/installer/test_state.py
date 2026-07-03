from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from neo_localmcp.installer.paths import ManagedPaths
from neo_localmcp.installer.state import (
    MetadataCorruptError,
    begin_operation,
    complete_operation,
    detect_state,
    fail_operation,
)
from neo_localmcp.installer.types import InstallStateKind, Operation


def _paths(tmp_path: Path) -> ManagedPaths:
    return ManagedPaths(
        root=tmp_path / ".neo-localmcp",
        platform="posix",
        home=tmp_path,
    )


def _write_runnable_python(paths: ManagedPaths, *, import_ok: bool = True) -> None:
    paths.python_executable.parent.mkdir(parents=True, exist_ok=True)
    exit_code = 0 if import_ok else 1
    paths.python_executable.write_text(
        f"#!/bin/sh\nexit {exit_code}\n",
        encoding="utf-8",
    )
    paths.python_executable.chmod(0o755)


def test_detects_absent_state(tmp_path: Path) -> None:
    state = detect_state(_paths(tmp_path))

    assert state.kind is InstallStateKind.ABSENT
    assert state.details["reason"] == "root_missing"


def test_detects_data_only_state(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    paths.config.mkdir(parents=True)

    state = detect_state(paths)

    assert state.kind is InstallStateKind.DATA_ONLY
    assert state.details["reason"] == "venv_missing"


def test_detects_healthy_runtime(tmp_path: Path) -> None:
    if os.name == "nt":
        pytest.skip("POSIX executable fixture; Windows runtime is covered by its lifecycle test")
    paths = _paths(tmp_path)
    _write_runnable_python(paths)

    state = detect_state(paths)

    assert state.kind is InstallStateKind.HEALTHY
    assert state.details["reason"] == "runtime_probe_succeeded"


def test_detects_broken_runtime_when_python_is_missing(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    paths.venv.mkdir(parents=True)

    state = detect_state(paths)

    assert state.kind is InstallStateKind.BROKEN_RUNTIME
    assert state.details["reason"] == "python_missing"


def test_detects_broken_runtime_when_package_import_fails(tmp_path: Path) -> None:
    if os.name == "nt":
        pytest.skip("POSIX executable fixture; Windows broken-runtime recovery has real coverage")
    paths = _paths(tmp_path)
    _write_runnable_python(paths, import_ok=False)

    state = detect_state(paths)

    assert state.kind is InstallStateKind.BROKEN_RUNTIME
    assert state.details["reason"] == "package_missing"


def test_detects_legacy_layout_before_data_only(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    paths.root.mkdir(parents=True)
    (paths.root / "config.yaml").write_text("{}", encoding="utf-8")

    state = detect_state(paths)

    assert state.kind is InstallStateKind.LEGACY_LAYOUT
    assert state.details["reason"] == "legacy_paths_present"
    assert state.details["legacy_paths"] == ("config.yaml",)


def test_detects_interrupted_operation_from_metadata(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    begin_operation(
        paths,
        Operation.REINSTALL,
        source_version="1.0.10",
        now=100.0,
    )

    state = detect_state(paths)

    assert state.kind is InstallStateKind.PARTIAL_OPERATION
    assert state.details["reason"] == "metadata_in_progress"
    assert state.details["operation"] == "reinstall"


def test_detects_corrupt_metadata_without_overwriting_it(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    paths.install_metadata.parent.mkdir(parents=True)
    paths.install_metadata.write_text("not-json", encoding="utf-8")

    state = detect_state(paths)

    assert state.kind is InstallStateKind.PARTIAL_OPERATION
    assert state.details["reason"] == "metadata_corrupt"
    assert "warning" in state.details
    assert paths.install_metadata.read_text(encoding="utf-8") == "not-json"

    with pytest.raises(MetadataCorruptError):
        begin_operation(
            paths,
            Operation.INSTALL,
            source_version="1.0.10",
            now=100.0,
        )
    assert paths.install_metadata.read_text(encoding="utf-8") == "not-json"


def test_metadata_transitions_are_atomic_and_preserve_schema(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths = _paths(tmp_path)
    replacements: list[tuple[Path, Path]] = []
    original_replace = os.replace

    def recording_replace(source: str | Path, destination: str | Path) -> None:
        replacements.append((Path(source), Path(destination)))
        original_replace(source, destination)

    monkeypatch.setattr("neo_localmcp.installer.state.os.replace", recording_replace)

    started = begin_operation(
        paths,
        Operation.INSTALL,
        source_version="1.0.10",
        runtime_python=paths.python_executable,
        clients=("codex", "claude-code"),
        now=100.0,
    )
    completed = complete_operation(paths, now=120.0)

    assert started["schema_version"] == 1
    assert started["status"] == "in_progress"
    assert completed["status"] == "succeeded"
    assert completed["completed_at"] == 120.0
    assert completed["runtime_python"] == str(paths.python_executable)
    assert completed["clients"] == ["codex", "claude-code"]
    assert completed["error"] is None
    assert replacements == [
        (paths.install_metadata.with_suffix(".json.tmp"), paths.install_metadata),
        (paths.install_metadata.with_suffix(".json.tmp"), paths.install_metadata),
    ]
    assert not paths.install_metadata.with_suffix(".json.tmp").exists()
    assert json.loads(paths.install_metadata.read_text(encoding="utf-8")) == completed


def test_fail_operation_records_error_and_completion_time(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    begin_operation(
        paths,
        Operation.UNINSTALL,
        source_version="1.0.10",
        now=100.0,
    )

    failed = fail_operation(paths, error="runtime locked", now=101.0)

    assert failed["status"] == "failed"
    assert failed["completed_at"] == 101.0
    assert failed["error"] == "runtime locked"
