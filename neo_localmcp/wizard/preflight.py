"""Stdlib-only dependency gate for the setup wizard.

Nothing here may import ``textual``, ``psutil``, or any ``neo_localmcp`` module
that imports them -- this runs on a bare interpreter straight off a fresh clone.
Its only job is to make sure the wizard's dependencies are importable, offering
to ``pip install`` them for the user if they are not, and then to re-exec so the
freshly-installed packages are picked up cleanly.
"""

from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
from pathlib import Path

WIZARD_DEPENDENCIES = ("textual", "psutil")
WIZARD_EXTRA = ".[wizard]"


def missing_dependencies() -> list[str]:
    """Return the wizard dependencies that are not importable, in order."""
    return [name for name in WIZARD_DEPENDENCIES if importlib.util.find_spec(name) is None]


def _install_command(repo_root: Path) -> list[str]:
    return [sys.executable, "-m", "pip", "install", "-e", WIZARD_EXTRA]


def _prompt_yes_no(question: str, *, default: bool = True) -> bool:
    suffix = " [Y/n] " if default else " [y/N] "
    try:
        answer = input(question + suffix).strip().lower()
    except (EOFError, KeyboardInterrupt):
        print()
        return False
    if not answer:
        return default
    return answer in {"y", "yes"}


def ensure_dependencies(repo_root: Path, argv: list[str]) -> None:
    """Guarantee the wizard's dependencies are present, or exit trying.

    If everything is importable this returns immediately. Otherwise it prints
    exactly what is missing and the command that fixes it, offers to run that
    command, and -- on success -- re-execs the wizard so the new packages load
    in a clean interpreter. Declining, or a failed install, exits non-zero with
    the manual command so the user is never left guessing.
    """
    missing = missing_dependencies()
    if not missing:
        return

    print("neo-localmcp setup wizard")
    print("-" * 26)
    print(
        "The wizard needs a couple of packages that are not installed yet:\n"
        f"  missing: {', '.join(missing)}\n"
    )
    command = _install_command(repo_root)
    printable = "pip install -e \".[wizard]\""
    print(f"They can be installed from this checkout with:\n  {printable}\n")

    if not _prompt_yes_no("Install them now?"):
        print(
            "\nSkipped. Install the dependencies yourself and re-run:\n"
            f"  {printable}\n"
            "  python setup_wizard.py"
        )
        raise SystemExit(1)

    print(f"\nRunning: {printable}\n")
    completed = subprocess.run(command, cwd=str(repo_root))
    if completed.returncode != 0:
        print(
            "\nDependency installation failed (see the pip output above).\n"
            f"Install them manually and re-run:\n  {printable}\n  python setup_wizard.py"
        )
        raise SystemExit(completed.returncode)

    still_missing = missing_dependencies()
    if still_missing:
        print(
            "\npip reported success but these are still not importable: "
            f"{', '.join(still_missing)}.\n"
            "You may be using a different interpreter than the one pip installed into. "
            f"Re-run the wizard with:\n  {sys.executable} setup_wizard.py"
        )
        raise SystemExit(1)

    # Re-exec so the freshly-installed packages load in a clean process. Preserve
    # the original arguments (e.g. --fake) across the restart.
    print("\nDependencies installed. Starting the wizard...\n")
    entry = str(Path(argv[0]).resolve()) if argv else "setup_wizard.py"
    os.execv(sys.executable, [sys.executable, entry, *argv[1:]])
