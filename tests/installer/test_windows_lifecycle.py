"""Real Windows acceptance tests for the setup-v2 managed lifecycle."""

from __future__ import annotations

import os
import json
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

import pytest

import neo_localmcp
from neo_localmcp.installer.paths import ManagedPaths
from neo_localmcp.installer.processes import discover_owned_processes, stop_owned_processes
from neo_localmcp.installer.runtime import build_candidate, promote_candidate
from neo_localmcp.installer.state import begin_operation
from neo_localmcp.installer.types import Operation


REPO_ROOT = Path(__file__).resolve().parents[2]
SETUP = REPO_ROOT / "setup.py"


def _run(executable: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(executable), *args],
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )


def _setup(home: Path, *args: str, stdin: str | None = None) -> subprocess.CompletedProcess[str]:
    test_user = home.parent / "phase14-user"
    appdata = test_user / "AppData" / "Roaming"
    appdata.mkdir(parents=True, exist_ok=True)
    env = {
        **os.environ,
        "NEO_LOCALMCP_HOME": str(home),
        "HOME": str(test_user),
        "USERPROFILE": str(test_user),
        "APPDATA": str(appdata),
    }
    return subprocess.run(
        [sys.executable, str(SETUP), *args],
        input=stdin,
        capture_output=True,
        text=True,
        env=env,
        timeout=600,
        check=False,
    )


def _seed_durable_data(home: Path, marker: str) -> None:
    config = home / "config" / "config.yaml"
    config.parent.mkdir(parents=True, exist_ok=True)
    payload = json.loads(config.read_text(encoding="utf-8")) if config.exists() else {}
    payload["_windows_lifecycle_marker"] = marker
    config.write_text(json.dumps(payload), encoding="utf-8")
    database = home / "sqlite" / "repo-context.sqlite"
    database.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(database)
    try:
        connection.execute(
            "CREATE TABLE IF NOT EXISTS phase14_marker (value TEXT PRIMARY KEY)"
        )
        connection.execute("INSERT OR REPLACE INTO phase14_marker VALUES (?)", (marker,))
        connection.commit()
    finally:
        connection.close()
    record = home / "clients" / "phase14.json"
    record.parent.mkdir(parents=True, exist_ok=True)
    record.write_text(json.dumps({"marker": marker}), encoding="utf-8")


def _durable_data_survives(home: Path, marker: str) -> bool:
    try:
        config = json.loads((home / "config" / "config.yaml").read_text(encoding="utf-8"))
        record = json.loads((home / "clients" / "phase14.json").read_text(encoding="utf-8"))
        connection = sqlite3.connect(home / "sqlite" / "repo-context.sqlite")
        try:
            row = connection.execute(
                "SELECT value FROM phase14_marker WHERE value = ?", (marker,)
            ).fetchone()
        finally:
            connection.close()
        return config.get("_windows_lifecycle_marker") == marker and record.get("marker") == marker and row == (marker,)
    except (OSError, ValueError, sqlite3.Error):
        return False


def _wait_for_server(home: Path, timeout: float = 20.0) -> int:
    registry = home / "cache" / "processes" / "servers"
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        records = list(registry.glob("*.json")) if registry.exists() else []
        if records:
            return int(json.loads(records[0].read_text(encoding="utf-8"))["pid"])
        time.sleep(0.1)
    raise AssertionError("managed MCP server did not register")


def _pid_alive(pid: int) -> bool:
    import psutil

    return psutil.pid_exists(pid)


def _wait_for_exit(pid: int, timeout: float = 30.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not _pid_alive(pid):
            return True
        time.sleep(0.1)
    return False


def _start_managed_server(home: Path) -> tuple[subprocess.Popen[bytes], int]:
    process = subprocess.Popen(
        [str(home / "venv" / "Scripts" / "neo-localmcp-server.exe")],
        stdin=subprocess.PIPE,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        env={**os.environ, "NEO_LOCALMCP_HOME": str(home)},
    )
    return process, _wait_for_server(home)


@pytest.mark.skipif(os.name != "nt", reason="Windows launcher relocation evidence")
@pytest.mark.slow
def test_promoted_windows_launchers_do_not_reference_staging(tmp_path: Path) -> None:
    """A moved venv must remain usable after its staging tree is removed."""

    paths = ManagedPaths(
        root=tmp_path / ".neo-localmcp",
        platform="windows",
        home=tmp_path,
        allow_test_root=True,
    )
    paths.ensure_directories()
    candidate = build_candidate(
        paths,
        source_root=REPO_ROOT,
        python_executable=Path(sys.executable),
        operation_id="windows-launcher-relocation",
    )
    assert candidate.build_ok, candidate.error

    promotion = promote_candidate(
        paths,
        candidate,
        expected_version=neo_localmcp.__version__,
    )
    assert promotion.ok, promotion.error
    assert not candidate.venv.exists()

    commands = {
        "python": (paths.python_executable, ("-c", "print('python-ok')"), "python-ok"),
        "pip": (paths.executable_dir / "pip.exe", ("--version",), "pip "),
        "cli": (paths.cli_executable, ("--help",), "usage:"),
        # The stdio server receives EOF immediately from subprocess.run and
        # should start and exit cleanly; it intentionally has no CLI parser.
        "server": (paths.server_executable, (), ""),
    }
    for name, (executable, args, expected) in commands.items():
        assert executable.exists(), f"missing promoted {name} launcher: {executable}"
        if executable.suffix == ".exe":
            assert str(candidate.venv).encode() not in executable.read_bytes()
        result = _run(executable, *args)
        assert result.returncode == 0, (
            f"promoted {name} launcher failed\nstdout={result.stdout}\nstderr={result.stderr}"
        )
        if expected:
            assert expected.casefold() in (result.stdout + result.stderr).casefold()


@pytest.mark.skipif(os.name != "nt", reason="Windows lifecycle evidence")
@pytest.mark.slow
def test_full_windows_lifecycle_via_setup(tmp_path: Path) -> None:
    home = tmp_path / ".neo-localmcp"
    marker = "phase14-preserved-data"
    unrelated = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(600)"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    managed_processes: list[subprocess.Popen[bytes]] = []
    try:
        result = _setup(home, "install", "--yes")
        assert result.returncode == 0, result.stdout + result.stderr
        _seed_durable_data(home, marker)

        server, pid = _start_managed_server(home)
        managed_processes.append(server)
        result = _setup(home, "reinstall")
        assert result.returncode == 0, result.stdout + result.stderr
        assert _wait_for_exit(pid), "live server survived reinstall"
        assert unrelated.poll() is None, "unrelated Python was terminated"
        assert _durable_data_survives(home, marker)

        server, pid = _start_managed_server(home)
        managed_processes.append(server)
        result = _setup(home, "uninstall", "--yes")
        assert result.returncode == 0, result.stdout + result.stderr
        assert _wait_for_exit(pid), "live server survived default uninstall"
        assert not (home / "venv").exists()
        assert _durable_data_survives(home, marker)
        assert unrelated.poll() is None, "unrelated Python was terminated"

        # A cancelled destructive install must leave durable data untouched.
        result = _setup(home, "install", "--clean", stdin="n\n")
        assert result.returncode == 2, result.stdout + result.stderr
        assert "without --yes" in (result.stdout + result.stderr)
        assert _durable_data_survives(home, marker)

        # A broken runtime is recoverable and preserved data is reused.
        (home / "venv").mkdir()
        (home / "venv" / "interrupted.txt").write_text("broken", encoding="utf-8")
        result = _setup(home, "install", "--yes")
        assert result.returncode == 0, result.stdout + result.stderr
        assert paths_are_runnable(home)
        assert _durable_data_survives(home, marker)

        # Metadata left in-progress by an interrupted reinstall is recoverable.
        managed_paths = ManagedPaths(
            root=home,
            platform="windows",
            home=home.parent,
            allow_test_root=True,
        )
        begin_operation(
            managed_paths,
            Operation.REINSTALL,
            source_version=neo_localmcp.__version__,
        )
        result = _setup(home, "install", "--yes")
        assert result.returncode == 0, result.stdout + result.stderr
        assert paths_are_runnable(home)
        assert _durable_data_survives(home, marker)

        result = _setup(home, "install", "--clean", "--yes")
        assert result.returncode == 0, result.stdout + result.stderr
        assert paths_are_runnable(home)
        assert not _durable_data_survives(home, marker)

        result = _setup(home, "uninstall", "--delete-memory", "--yes")
        assert result.returncode == 0, result.stdout + result.stderr
        assert not home.exists()
        assert unrelated.poll() is None, "unrelated Python was terminated"
    finally:
        for process in managed_processes:
            if process.poll() is None:
                process.kill()
            process.wait(timeout=10)
        if unrelated.poll() is None:
            unrelated.kill()
        unrelated.wait(timeout=10)


def paths_are_runnable(home: Path) -> bool:
    python = home / "venv" / "Scripts" / "python.exe"
    cli = home / "venv" / "Scripts" / "neo-localmcp.exe"
    return _run(python, "-c", "print('ok')").returncode == 0 and _run(cli, "--help").returncode == 0


@pytest.mark.skipif(os.name != "nt", reason="Windows process-tree evidence")
def test_registered_tree_escalates_without_killing_unrelated_python(tmp_path: Path) -> None:
    import psutil

    child_pid_file = tmp_path / "child.pid"
    root = subprocess.Popen(
        [
            sys.executable,
            "-c",
            (
                "import pathlib,subprocess,sys,time; "
                "p=subprocess.Popen([sys.executable,'-c','import time; time.sleep(600)']); "
                f"q=pathlib.Path({str(child_pid_file)!r}); t=q.with_suffix('.tmp'); "
                "t.write_text(str(p.pid)); t.replace(q); "
                "time.sleep(600)"
            ),
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    unrelated = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(600)"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    child_pid = None
    try:
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline and not child_pid_file.exists():
            time.sleep(0.05)
        assert child_pid_file.exists()
        child_pid = int(child_pid_file.read_text())
        identity = psutil.Process(root.pid)
        registrations = ({
            "pid": root.pid,
            "create_time": identity.create_time(),
            "source": "phase14-forced-escalation",
        },)
        paths = ManagedPaths(
            root=tmp_path / ".neo-localmcp",
            platform="windows",
            home=tmp_path,
            allow_test_root=True,
        )
        owned = discover_owned_processes(paths, registrations)
        assert {root.pid, child_pid}.issubset({process.pid for process in owned})
        assert unrelated.pid not in {process.pid for process in owned}

        result = stop_owned_processes(
            owned,
            registrations,
            graceful_request=lambda _pid: None,
            timeout=1.0,
        )
        assert result.ok
        assert root.pid in result.terminated
        assert child_pid in result.terminated
        assert _wait_for_exit(root.pid)
        assert _wait_for_exit(child_pid)
        assert unrelated.poll() is None
    finally:
        for process in (root, unrelated):
            if process.poll() is None:
                process.kill()
            process.wait(timeout=10)
        if child_pid is not None and psutil.pid_exists(child_pid):
            psutil.Process(child_pid).kill()
