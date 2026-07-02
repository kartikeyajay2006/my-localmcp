"""Parser / help / exit-code / dry-run / safety-refusal tests for setup_v2.py.

These tests exercise the thin CLI dispatcher in isolation, invoking
``setup_v2.main()`` in-process (fast) and, for the Python-floor guard, via a
real subprocess (so a faked ``sys.version_info`` cannot be short-circuited by
import caching).

No test here builds a real venv or touches a real managed root: every install/
reinstall/uninstall invocation either hits --dry-run (which must mutate
nothing) or a destructive-without---yes safety refusal, both of which exit
before any operation is invoked. The real, non-dry-run lifecycle is covered by
the separate slow acceptance test in test_macos_lifecycle.py.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SETUP_V2 = REPO_ROOT / "setup_v2.py"


def _run(argv: list[str], *, env: dict[str, str] | None = None, stdin_data: str | None = None) -> subprocess.CompletedProcess:
    import os

    full_env = {**os.environ, **(env or {})}
    return subprocess.run(
        [sys.executable, str(SETUP_V2), *argv],
        capture_output=True,
        text=True,
        env=full_env,
        input=stdin_data,
        timeout=30,
    )


def _isolated_env(tmp_path: Path) -> dict[str, str]:
    return {"NEO_LOCALMCP_HOME": str(tmp_path / ".neo-localmcp")}


# --------------------------------------------------------------------------- #
# Step 1: parser / help surface
# --------------------------------------------------------------------------- #


def test_top_level_help_exits_zero() -> None:
    result = _run(["--help"])
    assert result.returncode == 0
    assert "install" in result.stdout
    assert "reinstall" in result.stdout
    assert "uninstall" in result.stdout


def test_top_level_short_help_exits_zero() -> None:
    result = _run(["-h"])
    assert result.returncode == 0


@pytest.mark.parametrize("operation", ["install", "reinstall", "uninstall"])
def test_subcommand_help_exits_zero(operation: str) -> None:
    result = _run([operation, "--help"])
    assert result.returncode == 0
    assert operation in result.stdout or "--dry-run" in result.stdout


@pytest.mark.parametrize("operation", ["install", "reinstall", "uninstall"])
def test_subcommand_short_help_exits_zero(operation: str) -> None:
    result = _run([operation, "-h"])
    assert result.returncode == 0


def test_no_arguments_is_a_usage_error() -> None:
    result = _run([])
    assert result.returncode == 2


def test_unknown_operation_is_a_usage_error() -> None:
    result = _run(["frobnicate"])
    assert result.returncode == 2


def test_unknown_flag_is_a_usage_error() -> None:
    result = _run(["install", "--not-a-real-flag"])
    assert result.returncode == 2


def test_reinstall_rejects_clean_flag_as_usage_error() -> None:
    # --clean is install-only; passing it to reinstall must fail before any
    # mutation, same as any other argparse-level incompatibility.
    result = _run(["reinstall", "--clean"])
    assert result.returncode == 2


def test_install_rejects_delete_memory_flag_as_usage_error() -> None:
    # --delete-memory is uninstall-only.
    result = _run(["install", "--delete-memory"])
    assert result.returncode == 2


# --------------------------------------------------------------------------- #
# Step 2: Python-floor bootstrap
# --------------------------------------------------------------------------- #


def test_python_floor_guard_runs_before_package_import(tmp_path: Path) -> None:
    """Simulate an old interpreter by faking sys.version_info before the guard
    runs, in a fresh subprocess. If the guard truly runs before any
    neo_localmcp import, the process must print the floor message and exit 2
    without ever attempting to import the package (which would raise/behave
    differently under a faked low version tuple due to 3.12-only syntax)."""

    script = tmp_path / "fake_old_python.py"
    script.write_text(
        "import sys\n"
        "sys.version_info = (3, 8, 0, 'final', 0)\n"
        f"sys.argv = [{str(SETUP_V2)!r}, 'install']\n"
        f"with open({str(SETUP_V2)!r}) as f:\n"
        "    source = f.read()\n"
        f"exec(compile(source, {str(SETUP_V2)!r}, 'exec'), {{'__name__': '__main__'}})\n",
        encoding="utf-8",
    )
    result = subprocess.run(
        [sys.executable, str(script)],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 2
    assert "Python 3.12+ required" in result.stderr or "requires Python 3.12" in result.stderr
    # The floor guard must fire before neo_localmcp (and thus psutil) is ever
    # imported; a partial/failed package import would surface as a traceback
    # mentioning neo_localmcp/psutil instead of the clean floor message.
    assert "Traceback" not in result.stderr


def test_python_floor_message_content() -> None:
    import importlib.util

    spec = importlib.util.spec_from_file_location("setup_v2_floor_check", SETUP_V2)
    assert spec is not None and spec.loader is not None
    # We only inspect the constant/function; do not execute main().
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    message = module._python_floor_message((3, 12))
    assert "3.12" in message
    assert "neo-localmcp requires Python" in message


# --------------------------------------------------------------------------- #
# Step 5: dry-run mutates nothing; safety refusal; exit codes
# --------------------------------------------------------------------------- #


def test_install_dry_run_mutates_nothing(tmp_path: Path) -> None:
    home = tmp_path / ".neo-localmcp"
    result = _run(["install", "--dry-run"], env=_isolated_env(tmp_path))
    assert result.returncode == 0
    assert "DRY RUN" in result.stdout or "DRY RUN" in result.stderr
    assert not home.exists()


def test_reinstall_dry_run_mutates_nothing(tmp_path: Path) -> None:
    home = tmp_path / ".neo-localmcp"
    result = _run(["reinstall", "--dry-run"], env=_isolated_env(tmp_path))
    assert result.returncode == 0
    assert not home.exists()


def test_uninstall_dry_run_mutates_nothing(tmp_path: Path) -> None:
    home = tmp_path / ".neo-localmcp"
    result = _run(["uninstall", "--dry-run"], env=_isolated_env(tmp_path))
    assert result.returncode == 0
    assert not home.exists()


def test_install_clean_dry_run_mutates_nothing_and_shows_plan(tmp_path: Path) -> None:
    home = tmp_path / ".neo-localmcp"
    result = _run(["install", "--clean", "--dry-run", "--yes"], env=_isolated_env(tmp_path))
    assert result.returncode == 0
    assert not home.exists()
    combined = result.stdout + result.stderr
    assert "action plan" in combined.lower()


def test_uninstall_delete_memory_dry_run_mutates_nothing(tmp_path: Path) -> None:
    home = tmp_path / ".neo-localmcp"
    result = _run(["uninstall", "--delete-memory", "--dry-run", "--yes"], env=_isolated_env(tmp_path))
    assert result.returncode == 0
    assert not home.exists()


def test_install_clean_noninteractive_without_yes_is_safety_refusal(tmp_path: Path) -> None:
    home = tmp_path / ".neo-localmcp"
    home.mkdir(parents=True)
    marker = home / "sentinel.txt"
    marker.write_text("do not delete me")

    # No --dry-run: this is the real path. Non-interactive (no TTY; input=""
    # closes stdin immediately) and lacking --yes must refuse before any
    # mutation -- exit 2, and the sentinel file must survive untouched.
    result = _run(["install", "--clean"], env=_isolated_env(tmp_path), stdin_data="")
    assert result.returncode == 2
    assert marker.exists()
    assert marker.read_text() == "do not delete me"


def test_uninstall_delete_memory_noninteractive_without_yes_is_safety_refusal(tmp_path: Path) -> None:
    home = tmp_path / ".neo-localmcp"
    home.mkdir(parents=True)
    marker = home / "sentinel.txt"
    marker.write_text("do not delete me")

    result = _run(["uninstall", "--delete-memory"], env=_isolated_env(tmp_path), stdin_data="")
    assert result.returncode == 2
    assert marker.exists()


def test_install_plain_without_yes_is_not_a_refusal(tmp_path: Path) -> None:
    # --yes with no destructive flag is harmless/ignored; a plain, non-clean
    # install has nothing to refuse at the CLI safety layer (whatever happens
    # next is the operation's own business, not exercised here since we only
    # assert the refusal gate itself does not misfire for non-destructive
    # invocations). Using --dry-run keeps this fast and mutation-free.
    result = _run(["install", "--dry-run"], env=_isolated_env(tmp_path), stdin_data="")
    assert result.returncode == 0
