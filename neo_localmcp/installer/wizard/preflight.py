"""Stdlib-only dependency gate for the setup wizard.

Nothing here may import ``psutil`` or any ``neo_localmcp`` module that imports it
-- this runs on a bare interpreter straight off a fresh clone. Its only job is to
make sure the wizard's one dependency is importable, offering to ``pip install``
it for the user if it is not, and then to re-exec so the freshly-installed
package is picked up cleanly.
"""

from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
from pathlib import Path

# psutil is the only third-party import the wizard (and the setup lifecycle it
# drives) needs; it is already a base runtime dependency. The plain numbered UI
# is stdlib-only, so there is no TUI toolkit to install.
WIZARD_DEPENDENCIES = ("psutil",)
WIZARD_EXTRA = ".[wizard]"


def missing_dependencies() -> list[str]:
    # wizard dependencies not importable in the current interpreter, in order
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
    # everything importable -> return immediately; else print what's missing, offer to pip install, then re-exec into a clean interpreter on success
    # decline, or a failed/incomplete install -> exit non-zero with the manual command, never leaves the user guessing
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
    # the original arguments (e.g. --preview) across the restart.
    print("\nDependencies installed. Starting the wizard...\n")
    entry = str(Path(argv[0]).resolve()) if argv else "setup_wizard.py"
    os.execv(sys.executable, [sys.executable, entry, *argv[1:]])
