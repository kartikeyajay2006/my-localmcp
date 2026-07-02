"""Real end-to-end macOS lifecycle acceptance test for setup_v2.py.

This is the Task 13 Gate: prove the entire install -> reinstall -> uninstall ->
install -> install --clean -> uninstall --delete-memory lifecycle works on this
Mac end to end, driven exclusively through ``setup_v2.py`` (never `.ps1`/`.sh`),
against a temporary ``NEO_LOCALMCP_HOME``, without losing seeded durable data
along the way (except where a full wipe is explicitly requested).

It builds real venvs and is therefore slow (multiple full pip installs) --
marked ``@pytest.mark.slow`` like the existing real-build tests in
``test_runtime.py`` / ``test_verification.py``, and excluded from the default
test run the same way.
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

import neo_localmcp

REPO_ROOT = Path(__file__).resolve().parents[2]
SETUP_V2 = REPO_ROOT / "setup_v2.py"
EXPECTED_VERSION = neo_localmcp.__version__

SETUP_V2_TIMEOUT_SECONDS = 600.0


def _run_setup_v2(argv: list[str], *, home: Path) -> subprocess.CompletedProcess:
    env = {**os.environ, "NEO_LOCALMCP_HOME": str(home)}
    return subprocess.run(
        [sys.executable, str(SETUP_V2), *argv],
        capture_output=True,
        text=True,
        env=env,
        timeout=SETUP_V2_TIMEOUT_SECONDS,
    )


def _seed_durable_data(home: Path, *, marker: str) -> None:
    """Seed config, a fake SQLite file, and a client-registration record so we
    can assert they survive (or are wiped) across operations."""
    config_dir = home / "config"
    sqlite_dir = home / "sqlite"
    clients_dir = home / "clients"
    config_dir.mkdir(parents=True, exist_ok=True)
    sqlite_dir.mkdir(parents=True, exist_ok=True)
    clients_dir.mkdir(parents=True, exist_ok=True)

    # config.yaml is actually loaded as JSON (see neo_localmcp/config.py); it must
    # stay valid JSON so real code paths (e.g. Ollama-model lookups during
    # reinstall's unload step) keep working, with the marker embedded in an
    # unused custom field purely for this test's own before/after assertions.
    existing_config: dict = {}
    config_path = config_dir / "config.yaml"
    if config_path.exists():
        try:
            existing_config = json.loads(config_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            existing_config = {}
    existing_config["_test_marker"] = marker
    config_path.write_text(json.dumps(existing_config, indent=2), encoding="utf-8")

    # A real (schema-valid) SQLite database, not garbage bytes: `doctor`'s
    # required db_open/repo-status checks (exercised during reinstall's
    # verification step) open this file for real, so it must actually be a
    # database. The marker lives in the generic key/value `metadata` table.
    import sqlite3

    db_path = sqlite_dir / "repo-context.sqlite"
    conn = sqlite3.connect(db_path)
    try:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS metadata (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            """
        )
        conn.execute(
            "INSERT OR REPLACE INTO metadata (key, value, updated_at) VALUES ('_test_marker', ?, ?)",
            (marker, str(time.time())),
        )
        conn.commit()
    finally:
        conn.close()

    (clients_dir / "seeded-client-record.json").write_text(
        json.dumps({"marker": marker, "client": "test-fixture"}), encoding="utf-8"
    )


def _durable_marker_present(home: Path, *, marker: str) -> bool:
    import sqlite3

    config_path = home / "config" / "config.yaml"
    sqlite_path = home / "sqlite" / "repo-context.sqlite"
    client_path = home / "clients" / "seeded-client-record.json"
    if not (config_path.exists() and sqlite_path.exists() and client_path.exists()):
        return False
    try:
        config_marker = json.loads(config_path.read_text(encoding="utf-8")).get("_test_marker")
    except json.JSONDecodeError:
        config_marker = None

    sqlite_marker = None
    conn = sqlite3.connect(sqlite_path)
    try:
        row = conn.execute(
            "SELECT value FROM metadata WHERE key = '_test_marker'"
        ).fetchone()
        sqlite_marker = row[0] if row else None
    except sqlite3.DatabaseError:
        sqlite_marker = None
    finally:
        conn.close()

    return (
        config_marker == marker
        and sqlite_marker == marker
        and marker in client_path.read_text(encoding="utf-8")
    )


def _managed_python(home: Path) -> Path:
    return home / "venv" / "bin" / "python"


def _pid_alive(pid: int) -> bool:
    from neo_localmcp import lifecycle

    return lifecycle.pid_alive(pid)


def _wait_for_pid_exit(pid: int, *, timeout: float = 30.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not _pid_alive(pid):
            return True
        time.sleep(0.2)
    return False


async def _read_registered_pid(home: Path, *, timeout: float = 10.0) -> int:
    servers_dir = home / "cache" / "processes" / "servers"
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if servers_dir.exists():
            entries = list(servers_dir.glob("*.json"))
            if entries:
                data = json.loads(entries[0].read_text(encoding="utf-8"))
                return int(data["pid"])
        await asyncio.sleep(0.1)
    raise AssertionError("managed server never registered itself")


async def _start_session_get_pid(home: Path):
    """Open a real MCP stdio session against the managed server and return the
    (session, stdio_context, pid) triple. Caller owns closing session/context,
    and must do so from the same asyncio task that opened them (anyio task
    groups are task-affine)."""
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    python_executable = _managed_python(home)
    assert python_executable.exists(), f"managed python missing: {python_executable}"

    env = {**os.environ, "NEO_LOCALMCP_HOME": str(home)}
    params = StdioServerParameters(command=str(python_executable), args=["-m", "neo_localmcp.server"], env=env)

    context = stdio_client(params)
    read, write = await context.__aenter__()
    session = ClientSession(read, write)
    await session.__aenter__()
    await session.initialize()

    pid = await _read_registered_pid(home)
    return session, context, pid


async def _close_session(session, context) -> None:
    await session.__aexit__(None, None, None)
    await context.__aexit__(None, None, None)


async def _drive_start_then_reinstall(home: Path) -> tuple[int, subprocess.CompletedProcess]:
    """Start the managed MCP server, confirm it registered, disconnect our
    client (releasing our end of stdio), then run `reinstall` and return the
    original PID plus the reinstall's CompletedProcess -- all inside one
    asyncio task so the stdio_client task group is entered/exited consistently."""
    session, context, pid = await _start_session_get_pid(home)
    assert _pid_alive(pid)
    await _close_session(session, context)

    result = await asyncio.to_thread(_run_setup_v2, ["reinstall"], home=home)
    return pid, result


async def _drive_start_and_close(home: Path) -> int:
    session, context, pid = await _start_session_get_pid(home)
    assert _pid_alive(pid)
    await _close_session(session, context)
    return pid


@pytest.mark.slow
def test_full_macos_lifecycle_via_setup_v2(tmp_path: Path) -> None:
    home = tmp_path / ".neo-localmcp"
    marker_a = "marker-a-original-data"

    # ---------------------------------------------------------------- #
    # install -> seed config/SQLite/client record -> start MCP
    # ---------------------------------------------------------------- #
    result = _run_setup_v2(["install", "--yes"], home=home)
    assert result.returncode == 0, f"install failed:\nSTDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
    assert (home / "venv").exists()

    _seed_durable_data(home, marker=marker_a)
    assert _durable_marker_present(home, marker=marker_a)

    # ---------------------------------------------------------------- #
    # start MCP -> reinstall -> verify PID exited and data survived
    # ---------------------------------------------------------------- #
    pid, reinstall_result = asyncio.run(_drive_start_then_reinstall(home))
    assert reinstall_result.returncode == 0, (
        f"reinstall failed:\nSTDOUT:\n{reinstall_result.stdout}\nSTDERR:\n{reinstall_result.stderr}"
    )

    assert _wait_for_pid_exit(pid), "server PID did not exit after reinstall"
    assert _durable_marker_present(home, marker=marker_a), "durable data lost across reinstall"
    assert (home / "venv").exists()

    # ---------------------------------------------------------------- #
    # uninstall -> verify venv absent / data present
    # ---------------------------------------------------------------- #
    result = _run_setup_v2(["uninstall", "--yes"], home=home)
    assert result.returncode == 0, f"uninstall failed:\nSTDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"

    assert not (home / "venv").exists(), "uninstall should remove the managed venv"
    assert _durable_marker_present(home, marker=marker_a), "default uninstall must preserve durable data"

    # ---------------------------------------------------------------- #
    # install -> verify reuse message/data
    # ---------------------------------------------------------------- #
    result = _run_setup_v2(["install", "--yes"], home=home)
    assert result.returncode == 0, f"reuse install failed:\nSTDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
    combined_output = result.stdout + result.stderr
    assert "preserved memory" in combined_output.lower() or "reusing" in combined_output.lower(), (
        f"expected a preserved/reused memory message, got:\n{combined_output}"
    )
    assert (home / "venv").exists()
    assert _durable_marker_present(home, marker=marker_a), "install-over-existing-data must reuse it"

    # ---------------------------------------------------------------- #
    # install --clean --yes -> verify old data absent / new endpoint healthy
    # ---------------------------------------------------------------- #
    result = _run_setup_v2(["install", "--clean", "--yes"], home=home)
    assert result.returncode == 0, f"clean install failed:\nSTDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"

    assert not _durable_marker_present(home, marker=marker_a), "clean install must wipe prior durable data"
    assert (home / "venv").exists()

    # The endpoint being reachable and completing a real MCP initialize
    # handshake is the "new endpoint healthy" proof.
    new_pid = asyncio.run(_drive_start_and_close(home))
    assert _wait_for_pid_exit(new_pid), "post-clean-install server did not exit cleanly after disconnect"

    # ---------------------------------------------------------------- #
    # uninstall --delete-memory --yes -> verify root absent
    # ---------------------------------------------------------------- #
    result = _run_setup_v2(["uninstall", "--delete-memory", "--yes"], home=home)
    assert result.returncode == 0, f"full wipe failed:\nSTDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"

    assert not home.exists(), "uninstall --delete-memory must remove the entire managed root"
