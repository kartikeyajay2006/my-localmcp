from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Sequence

import pytest

import neo_localmcp
from neo_localmcp import mcp_server_lifecycle as lifecycle
from neo_localmcp.installer.clients import ClientRegistrationRecord, write_registrations
from neo_localmcp.installer.paths import ManagedPaths
from neo_localmcp.installer.runtime import CommandResult
from neo_localmcp.installer.verification import (
    VerificationCheck,
    VerificationReport,
    verify_installation,
)

REPO_ROOT = Path(__file__).resolve().parents[2]


def _paths(tmp_path: Path) -> ManagedPaths:
    platform = "windows" if os.name == "nt" else "posix"
    paths = ManagedPaths(root=tmp_path / ".neo-localmcp", platform=platform, home=tmp_path)
    paths.ensure_directories()
    return paths


def _touch_runtime_files(paths: ManagedPaths) -> None:
    paths.python_executable.parent.mkdir(parents=True, exist_ok=True)
    paths.python_executable.write_text("#!/bin/sh\n", encoding="utf-8")
    paths.python_executable.chmod(0o755)
    paths.cli_executable.write_text("#!/bin/sh\n", encoding="utf-8")
    paths.cli_executable.chmod(0o755)
    paths.server_executable.write_text("#!/bin/sh\n", encoding="utf-8")
    paths.server_executable.chmod(0o755)


class ScriptedCommandRunner:
    """Command runner returning canned results keyed by a classifier of the args."""

    def __init__(self, results: dict[str, CommandResult], default: CommandResult | None = None):
        self._results = results
        self._default = default
        self.calls: list[tuple[str, ...]] = []

    def _classify(self, args: Sequence[str]) -> str:
        joined = " ".join(str(a) for a in args)
        if "sys.version_info" in joined:
            return "python-floor"
        if "neo_localmcp.__version__" in joined:
            return "package-version"
        if joined.endswith("--help"):
            return "cli-startup"
        if "import neo_localmcp.mcp.server" in joined:
            return "server-import"
        return "other"

    def run(self, args: Sequence[str], *, log_path=None, timeout=None) -> CommandResult:
        self.calls.append(tuple(str(a) for a in args))
        key = self._classify(args)
        if key in self._results:
            return self._results[key]
        if self._default is not None:
            return self._default
        return CommandResult(args=tuple(str(a) for a in args), returncode=0, stdout="ok", stderr="")


class ScriptedEnvCommandRunner:
    """Env-aware command runner returning canned results keyed by a classifier."""

    def __init__(self, results: dict[str, CommandResult], default: CommandResult | None = None):
        self._results = results
        self._default = default
        self.calls: list[tuple[tuple[str, ...], dict]] = []

    def _classify(self, args: Sequence[str]) -> str:
        joined = " ".join(str(a) for a in args)
        if "system import doctor" in joined:
            return "doctor"
        if "config.config_path" in joined:
            return "canonical-paths"
        return "other"

    def run(self, args: Sequence[str], *, env, timeout=None) -> CommandResult:
        self.calls.append((tuple(str(a) for a in args), dict(env)))
        key = self._classify(args)
        if key in self._results:
            return self._results[key]
        if self._default is not None:
            return self._default
        return CommandResult(args=tuple(str(a) for a in args), returncode=0, stdout="{}", stderr="")


def _ok(stdout: str = "ok") -> CommandResult:
    return CommandResult(args=(), returncode=0, stdout=stdout, stderr="")


def _fail(stderr: str = "boom") -> CommandResult:
    return CommandResult(args=(), returncode=1, stdout="", stderr=stderr)


def _passing_runner(expected_version: str) -> ScriptedCommandRunner:
    return ScriptedCommandRunner(
        {
            "python-floor": _ok("3.12.4"),
            "package-version": _ok(expected_version),
            "cli-startup": _ok("usage: neo-localmcp"),
            "server-import": _ok(""),
        }
    )


def _healthy_doctor_payload() -> dict:
    """A doctor() payload shaped like the real system.doctor() output for a
    healthy install: top-level ``ok`` is always True (hardcoded in mcp/system.py), the
    substantive required signals ``config_exists``/``db_open`` are True, and
    Ollama is unreachable (which must NOT make the required doctor check fail)."""
    return {
        "ok": True,
        "config_exists": True,
        "db_open": True,
        "ollama": {"ok": False, "state": "unreachable"},
        "repo": {},
        "running_servers": [],
    }


def _canonical_payload(paths: ManagedPaths) -> str:
    return json.dumps(
        {
            "config_path": str(paths.config / "config.yaml"),
            "db_path": str(paths.sqlite / "repo-context.sqlite"),
        }
    )


def _passing_env_runner(paths: ManagedPaths) -> ScriptedEnvCommandRunner:
    return ScriptedEnvCommandRunner(
        {
            "doctor": _ok(json.dumps(_healthy_doctor_payload())),
            "canonical-paths": _ok(_canonical_payload(paths)),
        }
    )


def _passing_handshake(paths: ManagedPaths, *, timeout: float) -> tuple[bool, str]:
    return True, "ok"


def _base_kwargs(paths: ManagedPaths, expected_version: str = "9.9.9") -> dict:
    return dict(
        runner=_passing_runner(expected_version),
        env_runner=_passing_env_runner(paths),
        handshake=_passing_handshake,
    )


# --------------------------------------------------------------------------- #
# Step 1: one failure test per required check
# --------------------------------------------------------------------------- #


def test_python_floor_failure_fails_verification(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    _touch_runtime_files(paths)
    kwargs = _base_kwargs(paths)
    kwargs["runner"] = ScriptedCommandRunner(
        {
            "python-floor": _ok("3.9.0"),  # below the floor
            "package-version": _ok("9.9.9"),
            "cli-startup": _ok(),
            "server-import": _ok(),
        }
    )

    report = verify_installation(paths, "9.9.9", frozenset(), **kwargs)

    assert report.ok is False
    check = next(c for c in report.checks if c.name == "python-floor")
    assert check.required is True
    assert check.ok is False
    assert check.recovery


def test_package_version_mismatch_fails_verification(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    _touch_runtime_files(paths)
    kwargs = _base_kwargs(paths)
    kwargs["runner"] = ScriptedCommandRunner(
        {
            "python-floor": _ok("3.12.4"),
            "package-version": _ok("1.0.0"),  # mismatched
            "cli-startup": _ok(),
            "server-import": _ok(),
        }
    )

    report = verify_installation(paths, "9.9.9", frozenset(), **kwargs)

    assert report.ok is False
    check = next(c for c in report.checks if c.name == "package-version")
    assert check.ok is False
    assert check.recovery


def test_cli_resolution_failure_when_launcher_missing(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    # Do not touch runtime files: cli_executable does not exist.
    kwargs = _base_kwargs(paths)

    report = verify_installation(paths, "9.9.9", frozenset(), **kwargs)

    assert report.ok is False
    check = next(c for c in report.checks if c.name == "cli-resolution")
    assert check.ok is False
    assert check.recovery


def test_cli_startup_failure_fails_verification(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    _touch_runtime_files(paths)
    kwargs = _base_kwargs(paths)
    kwargs["runner"] = ScriptedCommandRunner(
        {
            "python-floor": _ok("3.12.4"),
            "package-version": _ok("9.9.9"),
            "cli-startup": _fail("cli crashed"),
            "server-import": _ok(),
        }
    )

    report = verify_installation(paths, "9.9.9", frozenset(), **kwargs)

    assert report.ok is False
    check = next(c for c in report.checks if c.name == "cli-startup")
    assert check.ok is False
    assert check.recovery


def test_server_import_failure_fails_verification(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    _touch_runtime_files(paths)
    kwargs = _base_kwargs(paths)
    kwargs["runner"] = ScriptedCommandRunner(
        {
            "python-floor": _ok("3.12.4"),
            "package-version": _ok("9.9.9"),
            "cli-startup": _ok(),
            "server-import": _fail("ModuleNotFoundError"),
        }
    )

    report = verify_installation(paths, "9.9.9", frozenset(), **kwargs)

    assert report.ok is False
    check = next(c for c in report.checks if c.name == "server-import")
    assert check.ok is False
    assert check.recovery


def test_broken_mcp_handshake_fails_verification_even_with_files_present(tmp_path: Path) -> None:
    """Gate: an intentionally broken MCP handshake must fail verification even
    when the venv and CLI files exist and every other check passes."""
    paths = _paths(tmp_path)
    _touch_runtime_files(paths)
    kwargs = _base_kwargs(paths)

    def broken_handshake(paths: ManagedPaths, *, timeout: float) -> tuple[bool, str]:
        return False, "connection refused"

    kwargs["handshake"] = broken_handshake

    report = verify_installation(paths, "9.9.9", frozenset(), **kwargs)

    assert report.ok is False
    check = next(c for c in report.checks if c.name == "mcp-initialize-handshake")
    assert check.required is True
    assert check.ok is False
    assert check.recovery
    # Every filesystem-level check should still have passed; only the handshake failed.
    other_required = [c for c in report.checks if c.required and c.name != "mcp-initialize-handshake"]
    assert all(c.ok for c in other_required), [c for c in other_required if not c.ok]


def test_handshake_probe_raising_is_treated_as_failure(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    _touch_runtime_files(paths)
    kwargs = _base_kwargs(paths)

    def raising_handshake(paths: ManagedPaths, *, timeout: float):
        raise RuntimeError("subprocess spawn failed")

    kwargs["handshake"] = raising_handshake

    report = verify_installation(paths, "9.9.9", frozenset(), **kwargs)

    assert report.ok is False
    check = next(c for c in report.checks if c.name == "mcp-initialize-handshake")
    assert check.ok is False
    assert "subprocess spawn failed" in check.details


def test_canonical_config_path_mismatch_fails_verification(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    _touch_runtime_files(paths)
    kwargs = _base_kwargs(paths)
    mismatched = json.dumps(
        {
            "config_path": "/somewhere/else/config.yaml",
            "db_path": str(paths.sqlite / "repo-context.sqlite"),
        }
    )
    kwargs["env_runner"] = ScriptedEnvCommandRunner(
        {
            "doctor": _ok(json.dumps(_healthy_doctor_payload())),
            "canonical-paths": _ok(mismatched),
        }
    )

    report = verify_installation(paths, "9.9.9", frozenset(), **kwargs)

    assert report.ok is False
    check = next(c for c in report.checks if c.name == "canonical-config-path")
    assert check.ok is False
    assert check.recovery


def test_canonical_database_path_mismatch_fails_verification(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    _touch_runtime_files(paths)
    kwargs = _base_kwargs(paths)
    mismatched = json.dumps(
        {
            "config_path": str(paths.config / "config.yaml"),
            "db_path": "/somewhere/else/repo-context.sqlite",
        }
    )
    kwargs["env_runner"] = ScriptedEnvCommandRunner(
        {
            "doctor": _ok(json.dumps(_healthy_doctor_payload())),
            "canonical-paths": _ok(mismatched),
        }
    )

    report = verify_installation(paths, "9.9.9", frozenset(), **kwargs)

    assert report.ok is False
    check = next(c for c in report.checks if c.name == "canonical-database-path")
    assert check.ok is False
    assert check.recovery


def test_client_targets_failure_when_registration_missing(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    _touch_runtime_files(paths)
    kwargs = _base_kwargs(paths)
    # No registrations written at all, but claude-code is expected.

    report = verify_installation(paths, "9.9.9", frozenset({"claude-code"}), **kwargs)

    assert report.ok is False
    check = next(c for c in report.checks if c.name == "client-targets")
    assert check.ok is False
    assert "missing" in check.details
    assert check.recovery


def test_client_targets_failure_when_launcher_stale(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    paths = _paths(tmp_path)
    _touch_runtime_files(paths)
    write_registrations(
        paths,
        (
            ClientRegistrationRecord(
                client="codex",
                active=True,
                manual=False,
                server_command="/stale/venv/bin/neo-localmcp-server",
                config_path=str(tmp_path / "config.toml"),
                detail="stale",
            ),
        ),
    )
    (tmp_path / "config.toml").write_text(
        "# BEGIN neo-localmcp\ncommand = \"/stale/venv/bin/neo-localmcp-server\"\n# END neo-localmcp\n",
        encoding="utf-8",
    )
    kwargs = _base_kwargs(paths)

    report = verify_installation(paths, "9.9.9", frozenset({"codex"}), **kwargs)

    assert report.ok is False
    check = next(c for c in report.checks if c.name == "client-targets")
    assert check.ok is False
    assert "stale launcher" in check.details


def test_doctor_required_checks_failure_fails_verification(tmp_path: Path) -> None:
    """A realistic unhealthy doctor payload: config file missing. Crucially the
    top-level ``ok`` is still True (system.doctor() hardcodes it), so this proves
    the check inspects the substantive signal rather than trusting ``ok``."""
    paths = _paths(tmp_path)
    _touch_runtime_files(paths)
    payload = _healthy_doctor_payload()
    payload["ok"] = True  # system.doctor() always reports ok: True
    payload["config_exists"] = False  # ...even when config is genuinely missing
    kwargs = _base_kwargs(paths)
    kwargs["env_runner"] = ScriptedEnvCommandRunner(
        {
            "doctor": _ok(json.dumps(payload)),
            "canonical-paths": _ok(_canonical_payload(paths)),
        }
    )

    report = verify_installation(paths, "9.9.9", frozenset(), **kwargs)

    assert report.ok is False
    check = next(c for c in report.checks if c.name == "doctor-required-checks")
    assert check.ok is False
    assert "config_exists" in check.details
    assert check.recovery


def test_doctor_db_open_false_fails_verification_despite_top_level_ok(tmp_path: Path) -> None:
    """The database health signal is also required and Ollama-independent."""
    paths = _paths(tmp_path)
    _touch_runtime_files(paths)
    payload = _healthy_doctor_payload()
    payload["ok"] = True
    payload["db_open"] = False
    kwargs = _base_kwargs(paths)
    kwargs["env_runner"] = ScriptedEnvCommandRunner(
        {
            "doctor": _ok(json.dumps(payload)),
            "canonical-paths": _ok(_canonical_payload(paths)),
        }
    )

    report = verify_installation(paths, "9.9.9", frozenset(), **kwargs)

    assert report.ok is False
    check = next(c for c in report.checks if c.name == "doctor-required-checks")
    assert check.ok is False
    assert "db_open" in check.details


def test_doctor_unreachable_ollama_does_not_fail_required_check(tmp_path: Path) -> None:
    """Deterministic retrieval must never depend on Ollama: a doctor payload that
    is healthy except for an unreachable Ollama must PASS the required doctor
    check (the unreachable model is surfaced by the separate warning check)."""
    paths = _paths(tmp_path)
    _touch_runtime_files(paths)
    payload = _healthy_doctor_payload()
    payload["ollama"] = {"ok": False, "state": "unreachable", "error": "connection refused"}
    kwargs = _base_kwargs(paths)
    kwargs["env_runner"] = ScriptedEnvCommandRunner(
        {
            "doctor": _ok(json.dumps(payload)),
            "canonical-paths": _ok(_canonical_payload(paths)),
        }
    )
    kwargs["ollama_status_fn"] = lambda: {"state": "unreachable", "model": "qwen3:8b"}

    report = verify_installation(paths, "9.9.9", frozenset(), **kwargs)

    doctor_check = next(c for c in report.checks if c.name == "doctor-required-checks")
    assert doctor_check.required is True
    assert doctor_check.ok is True
    # The unreachable Ollama is still surfaced -- but only as a warning.
    ollama_check = next(c for c in report.checks if c.name == "ollama-optional")
    assert ollama_check.required is False
    assert ollama_check.ok is False
    # Neither doctor nor the warning check should fail the overall install.
    assert report.ok is True


def test_doctor_invocation_crash_fails_verification(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    _touch_runtime_files(paths)
    kwargs = _base_kwargs(paths)
    kwargs["env_runner"] = ScriptedEnvCommandRunner(
        {
            "doctor": _fail("Traceback: ImportError"),
            "canonical-paths": _ok(_canonical_payload(paths)),
        }
    )

    report = verify_installation(paths, "9.9.9", frozenset(), **kwargs)

    assert report.ok is False
    check = next(c for c in report.checks if c.name == "doctor-required-checks")
    assert check.ok is False


# --------------------------------------------------------------------------- #
# Step 2: warning-only checks never fail the report
# --------------------------------------------------------------------------- #


def test_ollama_unreachable_is_warning_not_failure(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    _touch_runtime_files(paths)
    kwargs = _base_kwargs(paths)
    kwargs["ollama_status_fn"] = lambda: {"state": "unreachable", "model": "qwen3:8b"}

    report = verify_installation(paths, "9.9.9", frozenset(), **kwargs)

    check = next(c for c in report.checks if c.name == "ollama-optional")
    assert check.required is False
    assert check.ok is False
    assert check.recovery
    # A warning-only check failing must not flip the overall report.
    assert report.ok is True
    assert report.warnings and check in report.warnings
    assert report.failed_required == ()


def test_ollama_status_probe_raising_is_warning_not_failure(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    _touch_runtime_files(paths)
    kwargs = _base_kwargs(paths)

    def raising_status():
        raise RuntimeError("connection refused")

    kwargs["ollama_status_fn"] = raising_status

    report = verify_installation(paths, "9.9.9", frozenset(), **kwargs)

    check = next(c for c in report.checks if c.name == "ollama-optional")
    assert check.required is False
    assert check.ok is False
    assert report.ok is True


def test_claude_desktop_manual_install_is_warning_not_failure(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    _touch_runtime_files(paths)
    write_registrations(
        paths,
        (
            ClientRegistrationRecord(
                client="claude-desktop",
                active=True,
                manual=True,
                server_command=None,
                config_path=None,
                detail="selected (manual)",
            ),
        ),
    )
    kwargs = _base_kwargs(paths)

    report = verify_installation(paths, "9.9.9", frozenset(), **kwargs)

    check = next(c for c in report.checks if c.name == "claude-desktop-manual")
    assert check.required is False
    assert check.ok is False
    assert check.recovery
    assert report.ok is True


def test_claude_desktop_not_selected_is_clean_warning_check(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    _touch_runtime_files(paths)
    kwargs = _base_kwargs(paths)

    report = verify_installation(paths, "9.9.9", frozenset(), **kwargs)

    check = next(c for c in report.checks if c.name == "claude-desktop-manual")
    assert check.required is False
    assert check.ok is True


# --------------------------------------------------------------------------- #
# Everything passing end-to-end (all checks injected as healthy)
# --------------------------------------------------------------------------- #


def test_all_checks_passing_yields_successful_report(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    _touch_runtime_files(paths)
    kwargs = _base_kwargs(paths)
    kwargs["ollama_status_fn"] = lambda: {"state": "ready", "model": "qwen3:8b"}

    report = verify_installation(paths, "9.9.9", frozenset(), **kwargs)

    assert isinstance(report, VerificationReport)
    assert report.ok is True
    assert report.failed_required == ()
    assert all(isinstance(c, VerificationCheck) for c in report.checks)
    names = {c.name for c in report.checks}
    assert names == {
        "python-floor",
        "package-version",
        "cli-resolution",
        "cli-startup",
        "server-import",
        "mcp-initialize-handshake",
        "canonical-config-path",
        "canonical-database-path",
        "client-targets",
        "doctor-required-checks",
        "ollama-optional",
        "claude-desktop-manual",
    }
    # Every check record has exactly the contract fields.
    for check in report.checks:
        assert hasattr(check, "name")
        assert hasattr(check, "required")
        assert hasattr(check, "ok")
        assert hasattr(check, "details")
        assert hasattr(check, "recovery")


def test_env_runner_receives_neo_localmcp_home(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    _touch_runtime_files(paths)
    kwargs = _base_kwargs(paths)
    env_runner = kwargs["env_runner"]

    verify_installation(paths, "9.9.9", frozenset(), **kwargs)

    assert env_runner.calls, "expected doctor/canonical-path probes to run"
    for _args, env in env_runner.calls:
        assert env.get("NEO_LOCALMCP_HOME") == str(paths.root)


# --------------------------------------------------------------------------- #
# Step 4: real temporary-home probe (slow)
# --------------------------------------------------------------------------- #


@pytest.mark.slow
def test_real_installed_endpoint_verifies_and_leaves_no_registered_pid(tmp_path: Path) -> None:
    """Build a real managed runtime in a temporary home, then verify it end to
    end with the real subprocess and MCP handshake probes -- no fakes. The
    handshake probe must never leave a registered server PID behind."""
    from neo_localmcp.installer.runtime import build_candidate, promote_candidate

    platform = "windows" if os.name == "nt" else "posix"
    paths = ManagedPaths(root=tmp_path / ".neo-localmcp", platform=platform, home=tmp_path)
    paths.ensure_directories()
    expected_version = neo_localmcp.__version__

    candidate = build_candidate(
        paths,
        source_root=REPO_ROOT,
        python_executable=Path(sys.executable),
        operation_id="verification-real-build",
    )
    assert candidate.build_ok is True, candidate.error

    promotion = promote_candidate(paths, candidate, expected_version=expected_version)
    assert promotion.ok is True, promotion.error

    registry_dir = paths.process_registry / "servers"
    before = set(registry_dir.glob("*.json")) if registry_dir.exists() else set()

    report = verify_installation(paths, expected_version, frozenset())

    assert report.failed_required == (), report.failed_required
    assert report.ok is True

    after = set(registry_dir.glob("*.json")) if registry_dir.exists() else set()
    assert after == before, f"verification probe left registered PIDs: {after - before}"

    # Also confirm no server process is alive under the registry (belt and
    # braces beyond the file-based check above).
    for entry in lifecycle.list_servers(prune=False):
        assert Path(entry["executable"]) != paths.python_executable
