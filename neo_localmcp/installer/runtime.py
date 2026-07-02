"""Transactional managed-runtime build, validation, promotion, and removal.

A candidate virtual environment is always built and validated in an isolated
staging location before any healthy runtime is replaced. Promotion renames the
current runtime aside, moves the validated candidate into place, re-validates
from the final path, and only then deletes the backup. Any failure restores the
previous runtime, so a dependency, build, or validation failure can never leave
the managed root without a runnable ``venv/``.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol, Sequence

from .output import Reporter
from .paths import ManagedPaths, PlatformName

PYTHON_FLOOR: tuple[int, int] = (3, 12)

BUILD_COMMAND_TIMEOUT = 600.0
VALIDATION_COMMAND_TIMEOUT = 120.0

_INTERPRETER_SNIPPET = "import sys; print('%d.%d.%d' % sys.version_info[:3])"
_PACKAGE_VERSION_SNIPPET = "import neo_localmcp; print(neo_localmcp.__version__)"
_SERVER_IMPORT_SNIPPET = "import neo_localmcp.server"


# --------------------------------------------------------------------------- #
# Command construction (argument arrays only, never shell strings)
# --------------------------------------------------------------------------- #


def venv_command(python_executable: Path | str, candidate_venv: Path | str) -> tuple[str, ...]:
    return (str(python_executable), "-m", "venv", str(candidate_venv))


def pip_upgrade_command(candidate_python: Path | str) -> tuple[str, ...]:
    return (str(candidate_python), "-m", "pip", "install", "--upgrade", "pip")


def install_command(candidate_python: Path | str, source_root: Path | str) -> tuple[str, ...]:
    return (
        str(candidate_python),
        "-m",
        "pip",
        "install",
        "--upgrade",
        "--force-reinstall",
        str(source_root),
    )


@dataclass(frozen=True)
class CommandResult:
    args: tuple[str, ...]
    returncode: int
    stdout: str
    stderr: str

    @property
    def ok(self) -> bool:
        return self.returncode == 0


class CommandRunner(Protocol):
    def run(
        self,
        args: Sequence[str],
        *,
        log_path: Path | None = None,
        timeout: float | None = None,
    ) -> CommandResult: ...


class SubprocessCommandRunner:
    """Real command runner. Always passes an argument list; never uses a shell."""

    def run(
        self,
        args: Sequence[str],
        *,
        log_path: Path | None = None,
        timeout: float | None = None,
    ) -> CommandResult:
        arg_list = [str(argument) for argument in args]
        try:
            completed = subprocess.run(  # noqa: S603 - argument array, shell disabled
                arg_list,
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
            )
            result = CommandResult(
                args=tuple(arg_list),
                returncode=completed.returncode,
                stdout=completed.stdout or "",
                stderr=completed.stderr or "",
            )
        except subprocess.TimeoutExpired as exc:
            result = CommandResult(
                args=tuple(arg_list),
                returncode=124,
                stdout=(exc.stdout or "") if isinstance(exc.stdout, str) else "",
                stderr=f"timed out after {timeout}s",
            )
        except OSError as exc:
            result = CommandResult(
                args=tuple(arg_list),
                returncode=127,
                stdout="",
                stderr=str(exc),
            )
        _append_log(log_path, result)
        return result


def _append_log(log_path: Path | None, result: CommandResult) -> None:
    if log_path is None:
        return
    try:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with log_path.open("a", encoding="utf-8") as handle:
            handle.write("$ " + " ".join(result.args) + "\n")
            handle.write(f"[exit {result.returncode}]\n")
            if result.stdout:
                handle.write(result.stdout.rstrip("\n") + "\n")
            if result.stderr:
                handle.write("[stderr]\n" + result.stderr.rstrip("\n") + "\n")
            handle.write("\n")
    except OSError:
        # Logging must never break a lifecycle operation.
        pass


# --------------------------------------------------------------------------- #
# Filesystem primitives (injectable so promotion failures are testable)
# --------------------------------------------------------------------------- #


class RuntimeFileSystem(Protocol):
    def exists(self, path: Path) -> bool: ...

    def ensure_parent(self, path: Path) -> None: ...

    def move(self, source: Path, destination: Path) -> None: ...

    def remove_tree(self, path: Path) -> None: ...


def rehome_scripts(bindir: Path, old_prefix: str, new_prefix: str) -> tuple[str, ...]:
    """Rewrite absolute script shebangs after a venv is moved.

    Python ``venv`` console scripts (``pip``, ``neo-localmcp``, ...) embed an
    absolute shebang pointing at the interpreter path used when the venv was
    created. Moving the venv therefore breaks every script even though the
    ``bin/python`` symlink itself still resolves. Rewriting the first line of
    each affected script to the promoted prefix makes the moved venv runnable.

    Returns the names of the scripts that were rehomed. POSIX-only concern; on
    Windows the equivalent launchers are ``.exe`` files handled in Task 14.
    """

    directory = Path(bindir)
    if old_prefix == new_prefix or not directory.is_dir():
        return ()
    rehomed: list[str] = []
    for entry in sorted(directory.iterdir()):
        if entry.is_symlink() or not entry.is_file():
            continue
        try:
            with entry.open("rb") as handle:
                first_bytes = handle.readline()
        except OSError:
            continue
        if not first_bytes.startswith(b"#!"):
            continue
        try:
            text = entry.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        lines = text.split("\n")
        if old_prefix not in lines[0]:
            continue
        lines[0] = lines[0].replace(old_prefix, new_prefix)
        try:
            entry.write_text("\n".join(lines), encoding="utf-8")
        except OSError:
            continue
        rehomed.append(entry.name)
    return tuple(rehomed)


class RealRuntimeFileSystem:
    def exists(self, path: Path) -> bool:
        return Path(path).exists()

    def ensure_parent(self, path: Path) -> None:
        Path(path).parent.mkdir(parents=True, exist_ok=True)

    def move(self, source: Path, destination: Path) -> None:
        self.ensure_parent(destination)
        os.replace(Path(source), Path(destination))

    def remove_tree(self, path: Path) -> None:
        target = Path(path)
        if target.exists():
            shutil.rmtree(target)


# --------------------------------------------------------------------------- #
# Runtime locations
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class RuntimeLocation:
    venv: Path
    python_executable: Path
    cli_executable: Path
    server_executable: Path


def location_for_venv(
    venv: Path,
    platform: PlatformName,
    suffix: str,
) -> RuntimeLocation:
    executable_dir = Path(venv) / ("Scripts" if platform == "windows" else "bin")
    return RuntimeLocation(
        venv=Path(venv),
        python_executable=executable_dir / f"python{suffix}",
        cli_executable=executable_dir / f"neo-localmcp{suffix}",
        server_executable=executable_dir / f"neo-localmcp-server{suffix}",
    )


def installed_location(paths: ManagedPaths) -> RuntimeLocation:
    return RuntimeLocation(
        venv=paths.venv,
        python_executable=paths.python_executable,
        cli_executable=paths.cli_executable,
        server_executable=paths.server_executable,
    )


# --------------------------------------------------------------------------- #
# Candidate build
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class CandidateRuntime:
    operation_id: str
    staging_dir: Path
    location: RuntimeLocation
    build_ok: bool
    commands: tuple[CommandResult, ...]
    log_path: Path
    error: str | None = None

    @property
    def venv(self) -> Path:
        return self.location.venv

    @property
    def python_executable(self) -> Path:
        return self.location.python_executable

    @property
    def cli_executable(self) -> Path:
        return self.location.cli_executable

    @property
    def server_executable(self) -> Path:
        return self.location.server_executable


def new_operation_id() -> str:
    return f"{int(time.time())}-{uuid.uuid4().hex[:8]}"


def build_candidate(
    paths: ManagedPaths,
    source_root: Path,
    python_executable: Path,
    *,
    operation_id: str | None = None,
    runner: CommandRunner | None = None,
    reporter: Reporter | None = None,
) -> CandidateRuntime:
    """Build a candidate venv under ``cache/runtime-staging/<operation-id>/venv``.

    The current runtime is never touched here. Subprocess output is captured to a
    per-operation lifecycle log while concise progress is streamed to the reporter.
    """

    op_id = operation_id or new_operation_id()
    command_runner = runner or SubprocessCommandRunner()
    staging_dir = paths.cache / "runtime-staging" / op_id
    candidate_venv = staging_dir / "venv"
    location = location_for_venv(candidate_venv, paths.platform, paths.executable_suffix)
    log_path = paths.logs / f"runtime-build-{op_id}.log"

    staging_dir.mkdir(parents=True, exist_ok=True)
    log_path.parent.mkdir(parents=True, exist_ok=True)

    commands: list[CommandResult] = []

    def _fail(message: str) -> CandidateRuntime:
        if reporter is not None:
            reporter.error(message)
        return CandidateRuntime(
            operation_id=op_id,
            staging_dir=staging_dir,
            location=location,
            build_ok=False,
            commands=tuple(commands),
            log_path=log_path,
            error=message,
        )

    if reporter is not None:
        reporter.info("Creating candidate runtime.")
    create = command_runner.run(
        venv_command(python_executable, candidate_venv),
        log_path=log_path,
        timeout=BUILD_COMMAND_TIMEOUT,
    )
    commands.append(create)
    if not create.ok:
        return _fail("Candidate virtual environment creation failed.")

    if reporter is not None:
        reporter.info("Upgrading candidate packaging tools.")
    upgrade = command_runner.run(
        pip_upgrade_command(location.python_executable),
        log_path=log_path,
        timeout=BUILD_COMMAND_TIMEOUT,
    )
    commands.append(upgrade)
    if not upgrade.ok:
        return _fail("Candidate pip upgrade failed.")

    if reporter is not None:
        reporter.info("Installing neo-localmcp into the candidate runtime.")
    install = command_runner.run(
        install_command(location.python_executable, source_root),
        log_path=log_path,
        timeout=BUILD_COMMAND_TIMEOUT,
    )
    commands.append(install)
    if not install.ok:
        return _fail("Candidate dependency installation failed.")

    if reporter is not None:
        reporter.action("Candidate runtime built.")
    return CandidateRuntime(
        operation_id=op_id,
        staging_dir=staging_dir,
        location=location,
        build_ok=True,
        commands=tuple(commands),
        log_path=log_path,
        error=None,
    )


# --------------------------------------------------------------------------- #
# Validation
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class RuntimeCheck:
    name: str
    ok: bool
    details: str


@dataclass(frozen=True)
class RuntimeValidation:
    ok: bool
    version: str | None
    checks: tuple[RuntimeCheck, ...] = field(default_factory=tuple)


def _parse_version(text: str) -> tuple[int, ...]:
    parts: list[int] = []
    for chunk in text.strip().split("."):
        digits = "".join(ch for ch in chunk if ch.isdigit())
        if not digits:
            break
        parts.append(int(digits))
    return tuple(parts)


def _validate_location(
    location: RuntimeLocation,
    expected_version: str | None,
    *,
    runner: CommandRunner,
    python_floor: tuple[int, int],
    reporter: Reporter | None = None,
) -> RuntimeValidation:
    checks: list[RuntimeCheck] = []

    python_ok = Path(location.python_executable).exists()
    checks.append(
        RuntimeCheck("python-executable", python_ok, str(location.python_executable))
    )
    cli_ok = Path(location.cli_executable).exists()
    checks.append(RuntimeCheck("cli-executable", cli_ok, str(location.cli_executable)))

    interpreter = runner.run(
        (str(location.python_executable), "-c", _INTERPRETER_SNIPPET),
        timeout=VALIDATION_COMMAND_TIMEOUT,
    )
    interpreter_version = _parse_version(interpreter.stdout) if interpreter.ok else ()
    interpreter_ok = bool(interpreter.ok and interpreter_version[:2] >= python_floor)
    checks.append(
        RuntimeCheck(
            "interpreter-version",
            interpreter_ok,
            interpreter.stdout.strip() or interpreter.stderr.strip(),
        )
    )

    version_probe = runner.run(
        (str(location.python_executable), "-c", _PACKAGE_VERSION_SNIPPET),
        timeout=VALIDATION_COMMAND_TIMEOUT,
    )
    reported_version = version_probe.stdout.strip() if version_probe.ok else None
    if expected_version is None:
        version_ok = version_probe.ok and bool(reported_version)
    else:
        version_ok = version_probe.ok and reported_version == expected_version
    checks.append(
        RuntimeCheck(
            "package-version",
            version_ok,
            reported_version or version_probe.stderr.strip(),
        )
    )

    cli_help = runner.run(
        (str(location.cli_executable), "--help"),
        timeout=VALIDATION_COMMAND_TIMEOUT,
    )
    checks.append(
        RuntimeCheck("cli-help", cli_help.ok, cli_help.stderr.strip() or "ok")
    )

    server_import = runner.run(
        (str(location.python_executable), "-c", _SERVER_IMPORT_SNIPPET),
        timeout=VALIDATION_COMMAND_TIMEOUT,
    )
    checks.append(
        RuntimeCheck(
            "server-import", server_import.ok, server_import.stderr.strip() or "ok"
        )
    )

    ok = all(check.ok for check in checks)
    if reporter is not None and not ok:
        failed = ", ".join(check.name for check in checks if not check.ok)
        reporter.error(f"Runtime validation failed: {failed}.")
    return RuntimeValidation(ok=ok, version=reported_version, checks=tuple(checks))


def validate_candidate(
    candidate: CandidateRuntime,
    expected_version: str | None,
    *,
    runner: CommandRunner | None = None,
    reporter: Reporter | None = None,
    python_floor: tuple[int, int] = PYTHON_FLOOR,
) -> RuntimeValidation:
    command_runner = runner or SubprocessCommandRunner()
    return _validate_location(
        candidate.location,
        expected_version,
        runner=command_runner,
        python_floor=python_floor,
        reporter=reporter,
    )


# --------------------------------------------------------------------------- #
# Promotion and rollback
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class PromotionResult:
    ok: bool
    promoted: bool
    rolled_back: bool
    previous_runtime_removed: bool
    validation: RuntimeValidation | None
    error: str | None
    warnings: tuple[str, ...] = field(default_factory=tuple)


def promote_candidate(
    paths: ManagedPaths,
    candidate: CandidateRuntime,
    *,
    expected_version: str | None = None,
    runner: CommandRunner | None = None,
    reporter: Reporter | None = None,
    filesystem: RuntimeFileSystem | None = None,
    python_floor: tuple[int, int] = PYTHON_FLOOR,
) -> PromotionResult:
    """Transactionally replace ``venv/`` with a validated candidate.

    The previous runtime is renamed to a rollback path and only deleted after the
    candidate validates from its final installed location. Any promotion or
    validation failure restores the previous runtime.
    """

    command_runner = runner or SubprocessCommandRunner()
    fs = filesystem or RealRuntimeFileSystem()
    warnings: list[str] = []

    if not candidate.build_ok:
        return PromotionResult(
            ok=False,
            promoted=False,
            rolled_back=False,
            previous_runtime_removed=False,
            validation=None,
            error="Candidate runtime was not built successfully.",
            warnings=(),
        )

    # Validate the candidate in its staging location before disturbing the
    # current runtime; a bad candidate must never displace a healthy venv.
    candidate_validation = _validate_location(
        candidate.location,
        expected_version,
        runner=command_runner,
        python_floor=python_floor,
        reporter=reporter,
    )
    if not candidate_validation.ok:
        return PromotionResult(
            ok=False,
            promoted=False,
            rolled_back=False,
            previous_runtime_removed=False,
            validation=candidate_validation,
            error="Candidate runtime failed validation before promotion.",
            warnings=(),
        )

    target = paths.venv
    rollback = paths.cache / "runtime-rollback" / candidate.operation_id
    had_previous = fs.exists(target)

    if had_previous:
        try:
            fs.move(target, rollback)
        except OSError as exc:
            return PromotionResult(
                ok=False,
                promoted=False,
                rolled_back=False,
                previous_runtime_removed=False,
                validation=candidate_validation,
                error=f"Could not move the current runtime aside: {exc}",
                warnings=(),
            )

    try:
        fs.move(candidate.venv, target)
    except OSError as exc:
        rolled_back = False
        if had_previous:
            try:
                fs.move(rollback, target)
                rolled_back = True
            except OSError as restore_exc:
                warnings.append(
                    f"Could not restore previous runtime after promotion failure: {restore_exc}"
                )
        return PromotionResult(
            ok=False,
            promoted=False,
            rolled_back=rolled_back,
            previous_runtime_removed=False,
            validation=candidate_validation,
            error=f"Could not promote the candidate runtime: {exc}",
            warnings=tuple(warnings),
        )

    # A moved venv keeps script shebangs pointing at the staging path; rehome
    # them to the promoted location before validating from the final path.
    final_location = installed_location(paths)
    rehome_scripts(
        final_location.python_executable.parent,
        str(candidate.venv),
        str(target),
    )

    if reporter is not None:
        reporter.action("Validating promoted runtime.")
    final_validation = _validate_location(
        final_location,
        expected_version,
        runner=command_runner,
        python_floor=python_floor,
        reporter=reporter,
    )
    if not final_validation.ok:
        try:
            fs.remove_tree(target)
        except OSError as exc:
            warnings.append(f"Could not remove failed promoted runtime: {exc}")
        rolled_back = False
        if had_previous:
            try:
                fs.move(rollback, target)
                rolled_back = True
            except OSError as exc:
                warnings.append(f"Could not restore previous runtime: {exc}")
        return PromotionResult(
            ok=False,
            promoted=False,
            rolled_back=rolled_back,
            previous_runtime_removed=False,
            validation=final_validation,
            error="Promoted runtime failed final validation; previous runtime restored.",
            warnings=tuple(warnings),
        )

    previous_runtime_removed = False
    if had_previous:
        try:
            fs.remove_tree(rollback)
            previous_runtime_removed = True
        except OSError as exc:
            warnings.append(f"Could not remove previous runtime backup: {exc}")

    if reporter is not None:
        reporter.action("Runtime promoted.")
    return PromotionResult(
        ok=True,
        promoted=True,
        rolled_back=False,
        previous_runtime_removed=previous_runtime_removed,
        validation=final_validation,
        error=None,
        warnings=tuple(warnings),
    )


# --------------------------------------------------------------------------- #
# Removal (runtime only; never durable data)
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class RemovalResult:
    ok: bool
    removed: bool
    error: str | None


def remove_runtime(
    paths: ManagedPaths,
    *,
    filesystem: RuntimeFileSystem | None = None,
    reporter: Reporter | None = None,
) -> RemovalResult:
    """Remove only the managed ``venv/``. Durable directories are never touched."""

    fs = filesystem or RealRuntimeFileSystem()
    target = paths.venv
    if not fs.exists(target):
        return RemovalResult(ok=True, removed=False, error=None)
    try:
        fs.remove_tree(target)
    except OSError as exc:
        if reporter is not None:
            reporter.error(f"Could not remove managed runtime: {exc}")
        return RemovalResult(ok=False, removed=False, error=str(exc))
    if reporter is not None:
        reporter.action("Managed runtime removed.")
    return RemovalResult(ok=True, removed=True, error=None)
