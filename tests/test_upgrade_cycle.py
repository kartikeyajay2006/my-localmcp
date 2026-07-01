"""End-to-end proof of the graceful upgrade cycle on Windows (1.0.7 P7c, single-venv
scheme from 1.0.8).

Runs the real install.ps1 / uninstall.ps1 scripts against a fully isolated
NEO_LOCALMCP_HOME (never the developer's real ~/.neo-localmcp), spawns a genuine
long-lived server from the installed CLI, and confirms uninstall.ps1 stops it
gracefully (no lock error, no force-kill needed) rather than failing on a locked
venv file the way the pre-1.0.7 scripts did. Also proves the 1.0.8 single-venv
behavior: a second install of the same version is a fast no-op (no rebuild).

This is a real subprocess/venv/pip integration test, not a unit test -- it is slow
(one full venv build + pip install) and Windows-only (PowerShell + Windows process
semantics), matching the PowerShell-only nature of the scripts under test.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(
    os.name != "nt" or shutil.which("powershell.exe") is None,
    reason="install.ps1/uninstall.ps1 are Windows PowerShell scripts",
)

REPO_ROOT = Path(__file__).parents[1]


def _run_ps1(script: str, app_home: Path, extra_args: list[str] | None = None, timeout: int = 300) -> subprocess.CompletedProcess:
    env = {**os.environ, "NEO_LOCALMCP_HOME": str(app_home)}
    args = ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(REPO_ROOT / script)]
    if extra_args:
        args.extend(extra_args)
    return subprocess.run(args, cwd=REPO_ROOT, env=env, capture_output=True, text=True, timeout=timeout)


def _installed_cmd(app_home: Path) -> Path:
    return app_home / "bin" / "neo-localmcp.cmd"


def _source_version() -> str:
    import re

    text = (REPO_ROOT / "neo_localmcp" / "__init__.py").read_text(encoding="utf-8")
    match = re.search(r'__version__\s*=\s*"([^"]+)"', text)
    assert match, "could not read __version__ from neo_localmcp/__init__.py"
    return match.group(1)


def _venv_dirs(app_home: Path) -> list[Path]:
    if not app_home.exists():
        return []
    return sorted(app_home.glob(".venv-nlm-v*"))


def _wait_for_one_registered_server(app_home: Path, timeout: float = 15.0) -> int:
    servers_dir = app_home / "servers"
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if servers_dir.exists():
            entries = list(servers_dir.glob("*.json"))
            if entries:
                data = json.loads(entries[0].read_text(encoding="utf-8"))
                return int(data["pid"])
        time.sleep(0.2)
    raise AssertionError("server never registered itself within the timeout")


def _pid_alive(pid: int) -> bool:
    sys.path.insert(0, str(REPO_ROOT))
    from neo_localmcp import lifecycle

    return lifecycle.pid_alive(pid)


def test_install_then_uninstall_while_server_is_live(tmp_path):
    # uninstall.ps1 has a pre-existing safety guard requiring the app-home directory
    # to literally be named ".neo-localmcp" (a check against ever deleting an
    # arbitrary path), so the isolated fixture must use that exact name too.
    app_home = tmp_path / ".neo-localmcp"

    install = _run_ps1("install.ps1", app_home)
    assert install.returncode == 0, f"install.ps1 failed:\n{install.stdout}\n{install.stderr}"
    cmd = _installed_cmd(app_home)
    assert cmd.exists(), "install.ps1 did not produce the expected launcher"

    version = _source_version()
    venvs = _venv_dirs(app_home)
    assert len(venvs) == 1, f"expected exactly one venv, found {[v.name for v in venvs]}"
    assert venvs[0].name == f".venv-nlm-v{version}", f"unexpected venv name: {venvs[0].name}"

    # Spawn a genuine long-lived server from the freshly installed CLI. Piping a
    # long sleep into it keeps its stdin open (mirrors an attached MCP client)
    # without needing a full MCP handshake for this test's purpose.
    server_proc = subprocess.Popen(
        ["cmd.exe", "/c", str(cmd), "serve"],
        cwd=REPO_ROOT,
        env={**os.environ, "NEO_LOCALMCP_HOME": str(app_home)},
        stdin=subprocess.PIPE,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        pid = _wait_for_one_registered_server(app_home)
        assert _pid_alive(pid), "registered server PID is not actually alive"

        uninstall = _run_ps1("uninstall.ps1", app_home)
        assert uninstall.returncode == 0, (
            f"uninstall.ps1 failed while a server was live -- this is exactly the "
            f"pre-1.0.7 lock bug the graceful-stop mechanism exists to prevent:\n"
            f"{uninstall.stdout}\n{uninstall.stderr}"
        )
        assert "removed" in uninstall.stdout.lower()

        # The graceful stop (not this test) should have already ended the server.
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and _pid_alive(pid):
            time.sleep(0.2)
        assert not _pid_alive(pid), "server process was still alive after a successful uninstall"

        assert not _venv_dirs(app_home)
        assert not (app_home / "bin").exists()
        # Data is preserved by default (no -RemoveData was passed).
        assert (app_home / "config.yaml").exists()
    finally:
        # Best-effort cleanup: the server should already be gone; do not fail the
        # test over this, but don't leave a process behind if something went wrong.
        if server_proc.poll() is None:
            server_proc.kill()
        try:
            server_proc.stdin.close()
        except Exception:
            pass


def test_reinstalling_same_version_is_a_fast_noop_not_a_rebuild(tmp_path):
    """1.0.8: a venv is per-version, not per-install-run. Installing the same
    version twice must reuse the existing venv rather than rebuilding it -- this
    is what actually stops the repeated-pip-install disk churn this test was
    written in response to."""
    app_home = tmp_path / ".neo-localmcp"

    first = _run_ps1("install.ps1", app_home)
    assert first.returncode == 0, f"first install.ps1 failed:\n{first.stdout}\n{first.stderr}"
    venvs = _venv_dirs(app_home)
    assert len(venvs) == 1
    venv_dir = venvs[0]
    created_at = venv_dir.stat().st_ctime
    marker = venv_dir / "Scripts" / "python.exe"
    marker_mtime = marker.stat().st_mtime

    second = _run_ps1("install.ps1", app_home)
    assert second.returncode == 0, f"second install.ps1 failed:\n{second.stdout}\n{second.stderr}"
    assert "skipping venv rebuild" in second.stdout.lower()

    venvs_after = _venv_dirs(app_home)
    assert len(venvs_after) == 1, "reinstalling the same version must not create a second venv"
    assert venvs_after[0] == venv_dir
    assert venv_dir.stat().st_ctime == created_at, "the venv directory was recreated, not reused"
    assert marker.stat().st_mtime == marker_mtime, "python.exe was rewritten -- the venv was rebuilt, not skipped"


def test_repair_flag_forces_rebuild_of_same_version(tmp_path):
    app_home = tmp_path / ".neo-localmcp"

    first = _run_ps1("install.ps1", app_home)
    assert first.returncode == 0, f"first install.ps1 failed:\n{first.stdout}\n{first.stderr}"
    venv_dir = _venv_dirs(app_home)[0]
    # python.exe is copied by the venv module preserving the source interpreter's
    # mtime, so mtime is identical across every rebuild -- ctime (file creation
    # time) is the only reliable "was this file actually recreated" signal here.
    marker = venv_dir / "Scripts" / "python.exe"
    marker_ctime = marker.stat().st_ctime

    repaired = _run_ps1("install.ps1", app_home, extra_args=["-Repair"])
    assert repaired.returncode == 0, f"-Repair install.ps1 failed:\n{repaired.stdout}\n{repaired.stderr}"
    assert "skipping venv rebuild" not in repaired.stdout.lower()

    venvs_after = _venv_dirs(app_home)
    assert len(venvs_after) == 1
    assert marker.stat().st_ctime != marker_ctime, "-Repair should have rebuilt the venv"
