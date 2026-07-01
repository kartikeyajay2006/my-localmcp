"""Cross-process server lifecycle: registration, discovery, and graceful stop.

Background (1.0.7, P7b)
-----------------------
`server.main()` calls FastMCP's `mcp.run()`, which blocks the main thread inside
`anyio.run(run_stdio_async)` reading stdin. FastMCP installs no signal handlers,
and on Windows `os.kill(pid, SIGTERM)` maps to `TerminateProcess` -- an *external*
force-kill, which is what left orphaned console hosts and DWM ghost window frames
during upgrades, and what risks the venv DLL staying locked.

The reliable, portable mechanism is therefore not a signal but a **stop-request
file** that each server watches for, actuated by a **process-initiated** exit:

- Every server registers itself on startup (`servers/<pid>.json`) recording its PID
  and `sys.executable` (so a caller can target servers by which venv they run from).
- A daemon watcher thread polls for `servers/<pid>.stop`. When it appears, the
  server flushes, unregisters, and calls `os._exit(0)` -- a *self* exit, which does
  full OS-level process teardown (releasing file locks, detaching console cleanly,
  no ghost frames), unlike an external terminate.
- `neo-localmcp stop` writes the stop file(s) for the targeted PIDs and waits for
  them to disappear, escalating to a real terminate only as a last resort.

This lives alongside the existing `ollama-supervisor.json` state-file convention in
`ollama_client.py`; it is pure filesystem + a thread, no third-party dependency.
"""

from __future__ import annotations

import json
import os
import signal
import sys
import threading
import time
from pathlib import Path
from typing import Any, Callable

from . import config

DEFAULT_POLL_SECONDS = 0.4
DEFAULT_STOP_TIMEOUT_SECONDS = 12.0


def _servers_root() -> Path:
    # Resolved dynamically from config.APP_DIR (not bound at import) so it follows
    # both the NEO_LOCALMCP_HOME env used by spawned servers and the test fixture's
    # APP_DIR override, without any caller needing to remember to patch it.
    return config.APP_DIR / "servers"


def _servers_dir() -> Path:
    root = _servers_root()
    root.mkdir(parents=True, exist_ok=True)
    return root


def _registry_path(pid: int) -> Path:
    return _servers_dir() / f"{pid}.json"


def _stop_path(pid: int) -> Path:
    return _servers_dir() / f"{pid}.stop"


# --- process liveness (stdlib only, no psutil) -------------------------------


def pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name == "nt":
        import ctypes

        SYNCHRONIZE = 0x00100000
        WAIT_TIMEOUT = 0x00000102
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.OpenProcess(SYNCHRONIZE, False, int(pid))
        if not handle:
            # NULL handle => process does not exist (or is inaccessible, which for a
            # same-user server we treat as gone rather than risk a false "alive").
            return False
        try:
            # WAIT_TIMEOUT means the process object is not signaled => still running.
            # This avoids the STILL_ACTIVE(259) exit-code ambiguity of GetExitCodeProcess.
            return kernel32.WaitForSingleObject(handle, 0) == WAIT_TIMEOUT
        finally:
            kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def force_terminate(pid: int) -> bool:
    """Last-resort external termination. Only reached when a graceful stop times out."""
    try:
        if os.name == "nt":
            os.kill(pid, signal.SIGTERM)  # maps to TerminateProcess on Windows
        else:
            os.kill(pid, signal.SIGKILL)
        return True
    except (ProcessLookupError, OSError):
        return False


# --- registry ----------------------------------------------------------------


def register_server(version: str) -> dict[str, Any]:
    pid = os.getpid()
    data = {
        "pid": pid,
        "executable": sys.executable,
        "started_at": time.time(),
        "version": version,
    }
    path = _registry_path(pid)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data), encoding="utf-8")
    os.replace(tmp, path)  # atomic publish
    return data


def unregister_server(pid: int | None = None) -> None:
    pid = os.getpid() if pid is None else pid
    for path in (_registry_path(pid), _stop_path(pid)):
        try:
            path.unlink()
        except OSError:
            pass


def _read_registry_entry(path: Path) -> dict[str, Any] | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def list_servers(prune: bool = True) -> list[dict[str, Any]]:
    """Return registered servers with an `alive` flag, pruning dead entries by default."""
    root = _servers_root()
    if not root.exists():
        return []
    servers: list[dict[str, Any]] = []
    for path in sorted(root.glob("*.json")):
        entry = _read_registry_entry(path)
        if not entry or "pid" not in entry:
            if prune:
                try:
                    path.unlink()
                except OSError:
                    pass
            continue
        pid = int(entry["pid"])
        alive = pid_alive(pid)
        if not alive and prune:
            # A server that died without unregistering (crash / force-kill). Clean up.
            unregister_server(pid)
            continue
        servers.append({**entry, "alive": alive})
    return servers


# --- stop coordination -------------------------------------------------------


def request_stop(pid: int) -> None:
    _stop_path(pid).write_text(json.dumps({"requested_at": time.time(), "by": os.getpid()}), encoding="utf-8")


def stop_requested(pid: int) -> bool:
    return _stop_path(pid).exists()


def resolve_stop_targets(pid: int | None = None, all_servers: bool = False, match_executable: str | None = None) -> list[int]:
    if pid is not None:
        return [int(pid)]
    live = [s for s in list_servers(prune=True) if s.get("alive")]
    if all_servers:
        return [int(s["pid"]) for s in live]
    if match_executable:
        needle = match_executable.replace("\\", "/").lower()
        return [int(s["pid"]) for s in live if needle in str(s.get("executable", "")).replace("\\", "/").lower()]
    return []


def stop_servers(pids: list[int], timeout: float = DEFAULT_STOP_TIMEOUT_SECONDS, allow_force: bool = True, poll: float = 0.2) -> dict[str, Any]:
    """Request graceful stop for each PID, wait up to `timeout`, escalate to force
    only as a last resort. Returns a per-PID outcome and a summary."""
    pids = [int(p) for p in pids]
    outcomes: dict[int, str] = {}
    for pid in pids:
        if not pid_alive(pid):
            outcomes[pid] = "already_stopped"
            continue
        request_stop(pid)
        outcomes[pid] = "stop_requested"

    pending = [p for p in pids if outcomes[p] == "stop_requested"]
    deadline = time.monotonic() + max(0.0, float(timeout))
    while pending and time.monotonic() < deadline:
        time.sleep(poll)
        still = []
        for pid in pending:
            if pid_alive(pid):
                still.append(pid)
            else:
                outcomes[pid] = "stopped_gracefully"
                unregister_server(pid)  # tidy any stop-file the exited server left behind
        pending = still

    forced: list[int] = []
    timed_out: list[int] = []
    for pid in pending:
        if allow_force and force_terminate(pid):
            outcomes[pid] = "force_terminated"
            forced.append(pid)
            unregister_server(pid)
        else:
            outcomes[pid] = "stop_timed_out"
            timed_out.append(pid)

    return {
        "ok": not timed_out,
        "targets": pids,
        "outcomes": {str(k): v for k, v in outcomes.items()},
        "forced": forced,
        "timed_out": timed_out,
        "note": (
            "Force-termination was used as a last resort; a graceful stop is preferred. "
            "This can happen for a pre-1.0.7 server that predates the stop watcher."
            if forced else None
        ),
    }


# --- server-side watcher -----------------------------------------------------


def _graceful_self_exit() -> None:
    """Best-effort cleanup, then a process-initiated exit.

    os._exit is deliberate: it is a *self* exit (clean OS-level teardown that
    releases file locks and detaches the console without the ghost-frame artifact
    an external TerminateProcess leaves), and it does not depend on unwinding the
    blocked anyio/mcp.run loop on the main thread. It skips Python atexit/buffer
    flushing, so we flush and unregister here first. The server holds no long-lived
    in-process state -- SQLite connections are per-call and closed, and the WAL
    protects integrity against an abrupt stop exactly as it would a crash -- so an
    in-flight tool call being cut off is acceptable (the client reconnects to the
    new server). If a future version holds per-connection state, drain it here
    before the exit.
    """
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.flush()
        except Exception:
            pass
    try:
        unregister_server()
    except Exception:
        pass
    os._exit(0)


def start_stop_watcher(poll: float = DEFAULT_POLL_SECONDS, on_stop: Callable[[], None] = _graceful_self_exit) -> threading.Thread:
    """Start a daemon thread that exits the process when its stop-file appears."""
    pid = os.getpid()

    def _loop() -> None:
        while True:
            if stop_requested(pid):
                on_stop()
                return  # on_stop normally never returns; injectable for tests
            time.sleep(poll)

    thread = threading.Thread(target=_loop, name="neo-localmcp-stop-watcher", daemon=True)
    thread.start()
    return thread
