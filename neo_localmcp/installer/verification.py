"""Post-install verification: define "installed" from the system's perspective.

Building files (``runtime.py``) and registering clients (``clients.py``) are
necessary but not sufficient for a successful install -- the managed Python
floor could be wrong, the package version mismatched, the CLI could fail to
start, the MCP server could fail its handshake, or a client config could still
point at a stale launcher. ``verify_installation`` is the single place that
answers "did the install actually work?" by re-probing the *installed*
artifacts directly (never trusting that a prior build/promote step succeeded),
using the same bounded-subprocess and managed-environment patterns as
``runtime.py``.

Checks are split into required and warning-only. Required checks failing means
the install is broken; warning-only checks (optional Ollama, manual Claude
Desktop installation) surface guidance without ever failing the report.
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Mapping, Protocol, Sequence

from .. import lifecycle
from .clients import CLAUDE_DESKTOP, read_registrations, verify_registrations
from .output import Reporter
from .paths import ManagedPaths
from .runtime import (
    PYTHON_FLOOR,
    CommandResult,
    CommandRunner,
    SubprocessCommandRunner,
    installed_location,
)

_INTERPRETER_SNIPPET = "import sys; print('%d.%d.%d' % sys.version_info[:3])"
_PACKAGE_VERSION_SNIPPET = "import neo_localmcp; print(neo_localmcp.__version__)"
_SERVER_IMPORT_SNIPPET = "import neo_localmcp.mcp.server"
_DOCTOR_SNIPPET = "import sys; from neo_localmcp.mcp.system import doctor; sys.stdout.write(doctor())"
_CANONICAL_PATHS_SNIPPET = (
    "import json; from neo_localmcp import config;"
    " print(json.dumps({'config_path': str(config.config_path()),"
    " 'db_path': str(config.default_db_path())}))"
)

VALIDATION_COMMAND_TIMEOUT = 120.0
HANDSHAKE_TIMEOUT = 20.0


class EnvCommandRunner(Protocol):
    """Like ``CommandRunner``, but for probes that must run under an explicit
    environment (``NEO_LOCALMCP_HOME``) rather than the caller's own."""

    def run(
        self,
        args: Sequence[str],
        *,
        env: Mapping[str, str],
        timeout: float | None = None,
    ) -> CommandResult: ...


class SubprocessEnvCommandRunner:
    """Real env-aware command runner. Argument array only, never a shell."""

    def run(
        self,
        args: Sequence[str],
        *,
        env: Mapping[str, str],
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
                env=dict(env),
            )
            return CommandResult(
                args=tuple(arg_list),
                returncode=completed.returncode,
                stdout=completed.stdout or "",
                stderr=completed.stderr or "",
            )
        except subprocess.TimeoutExpired as exc:
            return CommandResult(
                args=tuple(arg_list),
                returncode=124,
                stdout=(exc.stdout or "") if isinstance(exc.stdout, str) else "",
                stderr=f"timed out after {timeout}s",
            )
        except OSError as exc:
            return CommandResult(args=tuple(arg_list), returncode=127, stdout="", stderr=str(exc))


def _parse_version(text: str) -> tuple[int, ...]:
    parts: list[int] = []
    for chunk in text.strip().split("."):
        digits = "".join(ch for ch in chunk if ch.isdigit())
        if not digits:
            break
        parts.append(int(digits))
    return tuple(parts)


# --------------------------------------------------------------------------- #
# Check + report records
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class VerificationCheck:
    name: str
    required: bool
    ok: bool
    details: str
    recovery: str = ""


@dataclass(frozen=True)
class VerificationReport:
    ok: bool
    checks: tuple[VerificationCheck, ...] = field(default_factory=tuple)
    version: str | None = None

    @property
    def failed_required(self) -> tuple[VerificationCheck, ...]:
        return tuple(check for check in self.checks if check.required and not check.ok)

    @property
    def warnings(self) -> tuple[VerificationCheck, ...]:
        return tuple(check for check in self.checks if not check.required and not check.ok)

    def to_json(self) -> dict[str, object]:
        return {
            "ok": self.ok,
            "version": self.version,
            "checks": [
                {
                    "name": check.name,
                    "required": check.required,
                    "ok": check.ok,
                    "details": check.details,
                    "recovery": check.recovery,
                }
                for check in self.checks
            ],
        }


# --------------------------------------------------------------------------- #
# Bounded MCP initialize handshake probe
# --------------------------------------------------------------------------- #


class HandshakeProbe(Protocol):
    def __call__(self, paths: ManagedPaths, *, timeout: float) -> tuple[bool, str]: ...


async def _probe_mcp_handshake_async(paths: ManagedPaths, *, timeout: float) -> tuple[bool, str]:
    """Launch the managed server over real stdio and complete one MCP initialize.

    Mirrors the proven pattern in tests/test_distribution.py /
    tests/test_lifecycle.py: an isolated ``NEO_LOCALMCP_HOME`` so the probe never
    touches the real managed registry, a hard ``asyncio.wait_for`` timeout around
    the whole handshake, and cleanup in ``finally`` so a probe failure never
    leaves a registered server PID behind.
    """

    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    python_executable = paths.python_executable
    if not python_executable.exists():
        return False, f"managed python not found: {python_executable}"

    env = {**os.environ, "NEO_LOCALMCP_HOME": str(paths.root)}
    params = StdioServerParameters(
        command=str(python_executable),
        args=["-m", "neo_localmcp.mcp.server"],
        env=env,
    )
    registry_dir = paths.process_registry / "servers"
    before_pids: set[str] = set()
    if registry_dir.exists():
        before_pids = {entry.name for entry in registry_dir.glob("*.json")}

    async def _run() -> tuple[bool, str]:
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                return True, "MCP initialize handshake completed"

    try:
        return await asyncio.wait_for(_run(), timeout=timeout)
    except asyncio.TimeoutError:
        return False, f"MCP initialize handshake timed out after {timeout}s"
    except Exception as exc:  # noqa: BLE001 - any handshake failure is a verification failure
        return False, f"MCP initialize handshake failed: {exc}"
    finally:
        # The probed server self-unregisters on clean stdio teardown, but a
        # probe that failed mid-handshake could leave an entry behind; sweep any
        # new registry files introduced by this probe so verification never
        # leaves a stray PID in the managed registry.
        if registry_dir.exists():
            for entry in registry_dir.glob("*.json"):
                if entry.name in before_pids:
                    continue
                try:
                    data = json.loads(entry.read_text(encoding="utf-8"))
                    pid = int(data.get("pid", -1))
                except (OSError, ValueError, json.JSONDecodeError):
                    pid = -1
                if pid > 0:
                    lifecycle.unregister_server(pid)
                else:
                    entry.unlink(missing_ok=True)


def real_mcp_handshake_probe(paths: ManagedPaths, *, timeout: float = HANDSHAKE_TIMEOUT) -> tuple[bool, str]:
    return asyncio.run(_probe_mcp_handshake_async(paths, timeout=timeout))


# --------------------------------------------------------------------------- #
# Individual required checks
# --------------------------------------------------------------------------- #


def _check_python_floor(
    location_python: Path,
    *,
    runner: CommandRunner,
    python_floor: tuple[int, int],
) -> VerificationCheck:
    result = runner.run(
        (str(location_python), "-c", _INTERPRETER_SNIPPET),
        timeout=VALIDATION_COMMAND_TIMEOUT,
    )
    version = _parse_version(result.stdout) if result.ok else ()
    ok = bool(result.ok and version[:2] >= python_floor)
    floor_text = ".".join(str(part) for part in python_floor)
    details = result.stdout.strip() or result.stderr.strip() or "no output"
    return VerificationCheck(
        name="python-floor",
        required=True,
        ok=ok,
        details=details,
        recovery=(
            "" if ok else f"Rebuild the managed runtime with a Python >= {floor_text} interpreter."
        ),
    )


def _check_package_version(
    location_python: Path,
    expected_version: str | None,
    *,
    runner: CommandRunner,
) -> tuple[VerificationCheck, str | None]:
    result = runner.run(
        (str(location_python), "-c", _PACKAGE_VERSION_SNIPPET),
        timeout=VALIDATION_COMMAND_TIMEOUT,
    )
    reported = result.stdout.strip() if result.ok else None
    if expected_version is None:
        ok = result.ok and bool(reported)
    else:
        ok = result.ok and reported == expected_version
    details = reported or result.stderr.strip() or "no output"
    check = VerificationCheck(
        name="package-version",
        required=True,
        ok=bool(ok),
        details=details,
        recovery="" if ok else "Reinstall/rebuild the managed runtime to match the expected version.",
    )
    return check, reported


def _check_cli_resolution(paths: ManagedPaths) -> VerificationCheck:
    exists = paths.cli_executable.exists()
    return VerificationCheck(
        name="cli-resolution",
        required=True,
        ok=exists,
        details=str(paths.cli_executable),
        recovery="" if exists else "Reinstall the managed runtime; the CLI launcher is missing.",
    )


def _check_cli_startup(paths: ManagedPaths, *, runner: CommandRunner) -> VerificationCheck:
    result = runner.run(
        (str(paths.cli_executable), "--help"),
        timeout=VALIDATION_COMMAND_TIMEOUT,
    )
    details = result.stderr.strip() or result.stdout.strip()[:200] or "ok"
    return VerificationCheck(
        name="cli-startup",
        required=True,
        ok=result.ok,
        details=details,
        recovery="" if result.ok else "Run the CLI directly to inspect the startup error and reinstall if needed.",
    )


def _check_server_import(location_python: Path, *, runner: CommandRunner) -> VerificationCheck:
    result = runner.run(
        (str(location_python), "-c", _SERVER_IMPORT_SNIPPET),
        timeout=VALIDATION_COMMAND_TIMEOUT,
    )
    details = result.stderr.strip() or "ok"
    return VerificationCheck(
        name="server-import",
        required=True,
        ok=result.ok,
        details=details,
        recovery="" if result.ok else "The MCP server module fails to import; reinstall the managed runtime.",
    )


def _check_mcp_handshake(
    paths: ManagedPaths,
    *,
    handshake: HandshakeProbe,
    timeout: float,
) -> VerificationCheck:
    try:
        ok, details = handshake(paths, timeout=timeout)
    except Exception as exc:  # noqa: BLE001 - a raising probe is a failed handshake, not a crash
        ok, details = False, f"MCP initialize handshake raised: {exc}"
    return VerificationCheck(
        name="mcp-initialize-handshake",
        required=True,
        ok=ok,
        details=details,
        recovery="" if ok else "The MCP server did not complete initialize over stdio; reinstall the managed runtime.",
    )


def _resolve_canonical_paths(
    paths: ManagedPaths, *, env_runner: EnvCommandRunner
) -> tuple[CommandResult, dict[str, str] | None]:
    env = {**os.environ, "NEO_LOCALMCP_HOME": str(paths.root)}
    result = env_runner.run(
        (str(paths.python_executable), "-c", _CANONICAL_PATHS_SNIPPET),
        env=env,
        timeout=VALIDATION_COMMAND_TIMEOUT,
    )
    if not result.ok:
        return result, None
    try:
        payload = json.loads(result.stdout)
    except (ValueError, json.JSONDecodeError):
        return result, None
    if not isinstance(payload, dict):
        return result, None
    return result, payload


def _check_canonical_config_path(
    paths: ManagedPaths, *, env_runner: EnvCommandRunner
) -> VerificationCheck:
    result, payload = _resolve_canonical_paths(paths, env_runner=env_runner)
    expected = str(paths.config / "config.yaml")
    if payload is None:
        details = result.stderr.strip() or "could not resolve config path under managed environment"
        return VerificationCheck(
            name="canonical-config-path",
            required=True,
            ok=False,
            details=details,
            recovery="Ensure NEO_LOCALMCP_HOME resolves consistently for the managed runtime.",
        )
    resolved = str(payload.get("config_path", ""))
    ok = Path(resolved) == Path(expected)
    details = f"expected={expected} resolved={resolved}"
    return VerificationCheck(
        name="canonical-config-path",
        required=True,
        ok=ok,
        details=details,
        recovery="" if ok else "NEO_LOCALMCP_HOME / config layout is out of sync with the managed paths.",
    )


def _check_canonical_database_path(
    paths: ManagedPaths, *, env_runner: EnvCommandRunner
) -> VerificationCheck:
    result, payload = _resolve_canonical_paths(paths, env_runner=env_runner)
    expected = str(paths.sqlite / "repo-context.sqlite")
    if payload is None:
        details = result.stderr.strip() or "could not resolve database path under managed environment"
        return VerificationCheck(
            name="canonical-database-path",
            required=True,
            ok=False,
            details=details,
            recovery="Ensure NEO_LOCALMCP_HOME resolves consistently for the managed runtime.",
        )
    resolved = str(payload.get("db_path", ""))
    ok = Path(resolved) == Path(expected)
    details = f"expected={expected} resolved={resolved}"
    return VerificationCheck(
        name="canonical-database-path",
        required=True,
        ok=ok,
        details=details,
        recovery="" if ok else "NEO_LOCALMCP_HOME / sqlite layout is out of sync with the managed paths.",
    )


def _check_client_targets(paths: ManagedPaths, expected_clients: frozenset[str]) -> VerificationCheck:
    if not expected_clients:
        return VerificationCheck(
            name="client-targets",
            required=True,
            ok=True,
            details="no client surfaces expected",
        )
    expected_launcher = paths.server_executable
    checks = verify_registrations(paths, expected_server_command=expected_launcher)
    by_client = {check.client: check for check in checks}
    registered = {record.client for record in read_registrations(paths)}
    missing = sorted(client for client in expected_clients if client not in registered)
    mismatched = sorted(
        client
        for client in expected_clients
        if client in by_client and not by_client[client].ok
    )
    ok = not missing and not mismatched
    details_parts = []
    if missing:
        details_parts.append(f"missing: {', '.join(missing)}")
    if mismatched:
        details_parts.append(f"stale launcher: {', '.join(mismatched)}")
    if not details_parts:
        details_parts.append(f"all expected clients target {expected_launcher}")
    return VerificationCheck(
        name="client-targets",
        required=True,
        ok=ok,
        details="; ".join(details_parts),
        recovery="" if ok else "Re-run client setup to point registrations at the promoted launcher.",
    )


def _check_doctor(paths: ManagedPaths, *, env_runner: EnvCommandRunner) -> VerificationCheck:
    env = {**os.environ, "NEO_LOCALMCP_HOME": str(paths.root)}
    result = env_runner.run(
        (str(paths.python_executable), "-c", _DOCTOR_SNIPPET),
        env=env,
        timeout=VALIDATION_COMMAND_TIMEOUT,
    )
    if not result.ok:
        details = result.stderr.strip() or "doctor invocation failed"
        return VerificationCheck(
            name="doctor-required-checks",
            required=True,
            ok=False,
            details=details,
            recovery="Run `neo-localmcp doctor` directly to inspect the failure.",
        )
    try:
        payload = json.loads(result.stdout)
    except (ValueError, json.JSONDecodeError):
        return VerificationCheck(
            name="doctor-required-checks",
            required=True,
            ok=False,
            details=f"doctor output was not valid JSON: {result.stdout[:200]!r}",
            recovery="Run `neo-localmcp doctor` directly to inspect the failure.",
        )
    # Inspect the substantive, Ollama-independent health signals doctor actually
    # returns rather than trusting its top-level ``ok`` (which is hardcoded True
    # in system.doctor() regardless of sub-check health, so it would make this a
    # no-op required check). Ollama reachability is deliberately NOT required
    # here: deterministic retrieval must never depend on Ollama, so an
    # unreachable/cold model surfaces only through the separate warning-only
    # ``ollama-optional`` check, never as an installation failure.
    required_signals = {
        "config_exists": payload.get("config_exists"),
        "db_open": payload.get("db_open"),
    }
    failed = sorted(name for name, value in required_signals.items() if value is not True)
    ok = not failed
    details = (
        "doctor required health signals ok (config_exists, db_open)"
        if ok
        else f"doctor reported unhealthy required signal(s): {', '.join(failed)}"
    )
    return VerificationCheck(
        name="doctor-required-checks",
        required=True,
        ok=ok,
        details=details,
        recovery="" if ok else "Run `neo-localmcp doctor` directly to inspect the failing checks.",
    )


# --------------------------------------------------------------------------- #
# Warning-only checks
# --------------------------------------------------------------------------- #


def _check_ollama_optional(*, status_fn: Callable[[], dict] | None = None) -> VerificationCheck:
    from .. import ollama_client

    fn = status_fn or ollama_client.status
    try:
        result = fn()
    except Exception as exc:  # noqa: BLE001 - Ollama is optional; never fail verification
        return VerificationCheck(
            name="ollama-optional",
            required=False,
            ok=False,
            details=f"Ollama status probe raised: {exc}",
            recovery="Install/start Ollama and pull the configured models if you want local ranking.",
        )
    state = str(result.get("state", "unreachable"))
    ok = state in {"ready", "model_cold", "disabled"}
    details = f"state={state} model={result.get('model')}"
    return VerificationCheck(
        name="ollama-optional",
        required=False,
        ok=ok,
        details=details,
        recovery="" if ok else "Install/start Ollama and pull the configured models if you want local ranking.",
    )


def _check_claude_desktop_manual(paths: ManagedPaths) -> VerificationCheck:
    records = read_registrations(paths)
    desktop = next((record for record in records if record.client == CLAUDE_DESKTOP), None)
    if desktop is None:
        return VerificationCheck(
            name="claude-desktop-manual",
            required=False,
            ok=True,
            details="Claude Desktop not selected",
        )
    return VerificationCheck(
        name="claude-desktop-manual",
        required=False,
        ok=False,
        details="Claude Desktop requires manually installing the .mcpb extension",
        recovery=(
            "In Claude Desktop: Settings > Extensions > Advanced settings > Install "
            "Extension, then select neo-localmcp.mcpb."
        ),
    )


# --------------------------------------------------------------------------- #
# Orchestration
# --------------------------------------------------------------------------- #


def verify_installation(
    paths: ManagedPaths,
    expected_version: str | None,
    expected_clients: frozenset[str] | set[str] | tuple[str, ...] = (),
    *,
    runner: CommandRunner | None = None,
    env_runner: EnvCommandRunner | None = None,
    reporter: Reporter | None = None,
    handshake: HandshakeProbe | None = None,
    handshake_timeout: float = HANDSHAKE_TIMEOUT,
    python_floor: tuple[int, int] = PYTHON_FLOOR,
    ollama_status_fn: Callable[[], dict] | None = None,
) -> VerificationReport:
    """Verify the installed CLI, MCP endpoint, paths, clients, and doctor.

    Re-probes the artifacts at ``paths`` directly rather than trusting a prior
    build/promote result, using bounded subprocess calls against the managed
    interpreter/executables. Required checks failing makes the report unsuccessful;
    warning-only checks (optional Ollama, manual Claude Desktop) never do.
    """

    command_runner = runner or SubprocessCommandRunner()
    env_command_runner = env_runner or SubprocessEnvCommandRunner()
    handshake_probe = handshake or real_mcp_handshake_probe
    expected_clients_set = frozenset(expected_clients)
    location = installed_location(paths)

    checks: list[VerificationCheck] = []

    if reporter is not None:
        reporter.info("Verifying installed runtime.")

    checks.append(
        _check_python_floor(location.python_executable, runner=command_runner, python_floor=python_floor)
    )
    version_check, reported_version = _check_package_version(
        location.python_executable, expected_version, runner=command_runner
    )
    checks.append(version_check)
    checks.append(_check_cli_resolution(paths))
    checks.append(_check_cli_startup(paths, runner=command_runner))
    checks.append(_check_server_import(location.python_executable, runner=command_runner))

    if reporter is not None:
        reporter.info("Verifying MCP initialize handshake.")
    checks.append(_check_mcp_handshake(paths, handshake=handshake_probe, timeout=handshake_timeout))

    checks.append(_check_canonical_config_path(paths, env_runner=env_command_runner))
    checks.append(_check_canonical_database_path(paths, env_runner=env_command_runner))
    checks.append(_check_client_targets(paths, expected_clients_set))

    if reporter is not None:
        reporter.info("Running doctor required checks.")
    checks.append(_check_doctor(paths, env_runner=env_command_runner))

    checks.append(_check_ollama_optional(status_fn=ollama_status_fn))
    checks.append(_check_claude_desktop_manual(paths))

    ok = all(check.ok for check in checks if check.required)
    if reporter is not None:
        if ok:
            reporter.action("Installation verified.")
        else:
            failed = ", ".join(check.name for check in checks if check.required and not check.ok)
            reporter.error(f"Installation verification failed: {failed}.")
        for check in checks:
            if not check.required and not check.ok:
                reporter.warn(f"{check.name}: {check.details}")

    return VerificationReport(ok=ok, checks=tuple(checks), version=reported_version)
