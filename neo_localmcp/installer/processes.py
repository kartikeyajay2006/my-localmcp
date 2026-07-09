"""Ownership-aware cross-platform process discovery and shutdown."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Protocol

import psutil

from .paths import ManagedPaths

CREATE_TIME_TOLERANCE_SECONDS = 0.02


@dataclass(frozen=True, order=True)
class ProcessIdentity:
    pid: int
    create_time: float


@dataclass(frozen=True)
class ProcessSnapshot:
    pid: int
    ppid: int
    create_time: float
    name: str | None
    executable: str | None
    command_line: tuple[str, ...]
    accessible: bool = True

    @property
    def identity(self) -> ProcessIdentity:
        return ProcessIdentity(self.pid, self.create_time)


@dataclass(frozen=True)
class OwnedProcess:
    identity: ProcessIdentity
    ppid: int
    executable: str | None
    command_line: tuple[str, ...]
    source: str
    evidence: str
    depth: int

    @property
    def pid(self) -> int:
        return self.identity.pid

    @property
    def create_time(self) -> float:
        return self.identity.create_time


@dataclass(frozen=True)
class ShutdownResult:
    ok: bool
    targets: tuple[int, ...]
    gracefully_stopped: tuple[int, ...]
    already_stopped: tuple[int, ...]
    terminated: tuple[int, ...]
    killed: tuple[int, ...]
    timed_out: tuple[int, ...]
    warnings: tuple[str, ...]


class ProcessProvider(Protocol):
    def snapshots(self) -> tuple[ProcessSnapshot, ...]: ...

    def is_running(self, identity: ProcessIdentity) -> bool: ...

    def terminate(self, identity: ProcessIdentity) -> bool: ...

    def kill(self, identity: ProcessIdentity) -> bool: ...

    def wait(
        self,
        identities: Iterable[ProcessIdentity],
        timeout: float,
    ) -> tuple[tuple[ProcessIdentity, ...], tuple[ProcessIdentity, ...]]: ...


class PsutilProcessProvider:
    """Thin psutil adapter; policy stays in the pure discovery/shutdown functions."""

    @staticmethod
    def _process(identity: ProcessIdentity) -> psutil.Process | None:
        try:
            process = psutil.Process(identity.pid)
            if abs(process.create_time() - identity.create_time) > CREATE_TIME_TOLERANCE_SECONDS:
                return None
            if not process.is_running():
                return None
            return process
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            return None

    def snapshots(self) -> tuple[ProcessSnapshot, ...]:
        snapshots: list[ProcessSnapshot] = []
        for process in psutil.process_iter(attrs=("pid", "ppid", "create_time", "name")):
            info = process.info
            try:
                with process.oneshot():
                    executable = process.exe() or None
                    command_line = tuple(process.cmdline())
                accessible = True
            except (psutil.AccessDenied, psutil.ZombieProcess):
                executable = None
                command_line = ()
                accessible = False
            except psutil.NoSuchProcess:
                continue
            try:
                snapshots.append(
                    ProcessSnapshot(
                        pid=int(info["pid"]),
                        ppid=int(info.get("ppid") or 0),
                        create_time=float(info.get("create_time") or process.create_time()),
                        name=str(info.get("name")) if info.get("name") else None,
                        executable=executable,
                        command_line=command_line,
                        accessible=accessible,
                    )
                )
            except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess, TypeError, ValueError):
                continue
        return tuple(snapshots)

    def is_running(self, identity: ProcessIdentity) -> bool:
        return self._process(identity) is not None

    def terminate(self, identity: ProcessIdentity) -> bool:
        process = self._process(identity)
        if process is None:
            return False
        try:
            process.terminate()
            return True
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            return False

    def kill(self, identity: ProcessIdentity) -> bool:
        process = self._process(identity)
        if process is None:
            return False
        try:
            process.kill()
            return True
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            return False

    def wait(
        self,
        identities: Iterable[ProcessIdentity],
        timeout: float,
    ) -> tuple[tuple[ProcessIdentity, ...], tuple[ProcessIdentity, ...]]:
        identity_by_pid = {identity.pid: identity for identity in identities}
        processes: list[psutil.Process] = []
        gone: list[ProcessIdentity] = []
        for identity in identity_by_pid.values():
            process = self._process(identity)
            if process is None:
                gone.append(identity)
            else:
                processes.append(process)
        waited_gone, waited_alive = psutil.wait_procs(
            processes,
            timeout=max(0.0, float(timeout)),
        )
        gone.extend(
            identity_by_pid[process.pid]
            for process in waited_gone
            if process.pid in identity_by_pid
        )
        alive = tuple(
            identity_by_pid[process.pid]
            for process in waited_alive
            if process.pid in identity_by_pid
        )
        return tuple(sorted(set(gone))), tuple(sorted(alive))


def _normalized(value: str | Path) -> str:
    return str(value).replace("\\", "/").rstrip("/").casefold()


def _is_below(executable: str, root: Path) -> bool:
    executable_value = _normalized(executable)
    root_value = _normalized(root.resolve(strict=False))
    return executable_value == root_value or executable_value.startswith(root_value + "/")


def _registered_identities(
    registrations: Iterable[Mapping[str, Any]],
) -> dict[ProcessIdentity, str]:
    identities: dict[ProcessIdentity, str] = {}
    for registration in registrations:
        try:
            identity = ProcessIdentity(
                int(registration["pid"]),
                float(registration["create_time"]),
            )
        except (KeyError, TypeError, ValueError):
            continue
        identities[identity] = str(registration.get("source") or "managed-runtime")
    return identities


def _registered_command_roots(
    registrations: Iterable[Mapping[str, Any]],
) -> tuple[tuple[str, str], ...]:
    roots: list[tuple[str, str]] = []
    for registration in registrations:
        raw_root = registration.get("command_root")
        if not raw_root:
            continue
        path = Path(str(raw_root)).expanduser()
        if not path.is_absolute():
            continue
        normalized = _normalized(path.resolve(strict=False))
        if "neo-localmcp" not in normalized:
            continue
        roots.append(
            (normalized, str(registration.get("source") or "client-extension"))
        )
    return tuple(roots)


def discover_owned_processes(
    paths: ManagedPaths,
    registrations: Iterable[Mapping[str, Any]],
    *,
    provider: ProcessProvider | None = None,
) -> tuple[OwnedProcess, ...]:
    process_provider = provider or PsutilProcessProvider()
    registration_list = tuple(registrations)
    registered = _registered_identities(registration_list)
    command_roots = _registered_command_roots(registration_list)
    snapshots = process_provider.snapshots()
    owned: dict[int, OwnedProcess] = {}
    current_pid = os.getpid()

    for snapshot in snapshots:
        if snapshot.pid == current_pid:
            continue
        source: str | None = None
        evidence: str | None = None
        if snapshot.identity in registered:
            source = registered[snapshot.identity]
            evidence = "registered_identity"
        elif snapshot.accessible and snapshot.executable and _is_below(
            snapshot.executable, paths.venv
        ):
            source = "managed-runtime"
            evidence = "managed_executable"
        elif snapshot.accessible and snapshot.command_line:
            command = _normalized(" ".join(snapshot.command_line))
            for command_root, registered_source in command_roots:
                if command_root in command:
                    source = registered_source
                    evidence = "registered_command_root"
                    break
        if source and evidence:
            owned[snapshot.pid] = OwnedProcess(
                identity=snapshot.identity,
                ppid=snapshot.ppid,
                executable=snapshot.executable,
                command_line=snapshot.command_line,
                source=source,
                evidence=evidence,
                depth=0,
            )

    changed = True
    while changed:
        changed = False
        for snapshot in snapshots:
            if snapshot.pid == current_pid or snapshot.pid in owned:
                continue
            parent = owned.get(snapshot.ppid)
            if parent is None:
                continue
            owned[snapshot.pid] = OwnedProcess(
                identity=snapshot.identity,
                ppid=snapshot.ppid,
                executable=snapshot.executable,
                command_line=snapshot.command_line,
                source=parent.source,
                evidence=f"descendant_of:{parent.pid}",
                depth=parent.depth + 1,
            )
            changed = True

    return tuple(sorted(owned.values(), key=lambda process: (process.depth, process.pid)))


def stop_owned_processes(
    owned_processes: Iterable[OwnedProcess],
    registrations: Iterable[Mapping[str, Any]],
    *,
    provider: ProcessProvider | None = None,
    graceful_request: Callable[[int], None] | None = None,
    timeout: float = 12.0,
) -> ShutdownResult:
    from neo_localmcp import mcp_server_lifecycle as lifecycle

    process_provider = provider or PsutilProcessProvider()
    request_stop = graceful_request or lifecycle.request_stop
    owned = tuple(owned_processes)
    registered = _registered_identities(tuple(registrations))
    warnings: list[str] = []
    already_stopped = tuple(
        sorted(
            process.pid
            for process in owned
            if not process_provider.is_running(process.identity)
        )
    )

    graceful_candidates = tuple(
        process
        for process in owned
        if process.identity in registered
        and process_provider.is_running(process.identity)
    )
    for process in graceful_candidates:
        try:
            request_stop(process.pid)
        except OSError as exc:
            warnings.append(f"Could not request graceful stop for PID {process.pid}: {exc}")

    gracefully_stopped: tuple[int, ...] = ()
    if graceful_candidates:
        gone, _alive = process_provider.wait(
            (process.identity for process in graceful_candidates),
            timeout,
        )
        gracefully_stopped = tuple(sorted(identity.pid for identity in gone))

    survivors = [
        process
        for process in owned
        if process_provider.is_running(process.identity)
    ]
    survivors.sort(key=lambda process: (-process.depth, process.pid))
    terminated: list[int] = []
    for process in survivors:
        if process_provider.terminate(process.identity):
            terminated.append(process.pid)

    _gone, alive_after_terminate = process_provider.wait(
        (process.identity for process in survivors),
        timeout,
    )
    alive_identities = set(alive_after_terminate)
    kill_candidates = [
        process for process in survivors if process.identity in alive_identities
    ]
    kill_candidates.sort(key=lambda process: (-process.depth, process.pid))
    killed: list[int] = []
    for process in kill_candidates:
        if process_provider.kill(process.identity):
            killed.append(process.pid)

    _gone, alive_after_kill = process_provider.wait(
        (process.identity for process in kill_candidates),
        timeout,
    )
    timed_out = tuple(sorted(identity.pid for identity in alive_after_kill))
    return ShutdownResult(
        ok=not timed_out,
        targets=tuple(sorted(process.pid for process in owned)),
        gracefully_stopped=gracefully_stopped,
        already_stopped=already_stopped,
        terminated=tuple(terminated),
        killed=tuple(killed),
        timed_out=timed_out,
        warnings=tuple(warnings),
    )
