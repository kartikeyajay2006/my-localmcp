#!/usr/bin/env python3
"""Guided terminal installer for neo-localmcp.

A friendlier front door than ``setup.py``: instead of remembering flags, run

    python setup_wizard.py

and a simple full-screen numbered wizard walks you through install / reinstall /
uninstall, client registration, and Ollama model selection, explaining every
option and the OS-specific paths involved and building up a summary of your
answers as it goes. It drives the *same* lifecycle operations ``setup.py`` uses
(``neo_localmcp.installer``); it is a friendlier caller, not a second
implementation.

This file is deliberately stdlib-only above the dependency gate. It must import
and run on a bare interpreter straight off a fresh clone, because its whole job
before drawing anything is to make sure the wizard's one dependency (``psutil``,
already a runtime dependency) is present -- offering to install it for you if it
is not. Nothing that needs it may be imported until ``ensure_dependencies`` has
returned.

Flags:
    --fake   Run against an in-memory simulation. No processes, venvs, network,
             or files are touched -- a safe way to walk the whole flow. Set
             NEO_LOCALMCP_WIZARD_FAKE_STATE=healthy to simulate a returning user
             (already-installed) instead of a first-time clone.
"""

from __future__ import annotations

import sys
from pathlib import Path

PYTHON_FLOOR = (3, 12)

if sys.version_info[:2] < PYTHON_FLOOR:
    current = "%d.%d.%d" % sys.version_info[:3]
    sys.stderr.write(
        f"neo-localmcp's setup wizard requires Python {PYTHON_FLOOR[0]}.{PYTHON_FLOOR[1]}+ ; "
        f"found {current}.\n"
        "Install a newer interpreter and re-run:  python3.12 setup_wizard.py\n"
    )
    raise SystemExit(2)

# Make the checked-out ``neo_localmcp`` package importable even before an editable
# install, so the stdlib-only preflight below can run straight off a clone.
REPO_ROOT = Path(__file__).resolve().parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


def main() -> int:
    # Stdlib-only preflight -- may print, prompt, pip-install, and re-exec.
    from neo_localmcp.wizard.preflight import ensure_dependencies

    ensure_dependencies(REPO_ROOT, sys.argv)

    # Dependencies are guaranteed present past this point.
    from neo_localmcp.wizard.console import run

    return run(sys.argv[1:])


if __name__ == "__main__":
    raise SystemExit(main())
