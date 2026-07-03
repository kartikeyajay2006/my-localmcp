#!/usr/bin/env python3
"""Cross-platform setup entrypoint: the thin install/reinstall/uninstall CLI.

This module is deliberately dumb. It parses arguments, builds an
``OperationContext`` from real sources (``ManagedPaths.from_environment()``,
this checkout as ``source_root``, ``sys.executable``, a real ``Reporter``),
invokes exactly one operation from :mod:`neo_localmcp.installer`, renders the
result, and returns an exit code. It contains NO path, migration, process,
runtime, or deletion policy -- that all lives in ``neo_localmcp/installer/``.

Exit codes:
    0 -- operation succeeded, OR was cancelled without error (e.g. an
         interactive user declined a full-wipe confirmation).
    1 -- the operation ran and failed.
    2 -- a usage error (argparse's own default), OR a safety refusal: a
         destructive flag (``--clean`` / ``--delete-memory``) was requested
         non-interactively (no TTY) without ``--yes``. That refusal happens
         BEFORE any operation is invoked and before any mutation occurs.

The Python-floor guard below is the first thing this file does, and it is
stdlib-only by construction: nothing below it may be imported until the
version check has already passed, because ``neo_localmcp`` (psutil, 3.12-only
syntax elsewhere in the package) may not be importable on an old interpreter.
"""

from __future__ import annotations

import sys

# --------------------------------------------------------------------------- #
# Step 2: Python-floor bootstrap -- stdlib-only, runs before ANY package import
# --------------------------------------------------------------------------- #

PYTHON_FLOOR = (3, 12)


def _python_floor_message(floor: tuple[int, int] = PYTHON_FLOOR) -> str:
    current = "%d.%d.%d" % sys.version_info[:3]
    floor_text = "%d.%d" % floor
    return (
        f"neo-localmcp requires Python {floor_text}+ ; found {current}.\n"
        f"Install a Python {floor_text} or newer interpreter and re-run this script with it, e.g.:\n"
        f"    python3.12 setup.py install"
    )


if sys.version_info[:2] < PYTHON_FLOOR:
    print(_python_floor_message(), file=sys.stderr)
    raise SystemExit(2)


# --------------------------------------------------------------------------- #
# Everything below this line may import the package -- the floor check above
# has already passed.
# --------------------------------------------------------------------------- #

import argparse  # noqa: E402
import time  # noqa: E402
from pathlib import Path  # noqa: E402

from neo_localmcp.installer import (  # noqa: E402
    ManagedPaths,
    Operation,
    OperationContext,
    OperationResult,
    OperationStatus,
    Reporter,
    confirm_full_wipe,
    detect_state,
    install,
    operation_explanation,
    reinstall,
    uninstall,
)

EXIT_SUCCESS = 0
EXIT_FAILURE = 1
EXIT_USAGE_OR_REFUSAL = 2

# Static, known ordered action plans rendered by --dry-run. These describe the
# *shape* of each operation for user-facing preview only; they are presentation,
# not policy -- the real ordered calls live in neo_localmcp/installer/operations.py.
_DRY_RUN_PLANS: dict[str, tuple[str, ...]] = {
    "install": (
        "validate source checkout and interpreter",
        "detect current install state",
        "stop owned processes (if any)",
        "unload Neo-used Ollama models",
        "record/restore selected client registrations",
        "migrate a recognized legacy layout (no-op otherwise)",
        "build a candidate runtime venv",
        "promote the candidate (atomic; rolls back on failure)",
        "restore client registrations against the promoted launcher",
        "verify the installation (CLI, MCP handshake, doctor)",
    ),
    "install --clean": (
        "validate source checkout and interpreter",
        "detect current install state",
        "stop owned processes (if any)",
        "unload Neo-used Ollama models",
        "confirm full wipe (interactive prompt or --yes)",
        "remove active client registrations and delete registration records",
        "delete the entire validated managed root",
        "recreate managed directories",
        "record newly selected client registrations",
        "build a candidate runtime venv",
        "promote the candidate (atomic; rolls back on failure)",
        "restore client registrations against the promoted launcher",
        "verify the installation (CLI, MCP handshake, doctor)",
    ),
    "reinstall": (
        "validate source checkout and interpreter",
        "detect current install state",
        "stop owned processes (if any)",
        "unload Neo-used Ollama models",
        "snapshot current client registrations",
        "migrate a recognized legacy layout (no-op otherwise)",
        "build a candidate runtime venv",
        "promote the candidate (atomic; rolls back on failure)",
        "restore client registrations against the promoted launcher",
        "verify the installation (CLI, MCP handshake, doctor)",
    ),
    "uninstall": (
        "detect current install state",
        "stop owned processes (if any)",
        "unload Neo-used Ollama models",
        "remove active client registrations (records retained for later reinstall)",
        "remove the managed runtime (venv/ only; durable data untouched)",
    ),
    "uninstall --delete-memory": (
        "detect current install state",
        "stop owned processes (if any)",
        "unload Neo-used Ollama models",
        "confirm full wipe (interactive prompt or --yes)",
        "remove active client registrations",
        "delete the entire validated managed root",
    ),
}


def _plan_key(operation: str, *, clean: bool = False, delete_memory: bool = False) -> str:
    if operation == "install" and clean:
        return "install --clean"
    if operation == "uninstall" and delete_memory:
        return "uninstall --delete-memory"
    return operation


# --------------------------------------------------------------------------- #
# Argument parsing
# --------------------------------------------------------------------------- #


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="setup.py",
        description=(
            "neo-localmcp setup: install, reinstall, or uninstall the managed "
            "runtime on macOS and Windows."
        ),
    )
    sub = parser.add_subparsers(dest="operation", required=True)

    install_parser = sub.add_parser(
        "install",
        help="Install or update the managed runtime. Preserves durable data by default.",
    )
    install_parser.add_argument(
        "--clean",
        action="store_true",
        help="Delete the entire managed root first, then install fresh. Destructive; requires --yes when non-interactive.",
    )
    install_parser.add_argument(
        "--yes",
        action="store_true",
        help="Non-interactively confirm a destructive operation (required for --clean without a TTY).",
    )
    install_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the detected state and the ordered action plan; make no changes.",
    )
    install_parser.add_argument(
        "--client",
        action="append",
        choices=("claude-code", "codex", "claude-desktop"),
        default=[],
        help="Register this client after a fresh/clean install. Repeat for multiple clients; omit to register none.",
    )
    install_parser.set_defaults(operation="install")

    reinstall_parser = sub.add_parser(
        "reinstall",
        help="Replace the managed runtime transactionally. Never deletes durable data.",
    )
    reinstall_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the detected state and the ordered action plan; make no changes.",
    )
    reinstall_parser.set_defaults(operation="reinstall")

    uninstall_parser = sub.add_parser(
        "uninstall",
        help="Remove the managed runtime (venv/ only, by default). Durable data is preserved unless --delete-memory is given.",
    )
    uninstall_parser.add_argument(
        "--delete-memory",
        action="store_true",
        help="Full wipe: delete the entire managed root, including all durable data. Requires --yes when non-interactive.",
    )
    uninstall_parser.add_argument(
        "--yes",
        action="store_true",
        help="Non-interactively confirm a destructive operation (required for --delete-memory without a TTY).",
    )
    uninstall_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the detected state and the ordered action plan; make no changes.",
    )
    uninstall_parser.set_defaults(operation="uninstall")

    return parser


# --------------------------------------------------------------------------- #
# Safety refusal: destructive flag requested non-interactively without --yes
# --------------------------------------------------------------------------- #


def _is_noninteractive() -> bool:
    try:
        return not sys.stdin.isatty()
    except (AttributeError, ValueError):
        return True


def _destructive_refusal(args: argparse.Namespace) -> str | None:
    """Return a refusal message if a destructive flag needs --yes but lacks it
    while running non-interactively, else None. Runs before any mutation."""

    if args.operation == "install" and getattr(args, "clean", False):
        if not args.yes and _is_noninteractive():
            return (
                "Refusing --clean non-interactively without --yes: this would delete "
                "the entire managed root. Re-run with --yes to confirm, or run "
                "interactively to be prompted."
            )
    if args.operation == "uninstall" and getattr(args, "delete_memory", False):
        if not args.yes and _is_noninteractive():
            return (
                "Refusing --delete-memory non-interactively without --yes: this would "
                "delete the entire managed root. Re-run with --yes to confirm, or run "
                "interactively to be prompted."
            )
    return None


# --------------------------------------------------------------------------- #
# Dry-run rendering (presentation only; calls detect_state, nothing else)
# --------------------------------------------------------------------------- #


def _render_dry_run(paths: ManagedPaths, args: argparse.Namespace, reporter: Reporter) -> None:
    state = detect_state(paths)
    reporter.info(f"DRY RUN: no changes will be made for '{args.operation}'.")
    reporter.info(f"Managed root: {paths.root}")
    reporter.info(f"Detected state: {state.kind.value}")
    if args.operation == "install":
        selected = ", ".join(args.client) if args.client else "none"
        reporter.info(f"Selected clients: {selected}")
    for key, value in sorted(state.details.items()):
        reporter.info(f"  state.{key}: {value}")

    key = _plan_key(
        args.operation,
        clean=getattr(args, "clean", False),
        delete_memory=getattr(args, "delete_memory", False),
    )
    plan = _DRY_RUN_PLANS[key]
    reporter.info(f"Ordered action plan for '{key}':")
    for index, step in enumerate(plan, start=1):
        reporter.info(f"  {index}. {step}")


# --------------------------------------------------------------------------- #
# Context construction + dispatch
# --------------------------------------------------------------------------- #


def _source_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _source_version() -> str:
    import neo_localmcp

    return neo_localmcp.__version__


def build_context(reporter: Reporter | None = None) -> OperationContext:
    """Construct a real OperationContext from real sources.

    ``source_root`` is this checkout (the directory containing setup.py),
    ``python_executable`` is the interpreter currently running this script, and
    ``confirm`` is the real interactive/--yes confirmation gate.
    """

    return OperationContext(
        paths=ManagedPaths.from_environment(),
        source_root=_source_root(),
        python_executable=Path(sys.executable),
        reporter=reporter or Reporter(),
        source_version=_source_version(),
        process_provider=None,
        clock=time.time,
        confirm=confirm_full_wipe,
    )


_STATUS_EXIT_CODES = {
    OperationStatus.SUCCEEDED: EXIT_SUCCESS,
    OperationStatus.CANCELLED: EXIT_SUCCESS,
    OperationStatus.FAILED: EXIT_FAILURE,
}


def _render_result(result: OperationResult, reporter: Reporter) -> int:
    if result.status is OperationStatus.CANCELLED:
        reporter.info(f"{result.operation.value} cancelled.")
    return _STATUS_EXIT_CODES[result.status]


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    reporter = Reporter()

    refusal = _destructive_refusal(args)
    if refusal is not None:
        reporter.error(refusal)
        return EXIT_USAGE_OR_REFUSAL

    context = build_context(reporter)
    if args.operation == "install":
        context.selected_clients = list(args.client)

    if getattr(args, "dry_run", False):
        _render_dry_run(context.paths, args, reporter)
        return EXIT_SUCCESS

    reporter.info(operation_explanation(Operation(args.operation)))

    if args.operation == "install":
        result = install(context, clean=args.clean, assume_yes=args.yes)
    elif args.operation == "reinstall":
        result = reinstall(context)
    elif args.operation == "uninstall":
        result = uninstall(context, delete_memory=args.delete_memory, assume_yes=args.yes)
    else:  # pragma: no cover - argparse restricts choices
        parser.error(f"Unknown operation: {args.operation}")
        return EXIT_USAGE_OR_REFUSAL

    return _render_result(result, reporter)


if __name__ == "__main__":
    raise SystemExit(main())
