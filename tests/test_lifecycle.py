from __future__ import annotations

import asyncio
import os
import sys
import time
from pathlib import Path

from neo_localmcp import lifecycle


# --- registry + liveness units (isolated) ------------------------------------


def test_register_list_unregister_roundtrip(isolated_config):
    data = lifecycle.register_server("9.9.9")
    pid = data["pid"]
    assert pid == os.getpid()
    servers = lifecycle.list_servers()
    assert any(s["pid"] == pid and s["alive"] for s in servers)
    assert any("version" in s and s["version"] == "9.9.9" for s in servers)
    lifecycle.unregister_server()
    assert not any(s["pid"] == pid for s in lifecycle.list_servers())


def test_registry_records_executable_for_targeting(isolated_config):
    lifecycle.register_server("9.9.9")
    servers = lifecycle.list_servers()
    entry = next(s for s in servers if s["pid"] == os.getpid())
    assert entry["executable"] == sys.executable
    lifecycle.unregister_server()


def test_pid_alive_true_for_self_false_for_bogus(isolated_config):
    assert lifecycle.pid_alive(os.getpid()) is True
    assert lifecycle.pid_alive(2_000_000_000) is False
    assert lifecycle.pid_alive(0) is False
    assert lifecycle.pid_alive(-1) is False


def test_list_servers_prunes_dead_entries(isolated_config):
    # Hand-write a registry entry for a PID that cannot be alive.
    dead_pid = 2_000_000_001
    servers_dir = lifecycle._servers_dir()
    (servers_dir / f"{dead_pid}.json").write_text(
        '{"pid": %d, "executable": "x", "started_at": 0, "version": "0"}' % dead_pid, encoding="utf-8"
    )
    listed = lifecycle.list_servers(prune=True)
    assert not any(s["pid"] == dead_pid for s in listed)
    assert not (servers_dir / f"{dead_pid}.json").exists()


def test_resolve_targets_by_pid_all_and_executable(isolated_config):
    lifecycle.register_server("9.9.9")
    me = os.getpid()
    assert lifecycle.resolve_stop_targets(pid=me) == [me]
    assert me in lifecycle.resolve_stop_targets(all_servers=True)
    # match on a substring of our own interpreter path
    needle = Path(sys.executable).name  # e.g. python.exe / python
    assert me in lifecycle.resolve_stop_targets(match_executable=needle)
    # a non-matching substring finds nothing
    assert lifecycle.resolve_stop_targets(match_executable="definitely-not-a-real-path-xyz") == []
    # no selector => empty (never accidentally stops everything)
    assert lifecycle.resolve_stop_targets() == []
    lifecycle.unregister_server()


def test_request_and_detect_stop(isolated_config):
    lifecycle.register_server("9.9.9")
    me = os.getpid()
    assert lifecycle.stop_requested(me) is False
    lifecycle.request_stop(me)
    assert lifecycle.stop_requested(me) is True
    lifecycle.unregister_server()  # also clears the stop file
    assert lifecycle.stop_requested(me) is False


def test_watcher_triggers_on_stop_without_exiting_the_test(isolated_config):
    """The watcher loop must fire on_stop exactly once when the stop file appears.
    We inject a benign on_stop so the test process is not actually killed."""
    fired = {"count": 0}

    def fake_on_stop():
        fired["count"] += 1

    lifecycle.register_server("9.9.9")
    thread = lifecycle.start_stop_watcher(poll=0.05, on_stop=fake_on_stop)
    lifecycle.request_stop(os.getpid())
    deadline = time.monotonic() + 3
    while fired["count"] == 0 and time.monotonic() < deadline:
        time.sleep(0.05)
    assert fired["count"] >= 1
    thread.join(timeout=1)
    lifecycle.unregister_server()


def test_stop_servers_reports_already_stopped_for_dead_pid(isolated_config):
    result = lifecycle.stop_servers([2_000_000_002], timeout=1.0)
    assert result["ok"] is True
    assert result["outcomes"]["2000000002"] == "already_stopped"


# --- real spawned-server integration -----------------------------------------


def test_spawned_server_stops_gracefully_and_unregisters(tmp_path):
    """Spawn a real server subprocess, wait for it to self-register, request a
    graceful stop, and assert it exits within budget and cleans up its registry
    entry -- with no force-termination needed."""
    root = Path(__file__).parents[1]
    app_home = tmp_path / "app"
    env = {
        **os.environ,
        "NEO_LOCALMCP_HOME": str(app_home),
        "PYTHONPATH": str(root),
        "PYTHONUNBUFFERED": "1",
    }
    asyncio.run(_spawn_register_and_stop(app_home, env))


async def _spawn_register_and_stop(app_home: Path, env: dict) -> None:
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    params = StdioServerParameters(command=sys.executable, args=["-m", "neo_localmcp.server"], env=env)
    servers_dir = app_home / "servers"

    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            # Give the server a moment to register, then read its PID from the registry.
            pid = None
            for _ in range(50):
                if servers_dir.exists():
                    entries = list(servers_dir.glob("*.json"))
                    if entries:
                        import json

                        data = json.loads(entries[0].read_text(encoding="utf-8"))
                        pid = int(data["pid"])
                        break
                await asyncio.sleep(0.1)
            assert pid is not None, "server never registered itself"

            # Request graceful stop from 'outside' by writing the stop file directly
            # (this is exactly what `neo-localmcp stop` does), then confirm the process
            # exits on its own within budget -- no force-kill.
            (servers_dir / f"{pid}.stop").write_text("{}", encoding="utf-8")

            deadline = time.monotonic() + 10
            exited = False
            while time.monotonic() < deadline:
                if not _alive(pid):
                    exited = True
                    break
                await asyncio.sleep(0.1)

    assert exited, "server did not exit after a graceful stop request"
    # Registry entry should be gone (self-unregistered on the way out).
    assert not (servers_dir / f"{pid}.json").exists()


def _alive(pid: int) -> bool:
    # Standalone liveness check for the test (the module's own, but importable here).
    return lifecycle.pid_alive(pid)
