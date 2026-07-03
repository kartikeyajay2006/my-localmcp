from __future__ import annotations

import os
from pathlib import Path
from typing import Iterable

from neo_localmcp.installer.paths import ManagedPaths
from neo_localmcp.installer.processes import (
    ProcessIdentity,
    ProcessSnapshot,
    discover_owned_processes,
    stop_owned_processes,
)


class FakeProcessProvider:
    def __init__(
        self,
        snapshots: Iterable[ProcessSnapshot],
        *,
        survives_terminate: Iterable[int] = (),
        survives_kill: Iterable[int] = (),
    ) -> None:
        self._snapshots = tuple(snapshots)
        self.alive = {snapshot.pid for snapshot in self._snapshots}
        self.survives_terminate = set(survives_terminate)
        self.survives_kill = set(survives_kill)
        self.terminated: list[int] = []
        self.killed: list[int] = []

    def snapshots(self) -> tuple[ProcessSnapshot, ...]:
        return self._snapshots

    def is_running(self, identity: ProcessIdentity) -> bool:
        return identity.pid in self.alive and any(
            snapshot.pid == identity.pid
            and snapshot.create_time == identity.create_time
            for snapshot in self._snapshots
        )

    def terminate(self, identity: ProcessIdentity) -> bool:
        if not self.is_running(identity):
            return False
        self.terminated.append(identity.pid)
        if identity.pid not in self.survives_terminate:
            self.alive.discard(identity.pid)
        return True

    def kill(self, identity: ProcessIdentity) -> bool:
        if not self.is_running(identity):
            return False
        self.killed.append(identity.pid)
        if identity.pid not in self.survives_kill:
            self.alive.discard(identity.pid)
        return True

    def wait(
        self,
        identities: Iterable[ProcessIdentity],
        timeout: float,
    ) -> tuple[tuple[ProcessIdentity, ...], tuple[ProcessIdentity, ...]]:
        del timeout
        gone: list[ProcessIdentity] = []
        alive: list[ProcessIdentity] = []
        for identity in identities:
            (alive if self.is_running(identity) else gone).append(identity)
        return tuple(gone), tuple(alive)

    def stop_for_graceful_request(self, pid: int) -> None:
        self.alive.discard(pid)


def _paths(tmp_path: Path) -> ManagedPaths:
    return ManagedPaths(
        root=tmp_path / ".neo-localmcp",
        platform="posix",
        home=tmp_path,
    )


def _snapshot(
    pid: int,
    ppid: int,
    create_time: float,
    executable: Path | None,
    command_line: tuple[str, ...] = (),
    *,
    accessible: bool = True,
) -> ProcessSnapshot:
    return ProcessSnapshot(
        pid=pid,
        ppid=ppid,
        create_time=create_time,
        name=executable.name if executable else None,
        executable=str(executable) if executable else None,
        command_line=command_line,
        accessible=accessible,
    )


def test_discovery_owns_only_verified_roots_and_descendants(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    extension_root = tmp_path / "Claude Extensions" / "local.mcpb.neo-localmcp.neo-localmcp"
    snapshots = (
        _snapshot(100, 1, 10.0, paths.python_executable),
        _snapshot(101, 100, 11.0, None, accessible=False),
        _snapshot(
            200,
            1,
            20.0,
            Path("/usr/local/bin/uv.exe"),
            ("uv.exe", "--directory", str(extension_root), "run"),
        ),
        _snapshot(201, 200, 21.0, Path("/usr/local/bin/python.exe")),
        _snapshot(202, 201, 22.0, Path("/Windows/System32/conhost.exe")),
        _snapshot(
            300,
            1,
            30.0,
            Path("/usr/local/bin/uv.exe"),
            ("uv.exe", "--directory", str(tmp_path / "other-extension"), "run"),
        ),
        _snapshot(301, 1, 31.0, Path("/usr/local/bin/python.exe")),
        _snapshot(400, 1, 41.0, Path("/usr/local/bin/python.exe")),
        _snapshot(500, 1, 50.0, None, accessible=False),
    )
    provider = FakeProcessProvider(snapshots)
    registrations = (
        {"pid": 100, "create_time": 10.0, "source": "managed-runtime"},
        {"pid": 400, "create_time": 40.0, "source": "managed-runtime"},
        {"command_root": str(extension_root), "source": "client-extension"},
    )

    owned = discover_owned_processes(paths, registrations, provider=provider)

    assert {process.pid for process in owned} == {100, 101, 200, 201, 202}
    by_pid = {process.pid: process for process in owned}
    assert by_pid[100].evidence in {"registered_identity", "managed_executable"}
    assert by_pid[101].evidence == "descendant_of:100"
    assert by_pid[101].source == "managed-runtime"
    assert by_pid[200].evidence == "registered_command_root"
    assert by_pid[202].depth == 2
    assert by_pid[202].source == "client-extension"


def test_discovery_never_targets_current_installer_process(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    provider = FakeProcessProvider(
        (_snapshot(os.getpid(), 1, 10.0, paths.python_executable),)
    )

    owned = discover_owned_processes(paths, (), provider=provider)

    assert owned == ()


def test_relative_or_non_neo_command_root_is_not_ownership_evidence(
    tmp_path: Path,
) -> None:
    paths = _paths(tmp_path)
    snapshot = _snapshot(
        200,
        1,
        20.0,
        Path("/usr/local/bin/uv.exe"),
        ("uv.exe", "relative/path", str(tmp_path / "other-product")),
    )
    provider = FakeProcessProvider((snapshot,))

    owned = discover_owned_processes(
        paths,
        (
            {"command_root": "relative/path", "source": "client-extension"},
            {"command_root": str(tmp_path / "other-product"), "source": "client-extension"},
        ),
        provider=provider,
    )

    assert owned == ()


def test_shutdown_requests_graceful_stop_then_terminates_and_kills_descendants(
    tmp_path: Path,
) -> None:
    paths = _paths(tmp_path)
    snapshots = (
        _snapshot(10, 1, 10.0, paths.python_executable),
        _snapshot(11, 10, 11.0, Path("/usr/local/bin/helper")),
        _snapshot(20, 1, 20.0, paths.python_executable),
        _snapshot(21, 20, 21.0, Path("/usr/local/bin/helper")),
        _snapshot(99, 1, 99.0, Path("/usr/local/bin/python")),
    )
    provider = FakeProcessProvider(snapshots, survives_terminate={21})
    registrations = (
        {"pid": 10, "create_time": 10.0, "source": "managed-runtime"},
    )
    owned = discover_owned_processes(paths, registrations, provider=provider)
    graceful_requests: list[int] = []

    def request_graceful(pid: int) -> None:
        graceful_requests.append(pid)
        provider.stop_for_graceful_request(pid)

    result = stop_owned_processes(
        owned,
        registrations,
        provider=provider,
        graceful_request=request_graceful,
        timeout=1.0,
    )

    assert result.ok is True
    assert graceful_requests == [10]
    assert result.gracefully_stopped == (10,)
    assert provider.terminated == [11, 21, 20]
    assert provider.killed == [21]
    assert 99 not in provider.terminated
    assert 99 not in provider.killed
    assert result.timed_out == ()


def test_shutdown_reports_verified_survivor_that_resists_kill(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    snapshot = _snapshot(20, 1, 20.0, paths.python_executable)
    provider = FakeProcessProvider(
        (snapshot,),
        survives_terminate={20},
        survives_kill={20},
    )
    owned = discover_owned_processes(paths, (), provider=provider)

    result = stop_owned_processes(
        owned,
        (),
        provider=provider,
        graceful_request=lambda _pid: None,
        timeout=0.1,
    )

    assert result.ok is False
    assert result.terminated == (20,)
    assert result.killed == (20,)
    assert result.timed_out == (20,)
