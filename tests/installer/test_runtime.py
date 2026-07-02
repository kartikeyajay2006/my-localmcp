from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path
from typing import Sequence

import pytest

import neo_localmcp
from neo_localmcp.installer.paths import ManagedPaths
from neo_localmcp.installer.runtime import (
    CandidateRuntime,
    CommandResult,
    RuntimeValidation,
    build_candidate,
    install_command,
    installed_location,
    location_for_venv,
    pip_upgrade_command,
    promote_candidate,
    rehome_scripts,
    remove_runtime,
    validate_candidate,
    venv_command,
)

REPO_ROOT = Path(__file__).resolve().parents[2]


def _paths(tmp_path: Path) -> ManagedPaths:
    return ManagedPaths(
        root=tmp_path / ".neo-localmcp",
        platform="posix",
        home=tmp_path,
    )


def _classify(args: tuple[str, ...]) -> str:
    joined = " ".join(args)
    if "-m venv" in joined:
        return "venv"
    if "install --upgrade pip" in joined:
        return "pip-upgrade"
    if "--force-reinstall" in joined:
        return "install"
    if "sys.version_info" in joined:
        return "interpreter"
    if "neo_localmcp.__version__" in joined:
        return "package-version"
    if joined.endswith("--help"):
        return "cli-help"
    if "import neo_localmcp.server" in joined:
        return "server-import"
    return "other"


class FakeCommandRunner:
    """Records argument arrays and returns configurable results by command kind."""

    def __init__(
        self,
        *,
        version: str = "1.0.9",
        interpreter: str = "3.12.4",
        fail_kinds: Sequence[str] = (),
        fail_path_contains: Sequence[str] = (),
    ) -> None:
        self.calls: list[tuple[str, ...]] = []
        self.version = version
        self.interpreter = interpreter
        self.fail_kinds = set(fail_kinds)
        self.fail_path_contains = tuple(fail_path_contains)

    def run(
        self,
        args: Sequence[str],
        *,
        log_path: Path | None = None,
        timeout: float | None = None,
    ) -> CommandResult:
        del log_path, timeout
        recorded = tuple(str(argument) for argument in args)
        self.calls.append(recorded)
        kind = _classify(recorded)
        joined = " ".join(recorded)
        failing = kind in self.fail_kinds or any(
            needle in joined for needle in self.fail_path_contains
        )
        if failing:
            return CommandResult(recorded, 1, "", f"forced failure: {kind}")
        stdout = ""
        if kind == "interpreter":
            stdout = self.interpreter
        elif kind == "package-version":
            stdout = self.version
        return CommandResult(recorded, 0, stdout, "")


class RecordingFileSystem:
    """Real filesystem operations with injectable move/remove failures."""

    def __init__(
        self,
        *,
        fail_move_src: Path | None = None,
        fail_remove_path: Path | None = None,
    ) -> None:
        self.moves: list[tuple[Path, Path]] = []
        self.removes: list[Path] = []
        self.fail_move_src = fail_move_src
        self.fail_remove_path = fail_remove_path

    def exists(self, path: Path) -> bool:
        return Path(path).exists()

    def ensure_parent(self, path: Path) -> None:
        Path(path).parent.mkdir(parents=True, exist_ok=True)

    def move(self, source: Path, destination: Path) -> None:
        if self.fail_move_src is not None and Path(source) == self.fail_move_src:
            raise OSError(f"move of {source} refused")
        self.ensure_parent(destination)
        os.replace(Path(source), Path(destination))
        self.moves.append((Path(source), Path(destination)))

    def remove_tree(self, path: Path) -> None:
        if self.fail_remove_path is not None and Path(path) == self.fail_remove_path:
            raise OSError(f"remove of {path} refused")
        target = Path(path)
        if target.exists():
            shutil.rmtree(target)
            self.removes.append(target)


def _make_venv_dir(venv: Path) -> Path:
    bindir = venv / "bin"
    bindir.mkdir(parents=True)
    for name in ("python", "neo-localmcp", "neo-localmcp-server"):
        executable = bindir / name
        executable.write_text("#!/bin/sh\n")
        executable.chmod(0o755)
    return venv


def _candidate(paths: ManagedPaths, *, build_ok: bool = True, op_id: str = "op-1") -> CandidateRuntime:
    staging = paths.cache / "runtime-staging" / op_id
    candidate_venv = staging / "venv"
    if build_ok:
        _make_venv_dir(candidate_venv)
    location = location_for_venv(candidate_venv, "posix", "")
    return CandidateRuntime(
        operation_id=op_id,
        staging_dir=staging,
        location=location,
        build_ok=build_ok,
        commands=(),
        log_path=paths.logs / f"runtime-build-{op_id}.log",
        error=None if build_ok else "build failed",
    )


# --------------------------------------------------------------------------- #
# Step 1: command construction uses argument arrays, never shell strings
# --------------------------------------------------------------------------- #


def test_command_builders_return_argument_arrays() -> None:
    created = venv_command("/usr/bin/python3", "/tmp/staging/venv")
    upgrade = pip_upgrade_command("/tmp/staging/venv/bin/python")
    install = install_command("/tmp/staging/venv/bin/python", "/checkout")

    for command in (created, upgrade, install):
        assert isinstance(command, tuple)
        assert not isinstance(command, str)
        assert all(isinstance(part, str) for part in command)

    assert created == ("/usr/bin/python3", "-m", "venv", "/tmp/staging/venv")
    assert upgrade == (
        "/tmp/staging/venv/bin/python",
        "-m",
        "pip",
        "install",
        "--upgrade",
        "pip",
    )
    assert install == (
        "/tmp/staging/venv/bin/python",
        "-m",
        "pip",
        "install",
        "--upgrade",
        "--force-reinstall",
        "/checkout",
    )


def test_build_candidate_runs_expected_command_arrays(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    runner = FakeCommandRunner()

    candidate = build_candidate(
        paths,
        source_root=REPO_ROOT,
        python_executable=Path("/usr/bin/python3.12"),
        operation_id="op-build",
        runner=runner,
    )

    assert candidate.build_ok is True
    assert candidate.staging_dir == paths.cache / "runtime-staging" / "op-build"
    assert candidate.venv == candidate.staging_dir / "venv"
    # Every recorded call is an argument array, not a single shell string.
    assert all(isinstance(call, tuple) and len(call) >= 2 for call in runner.calls)
    kinds = [_classify(call) for call in runner.calls]
    assert kinds == ["venv", "pip-upgrade", "install"]
    candidate_python = candidate.venv / "bin" / "python"
    assert runner.calls[0] == venv_command(Path("/usr/bin/python3.12"), candidate.venv)
    assert runner.calls[1] == pip_upgrade_command(candidate_python)
    assert runner.calls[2] == install_command(candidate_python, REPO_ROOT)


def test_build_candidate_stops_at_first_failing_command(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    runner = FakeCommandRunner(fail_kinds=("pip-upgrade",))

    candidate = build_candidate(
        paths,
        source_root=REPO_ROOT,
        python_executable=Path("/usr/bin/python3.12"),
        operation_id="op-fail",
        runner=runner,
    )

    assert candidate.build_ok is False
    assert candidate.error is not None
    # Never attempted the install after the upgrade failed.
    assert [_classify(call) for call in runner.calls] == ["venv", "pip-upgrade"]


# --------------------------------------------------------------------------- #
# Step 4: candidate validation
# --------------------------------------------------------------------------- #


def test_validate_candidate_passes_for_matching_version(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    candidate = _candidate(paths)
    runner = FakeCommandRunner(version="1.0.9")

    validation = validate_candidate(candidate, "1.0.9", runner=runner)

    assert isinstance(validation, RuntimeValidation)
    assert validation.ok is True
    assert validation.version == "1.0.9"
    assert {check.name for check in validation.checks} >= {
        "python-executable",
        "cli-executable",
        "interpreter-version",
        "package-version",
        "cli-help",
        "server-import",
    }


def test_validate_candidate_fails_on_version_mismatch(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    candidate = _candidate(paths)
    runner = FakeCommandRunner(version="0.0.1")

    validation = validate_candidate(candidate, "1.0.9", runner=runner)

    assert validation.ok is False
    assert any(
        check.name == "package-version" and not check.ok for check in validation.checks
    )


def test_validate_candidate_fails_on_old_interpreter(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    candidate = _candidate(paths)
    runner = FakeCommandRunner(interpreter="3.11.9")

    validation = validate_candidate(candidate, "1.0.9", runner=runner)

    assert validation.ok is False
    assert any(
        check.name == "interpreter-version" and not check.ok
        for check in validation.checks
    )


def test_validate_candidate_fails_when_executables_missing(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    candidate = _candidate(paths, build_ok=False)  # no venv dir created
    runner = FakeCommandRunner()

    validation = validate_candidate(candidate, "1.0.9", runner=runner)

    assert validation.ok is False
    assert any(
        check.name == "python-executable" and not check.ok for check in validation.checks
    )


# --------------------------------------------------------------------------- #
# Step 2: promotion / rollback matrix
# --------------------------------------------------------------------------- #


def test_promote_replaces_healthy_current_runtime(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    _make_venv_dir(paths.venv)
    (paths.venv / "MARKER-OLD").write_text("old")
    candidate = _candidate(paths)
    (candidate.venv / "MARKER-NEW").write_text("new")
    runner = FakeCommandRunner(version="1.0.9")
    fs = RecordingFileSystem()

    result = promote_candidate(
        paths, candidate, expected_version="1.0.9", runner=runner, filesystem=fs
    )

    assert result.ok is True
    assert result.promoted is True
    assert result.previous_runtime_removed is True
    assert (paths.venv / "MARKER-NEW").exists()
    assert not (paths.venv / "MARKER-OLD").exists()
    rollback = paths.cache / "runtime-rollback" / candidate.operation_id
    assert not rollback.exists()


def test_promote_into_absent_runtime(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    candidate = _candidate(paths)
    runner = FakeCommandRunner(version="1.0.9")
    fs = RecordingFileSystem()

    result = promote_candidate(
        paths, candidate, expected_version="1.0.9", runner=runner, filesystem=fs
    )

    assert result.ok is True
    assert result.promoted is True
    assert result.previous_runtime_removed is False
    assert (paths.venv / "bin" / "python").exists()


def test_promote_replaces_broken_current_runtime(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    # A broken current runtime: directory exists but is missing executables.
    paths.venv.mkdir(parents=True)
    (paths.venv / "stale-file").write_text("broken")
    candidate = _candidate(paths)
    runner = FakeCommandRunner(version="1.0.9")
    fs = RecordingFileSystem()

    result = promote_candidate(
        paths, candidate, expected_version="1.0.9", runner=runner, filesystem=fs
    )

    assert result.ok is True
    assert (paths.venv / "bin" / "python").exists()
    assert not (paths.venv / "stale-file").exists()


def test_promote_refuses_failed_candidate_build(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    _make_venv_dir(paths.venv)
    (paths.venv / "MARKER-OLD").write_text("old")
    candidate = _candidate(paths, build_ok=False)
    fs = RecordingFileSystem()

    result = promote_candidate(
        paths, candidate, expected_version="1.0.9", filesystem=fs
    )

    assert result.ok is False
    assert result.promoted is False
    # The prior runtime is completely untouched.
    assert fs.moves == []
    assert (paths.venv / "MARKER-OLD").exists()


def test_promote_refuses_candidate_that_fails_validation(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    _make_venv_dir(paths.venv)
    (paths.venv / "MARKER-OLD").write_text("old")
    candidate = _candidate(paths)
    runner = FakeCommandRunner(version="0.0.1")  # wrong version
    fs = RecordingFileSystem()

    result = promote_candidate(
        paths, candidate, expected_version="1.0.9", runner=runner, filesystem=fs
    )

    assert result.ok is False
    assert result.promoted is False
    assert fs.moves == []
    assert (paths.venv / "MARKER-OLD").exists()


def test_promote_restores_previous_runtime_on_move_failure(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    _make_venv_dir(paths.venv)
    (paths.venv / "MARKER-OLD").write_text("old")
    candidate = _candidate(paths)
    runner = FakeCommandRunner(version="1.0.9")
    # Fail exactly the candidate -> venv move.
    fs = RecordingFileSystem(fail_move_src=candidate.venv)

    result = promote_candidate(
        paths, candidate, expected_version="1.0.9", runner=runner, filesystem=fs
    )

    assert result.ok is False
    assert result.promoted is False
    assert result.rolled_back is True
    # The previous healthy runtime is runnable again at its canonical path.
    assert (paths.venv / "MARKER-OLD").exists()
    assert (paths.venv / "bin" / "python").exists()


def test_promote_restores_previous_runtime_on_final_validation_failure(
    tmp_path: Path,
) -> None:
    paths = _paths(tmp_path)
    _make_venv_dir(paths.venv)
    (paths.venv / "MARKER-OLD").write_text("old")
    candidate = _candidate(paths)
    # Candidate (staging path) validates, but any command against the final
    # installed venv path fails -> final validation fails -> rollback restored.
    runner = FakeCommandRunner(
        version="1.0.9", fail_path_contains=(str(paths.venv),)
    )
    fs = RecordingFileSystem()

    result = promote_candidate(
        paths, candidate, expected_version="1.0.9", runner=runner, filesystem=fs
    )

    assert result.ok is False
    assert result.promoted is False
    assert result.rolled_back is True
    assert (paths.venv / "MARKER-OLD").exists()
    assert (paths.venv / "bin" / "python").exists()


def test_promote_succeeds_but_warns_when_backup_cleanup_fails(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    _make_venv_dir(paths.venv)
    candidate = _candidate(paths)
    (candidate.venv / "MARKER-NEW").write_text("new")
    runner = FakeCommandRunner(version="1.0.9")
    rollback = paths.cache / "runtime-rollback" / candidate.operation_id
    fs = RecordingFileSystem(fail_remove_path=rollback)

    result = promote_candidate(
        paths, candidate, expected_version="1.0.9", runner=runner, filesystem=fs
    )

    # Promotion still succeeds; the new runtime is live and the failure is a warning.
    assert result.ok is True
    assert result.promoted is True
    assert result.previous_runtime_removed is False
    assert result.warnings
    assert (paths.venv / "MARKER-NEW").exists()


# --------------------------------------------------------------------------- #
# Shebang rehoming: a moved venv keeps staging-path shebangs
# --------------------------------------------------------------------------- #


def test_rehome_scripts_rewrites_staging_shebangs(tmp_path: Path) -> None:
    bindir = tmp_path / "venv" / "bin"
    bindir.mkdir(parents=True)
    old_prefix = "/staging/op-1/venv"
    new_prefix = str(tmp_path / "venv")
    script = bindir / "neo-localmcp"
    script.write_text(f"#!{old_prefix}/bin/python\nprint('hi')\n")
    # A symlinked interpreter must not be rewritten.
    (bindir / "python").symlink_to("/usr/bin/python3")

    rehomed = rehome_scripts(bindir, old_prefix, new_prefix)

    assert "neo-localmcp" in rehomed
    assert script.read_text().splitlines()[0] == f"#!{new_prefix}/bin/python"


def test_promotion_rehomes_moved_candidate_shebangs(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    candidate = _candidate(paths)
    # Rewrite the candidate's cli script to a realistic staging-path shebang.
    cli = candidate.venv / "bin" / "neo-localmcp"
    cli.write_text(f"#!{candidate.venv}/bin/python\nprint('cli')\n")
    runner = FakeCommandRunner(version="1.0.9")
    fs = RecordingFileSystem()

    result = promote_candidate(
        paths, candidate, expected_version="1.0.9", runner=runner, filesystem=fs
    )

    assert result.ok is True
    promoted_cli = paths.venv / "bin" / "neo-localmcp"
    assert promoted_cli.read_text().splitlines()[0] == f"#!{paths.venv}/bin/python"


# --------------------------------------------------------------------------- #
# remove_runtime: runtime only, never durable data
# --------------------------------------------------------------------------- #


def test_remove_runtime_removes_only_venv(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    paths.ensure_directories()
    _make_venv_dir(paths.venv)
    (paths.memory / "note.md").write_text("keep me")
    (paths.sqlite / "repo.sqlite").write_text("data")

    result = remove_runtime(paths)

    assert result.ok is True
    assert result.removed is True
    assert not paths.venv.exists()
    # Durable directories survive.
    for durable in paths.durable_directories:
        assert durable.exists()
    assert (paths.memory / "note.md").read_text() == "keep me"


def test_remove_runtime_is_noop_when_absent(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    paths.ensure_directories()

    result = remove_runtime(paths)

    assert result.ok is True
    assert result.removed is False


# --------------------------------------------------------------------------- #
# Step 6: real candidate build in a temporary home (marked slow)
# --------------------------------------------------------------------------- #


@pytest.mark.slow
def test_real_candidate_build_and_promote(tmp_path: Path) -> None:
    paths = ManagedPaths(root=tmp_path / ".neo-localmcp", platform="posix", home=tmp_path)
    paths.ensure_directories()
    expected_version = neo_localmcp.__version__

    candidate = build_candidate(
        paths,
        source_root=REPO_ROOT,
        python_executable=Path(sys.executable),
        operation_id="real-build",
    )
    assert candidate.build_ok is True, candidate.error

    validation = validate_candidate(candidate, expected_version)
    assert validation.ok is True, [c for c in validation.checks if not c.ok]
    assert validation.version == expected_version

    result = promote_candidate(paths, candidate, expected_version=expected_version)
    assert result.ok is True, result.error
    assert result.promoted is True

    final = installed_location(paths)
    assert final.python_executable.exists()
    assert final.cli_executable.exists()
