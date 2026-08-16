"""Minimal stdlib-only ANSI color helpers for the console wizard.

No dependency: on Windows, a no-op ``os.system("")`` call is enough to make
modern consoles (Windows Terminal, cmd.exe on Windows 10 1511+) process ANSI
escape sequences, so no ctypes/SetConsoleMode call is needed. Color is
disabled automatically when the ``NO_COLOR`` env var is set (any value, per
the no-color.org convention) or when stdout isn't a real terminal (piped
output, e.g. under test), so scripted/piped runs are unaffected.
"""

from __future__ import annotations

import os
import sys

if sys.platform.startswith("win"):
    os.system("")


def _supports_color() -> bool:
    # NO_COLOR set, or stdout not a real terminal (piped/test) -> no color
    if os.environ.get("NO_COLOR") is not None:
        return False
    try:
        return sys.stdout.isatty()
    except Exception:
        return False


COLOR_ENABLED = _supports_color()

_CODES = {
    "bold": "1",
    "dim": "2",
    "red": "31",
    "green": "32",
    "yellow": "33",
    "cyan": "36",
}


def _wrap(text: str, *codes: str) -> str:
    if not COLOR_ENABLED or not text:
        return text
    seq = ";".join(_CODES[c] for c in codes)
    return f"\033[{seq}m{text}\033[0m"


def dim(text: str) -> str:
    return _wrap(text, "dim")


def cyan_bold(text: str) -> str:
    return _wrap(text, "cyan", "bold")


def yellow(text: str) -> str:
    return _wrap(text, "yellow")


def green(text: str) -> str:
    return _wrap(text, "green")


def red_bold(text: str) -> str:
    return _wrap(text, "red", "bold")
