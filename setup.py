"""Packaging-compatible front door for the neo-localmcp setup lifecycle."""

from __future__ import annotations

import sys


PYTHON_FLOOR = (3, 12)
_SETUPTOOLS_COMMANDS = {
    "bdist_wheel",
    "build",
    "build_ext",
    "clean",
    "dist_info",
    "editable_wheel",
    "egg_info",
    "sdist",
}


def _python_floor_message(required: tuple[int, int]) -> str:
    return (
        f"neo-localmcp requires Python {required[0]}.{required[1]} or newer.\n"
        "Install a supported system Python, then run:\n"
        "    python3.12 setup.py install"
    )


def _is_setuptools_invocation(argv: list[str]) -> bool:
    return any(argument in _SETUPTOOLS_COMMANDS for argument in argv[1:])


if sys.version_info[:2] < PYTHON_FLOOR:
    print(_python_floor_message(PYTHON_FLOOR), file=sys.stderr)
    raise SystemExit(2)


if __name__ == "__main__":
    if _is_setuptools_invocation(sys.argv):
        from setuptools import setup

        setup()
    else:
        from neo_localmcp.setup_cli import main

        raise SystemExit(main())
