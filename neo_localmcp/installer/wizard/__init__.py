"""Guided terminal installer (TUI) for neo-localmcp.

This package intentionally imports nothing heavy at the top level: ``preflight``
is stdlib-only so it can run on a bare interpreter, and the Textual-dependent
modules (``app``, ``screens``) are imported only after dependencies are ensured.
"""
