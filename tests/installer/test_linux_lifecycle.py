"""Real Linux setup-v2 lifecycle acceptance test."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

from .test_macos_lifecycle import (
    _drive_start_and_close,
    _drive_start_then_reinstall,
    _durable_marker_present,
    _run_setup_v2,
    _seed_durable_data,
    _wait_for_pid_exit,
)


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="Linux lifecycle evidence")
@pytest.mark.slow
def test_full_linux_lifecycle_via_setup_v2(tmp_path: Path) -> None:
    home = tmp_path / ".neo-localmcp"
    marker = "linux-preserved-data"

    result = _run_setup_v2(["install", "--yes"], home=home)
    assert result.returncode == 0, result.stdout + result.stderr
    _seed_durable_data(home, marker=marker)
    assert _durable_marker_present(home, marker=marker)

    pid, result = asyncio.run(_drive_start_then_reinstall(home))
    assert result.returncode == 0, result.stdout + result.stderr
    assert _wait_for_pid_exit(pid), "live server survived Linux reinstall"
    assert _durable_marker_present(home, marker=marker)

    result = _run_setup_v2(["uninstall", "--yes"], home=home)
    assert result.returncode == 0, result.stdout + result.stderr
    assert not (home / "venv").exists()
    assert _durable_marker_present(home, marker=marker)

    result = _run_setup_v2(["install", "--yes"], home=home)
    assert result.returncode == 0, result.stdout + result.stderr
    assert _durable_marker_present(home, marker=marker)

    result = _run_setup_v2(["install", "--clean", "--yes"], home=home)
    assert result.returncode == 0, result.stdout + result.stderr
    assert not _durable_marker_present(home, marker=marker)
    pid = asyncio.run(_drive_start_and_close(home))
    assert _wait_for_pid_exit(pid), "post-clean Linux server did not exit"

    result = _run_setup_v2(["uninstall", "--delete-memory", "--yes"], home=home)
    assert result.returncode == 0, result.stdout + result.stderr
    assert not home.exists()
