"""Archived end-to-end proof of the legacy PowerShell upgrade cycle (not collected).

Historical proof of the graceful upgrade cycle on Windows (1.0.7 P7c, single-venv
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
import sqlite3
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


def _run_ps1(script: str, app_home: Path, extra_args: list[str] | None = None, timeout: int = 300, stdin_text: str | None = None) -> subprocess.CompletedProcess:
    env = {**os.environ, "NEO_LOCALMCP_HOME": str(app_home)}
    args = ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(REPO_ROOT / script)]
    if extra_args:
        args.extend(extra_args)
    return subprocess.run(args, cwd=REPO_ROOT, env=env, input=stdin_text, capture_output=True, text=True, timeout=timeout)


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
    servers_dir = app_home / "cache" / "processes" / "servers"
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
        assert (app_home / "config" / "config.yaml").exists()
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


def _seed_full_memory_db(db_path: Path) -> list[tuple]:
    """Create a realistic repo-context.sqlite with the real full schema (as a db that
    has already seen use would have) plus two retrieval_boost rows, and return those
    rows. Uses repo_memory.init_db so the schema can never drift from production."""
    from neo_localmcp import repo_memory

    seeded = [
        ("repo-abc", "widget|rollup", "docs/plan.md", "m9.2 beta widget rollup", 7, 5, 1, "2026-06-01T00:00:00+00:00"),
        ("repo-xyz", "startup|warm", "neo_localmcp/ollama_client.py", "", 4, 4, 0, "2026-06-15T12:30:00+00:00"),
    ]
    conn = sqlite3.connect(db_path)
    try:
        conn.row_factory = sqlite3.Row
        repo_memory.init_db(conn)
        conn.executemany("INSERT INTO retrieval_boost VALUES(?,?,?,?,?,?,?,?)", seeded)
        conn.commit()
    finally:
        conn.close()
    return seeded


def test_retrieval_memory_survives_a_real_venv_upgrade(tmp_path):
    """1.0.9 (P9g) upgrade-persistence guarantee. The retrieval-boost memory lives in
    repo-context.sqlite directly under APP_DIR, never inside a versioned venv, and
    install.ps1's removal sweep only ever targets .venv-nlm-v*/venvs/venv paths. This
    asserts that never-tested claim: seed retrieval_boost rows, stage a different-version
    venv so install.ps1 does a real upgrade sweep, install, and prove the rows survive
    intact.

    Note the guarantee is DATA-level, not file-byte-level: install runs
    'neo-localmcp init'/'doctor', which legitimately open and write to the db (indexer
    version, FTS pages, the SQLite header change counter), so the file bytes DO change
    on every upgrade -- verified live. What must never change is the memory data."""
    app_home = tmp_path / ".neo-localmcp"
    app_home.mkdir()

    db = app_home / "repo-context.sqlite"
    seeded = _seed_full_memory_db(db)

    # Stage a leftover different-version venv so install.ps1 exercises its real
    # upgrade sweep (removing another version's venv) rather than a fresh install.
    stale_venv = app_home / ".venv-nlm-v0.0.1"
    (stale_venv / "Scripts").mkdir(parents=True)
    (stale_venv / "Scripts" / "python.exe").write_text("stale", encoding="utf-8")

    install = _run_ps1("install.ps1", app_home)
    assert install.returncode == 0, f"install.ps1 failed:\n{install.stdout}\n{install.stderr}"

    # The stale venv must have been swept and the current one built...
    assert not stale_venv.exists(), "install.ps1 should have removed the other-version venv"
    assert _venv_dirs(app_home), "install.ps1 should have built the current-version venv"

    # ...and every seeded retrieval_boost row must survive exactly, none added or lost.
    assert db.exists(), "the memory database must survive the upgrade"
    conn = sqlite3.connect(db)
    try:
        after = conn.execute(
            "SELECT repo_id, term_key, path, heading_name, shown_count, followed_count, corrected_count, last_updated_at "
            "FROM retrieval_boost ORDER BY repo_id"
        ).fetchall()
    finally:
        conn.close()
    assert [tuple(r) for r in after] == sorted(seeded), "retrieval_boost rows were altered/lost across an upgrade -- memory did not survive"


def test_uninstall_granular_switches_keep_and_delete_independently(tmp_path):
    """1.0.9 (P9f): uninstall.ps1's granular switches must delete/keep each category
    independently. -KeepVenv -RemoveServers leaves the runtime but clears the
    registry; a follow-up -RemoveConfig -RemoveDatabase clears the data while the
    default venv removal proceeds. Config/db are seeded directly so no live server
    is required."""
    app_home = tmp_path / ".neo-localmcp"

    install = _run_ps1("install.ps1", app_home)
    assert install.returncode == 0, f"install.ps1 failed:\n{install.stdout}\n{install.stderr}"

    # Seed the categories the switches act on.
    (app_home / "config.yaml").write_text("{}", encoding="utf-8")
    (app_home / "repo-context.sqlite").write_text("db", encoding="utf-8")
    servers_dir = app_home / "servers"
    servers_dir.mkdir(exist_ok=True)
    (servers_dir / "1234.json").write_text("{}", encoding="utf-8")

    # Keep the runtime, clear only the servers/ registry.
    keep = _run_ps1("uninstall.ps1", app_home, extra_args=["-KeepVenv", "-RemoveServers"])
    assert keep.returncode == 0, f"granular uninstall failed:\n{keep.stdout}\n{keep.stderr}"
    assert _venv_dirs(app_home), "-KeepVenv must not remove the venv"
    assert not servers_dir.exists(), "-RemoveServers must remove the servers/ directory"
    assert (app_home / "config.yaml").exists(), "config.yaml must be preserved without -RemoveConfig"
    assert (app_home / "repo-context.sqlite").exists(), "database must be preserved without -RemoveDatabase"

    # Now remove the data categories; venv removal proceeds by default.
    wipe = _run_ps1("uninstall.ps1", app_home, extra_args=["-RemoveConfig", "-RemoveDatabase"])
    assert wipe.returncode == 0, f"data uninstall failed:\n{wipe.stdout}\n{wipe.stderr}"
    assert not _venv_dirs(app_home), "default uninstall must remove the venv"
    assert not (app_home / "config.yaml").exists(), "-RemoveConfig must delete config.yaml"
    assert not (app_home / "repo-context.sqlite").exists(), "-RemoveDatabase must delete the database"


def test_setup_wizard_uninstall_surface_actually_deletes_selected_data(tmp_path):
    """1.0.9 (P9f) regression guard for the wizard->uninstall.ps1 splat: setup.ps1
    must pass the granular switches through as real switch parameters. An earlier
    cut splatted an array of '-Switch' strings, which PowerShell binds as positional
    VALUES, silently dropping every switch so a user who typed DELETE to wipe their
    database had it preserved. Driving the wizard end-to-end (not uninstall.ps1
    directly) is the only thing that catches that -- the direct-flag test above does
    not exercise the splat at all."""
    app_home = tmp_path / ".neo-localmcp"
    install = _run_ps1("install.ps1", app_home)
    assert install.returncode == 0, f"install.ps1 failed:\n{install.stdout}\n{install.stderr}"
    (app_home / "repo-context.sqlite").write_text("db", encoding="utf-8")

    # Menu 2 (Uninstall) -> surface 4 (CLI + local state) -> remove runtime (default)
    # -> remove mcpb (default) -> remove servers (y) -> delete config (y) -> delete
    # database (y) -> config DELETE gate -> database DELETE gate -> menu 5 (Exit).
    stdin_text = "\n".join(["2", "4", "", "", "y", "y", "y", "DELETE", "DELETE", "5"]) + "\n"
    wizard = _run_ps1("setup.ps1", app_home, stdin_text=stdin_text)
    assert wizard.returncode == 0, f"setup.ps1 wizard failed:\n{wizard.stdout}\n{wizard.stderr}"
    assert not _venv_dirs(app_home), "wizard uninstall must remove the venv"
    assert not (app_home / "config.yaml").exists(), "wizard must delete config.yaml when DELETE is typed"
    assert not (app_home / "repo-context.sqlite").exists(), "wizard must delete the database when DELETE is typed"
