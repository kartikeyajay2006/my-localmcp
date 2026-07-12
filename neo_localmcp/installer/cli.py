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

from .clients import apply_client_selection  # noqa: E402
from .ollama import configure_models  # noqa: E402
from .operations import OperationContext, install, reinstall, uninstall  # noqa: E402
from .output import Reporter, confirm_full_wipe, operation_explanation  # noqa: E402
from .path import add_to_path, path_hint  # noqa: E402
from .paths import ManagedPaths  # noqa: E402
from .state import detect_state  # noqa: E402
from .types import Operation, OperationResult, OperationStatus  # noqa: E402

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
    # operation + destructive flag -> the matching _DRY_RUN_PLANS key
    if operation == "install" and clean:
        return "install --clean"
    if operation == "uninstall" and delete_memory:
        return "uninstall --delete-memory"
    return operation


def dry_run_plan(operation: str, *, clean: bool = False, delete_memory: bool = False) -> tuple[str, tuple[str, ...]]:
    # stable public entry point over _plan_key/_DRY_RUN_PLANS -- both this module's _render_dry_run and the wizard's dry-run path call this instead of touching the private tables directly
    key = _plan_key(operation, clean=clean, delete_memory=delete_memory)
    return key, _DRY_RUN_PLANS[key]


# --------------------------------------------------------------------------- #
# Argument parsing
# --------------------------------------------------------------------------- #


def build_parser() -> argparse.ArgumentParser:
    # one subparser per lifecycle op: install / reinstall / uninstall / config-ollama / manage-clients.
    # per-flag help strings carry the details; args.operation is dispatched via if-chains below.
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
    install_parser.add_argument(
        "--add-to-path",
        action="store_true",
        help="Add the managed CLI directory to the current user's PATH after success.",
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
    reinstall_parser.add_argument(
        "--add-to-path",
        action="store_true",
        help="Add the managed CLI directory to the current user's PATH after success.",
    )
    reinstall_parser.add_argument(
        "--client", action="append", choices=("claude-code", "codex", "claude-desktop"),
        default=[], help="Client to keep connected after reinstall. Repeatable; omit to keep the current set.",
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
    uninstall_parser.add_argument(
        "--client", action="append", choices=("claude-code", "codex", "claude-desktop"),
        default=[], help="Detach only these clients and retain the runtime and durable data.",
    )
    uninstall_parser.set_defaults(operation="uninstall")

    config_ollama_parser = sub.add_parser(
        "config-ollama",
        help="Set the Ollama base URL and/or fast/summary/embed models. Omitted flags keep their current value.",
    )
    config_ollama_parser.add_argument("--base-url")
    config_ollama_parser.add_argument("--fast-model")
    config_ollama_parser.add_argument("--summary-model")
    config_ollama_parser.add_argument(
        "--embed-model",
        help="Optional embedding model for the semantic layer; pass an empty string to disable it.",
    )
    config_ollama_parser.set_defaults(operation="config-ollama")

    manage_clients_parser = sub.add_parser(
        "manage-clients",
        help="Reconcile which clients are connected to the currently installed runtime.",
    )
    manage_clients_parser.add_argument(
        "--client",
        action="append",
        choices=("claude-code", "codex", "claude-desktop"),
        default=[],
        help="Client that should stay connected. Repeat for multiple; omit entirely to disconnect all.",
    )
    manage_clients_parser.set_defaults(operation="manage-clients")

    return parser


# --------------------------------------------------------------------------- #
# Safety refusal: destructive flag requested non-interactively without --yes
# --------------------------------------------------------------------------- #


def _is_noninteractive() -> bool:
    # no real TTY on stdin (or can't tell) -> treat as non-interactive, the safer default
    try:
        return not sys.stdin.isatty()
    except (AttributeError, ValueError):
        return True


def _destructive_refusal(args: argparse.Namespace) -> str | None:
    # --clean/--delete-memory + no --yes + non-interactive -> refusal message; runs before any mutation, else None
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
    # presentation only: detect_state + the static dry_run_plan steps, printed via reporter; makes no changes
    state = detect_state(paths)
    reporter.info(f"DRY RUN: no changes will be made for '{args.operation}'.")
    reporter.info(f"Managed root: {paths.root}")
    reporter.info(f"Detected state: {state.kind.value}")
    if args.operation == "install":
        selected = ", ".join(args.client) if args.client else "none"
        reporter.info(f"Selected clients: {selected}")
    for key, value in sorted(state.details.items()):
        reporter.info(f"  state.{key}: {value}")

    key, plan = dry_run_plan(
        args.operation,
        clean=getattr(args, "clean", False),
        delete_memory=getattr(args, "delete_memory", False),
    )
    reporter.info(f"Ordered action plan for '{key}':")
    for index, step in enumerate(plan, start=1):
        reporter.info(f"  {index}. {step}")


# --------------------------------------------------------------------------- #
# Context construction + dispatch
# --------------------------------------------------------------------------- #


def _source_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _source_version() -> str:
    import neo_localmcp

    return neo_localmcp.__version__


def build_context(reporter: Reporter | None = None) -> OperationContext:
    # real sources for every field: this checkout as source_root, the running interpreter, real confirm_full_wipe gate -- no fakes/seams overridden
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


def _report_path_setup(
    args: argparse.Namespace, context: OperationContext, result: OperationResult, reporter: Reporter,
) -> None:
    # successful install/reinstall -> always show the exact manual PATH command; --add-to-path persists it explicitly
    if result.status is not OperationStatus.SUCCEEDED:
        return
    reporter.info("To use neo-localmcp in a new terminal, add this to your PATH:")
    reporter.info(path_hint(context.paths))
    if not args.add_to_path:
        return
    try:
        update = add_to_path(context.paths)
    except (OSError, ValueError) as exc:
        reporter.warn(f"Could not add neo-localmcp to PATH: {exc}")
        return
    state = "added" if update.changed else "already present"
    reporter.action(f"PATH {state}: {update.target}")


def _run_config_ollama(args: argparse.Namespace, reporter: Reporter) -> int:
    # embed_model is tri-state in configure_models: None (flag omitted) keeps current, "" disables, a name enables
    ollama_cfg = configure_models(
        base_url=args.base_url, fast_model=args.fast_model, summary_model=args.summary_model,
        embed_model=args.embed_model,
    )
    reporter.action(
        f"Saved Ollama config: fast={ollama_cfg.get('fast_model')}, "
        f"summary={ollama_cfg.get('summary_model')}, "
        f"embed={ollama_cfg.get('embed_model') or 'disabled'}, base_url={ollama_cfg.get('base_url')}"
    )
    return EXIT_SUCCESS


_LEVEL_METHODS = {
    "info": Reporter.info,
    "warning": Reporter.warn,
    "error": Reporter.error,
    "action": Reporter.action,
}


def _run_manage_clients(
    args: argparse.Namespace, context: OperationContext, reporter: Reporter,
) -> int:
    outcome = apply_client_selection(
        context.paths,
        args.client,
        server_command=context.paths.server_executable,
        on_event=lambda level, message: _LEVEL_METHODS[level](reporter, message),
    )
    if not outcome.ok:
        return EXIT_FAILURE
    reporter.summary(
        "manage-clients succeeded",
        {"connected": ", ".join(outcome.connected) or "none"},
    )
    return EXIT_SUCCESS


def main(argv: list[str] | None = None) -> int:
    # parse -> destructive-flag refusal check (before any mutation) -> build context -> dry-run short-circuit -> dispatch to the matching operation
    parser = build_parser()
    args = parser.parse_args(argv)

    reporter = Reporter()

    refusal = _destructive_refusal(args)
    if refusal is not None:
        reporter.error(refusal)
        return EXIT_USAGE_OR_REFUSAL

    context = build_context(reporter)
    if args.operation in {"install", "reinstall", "uninstall"}:
        context.selected_clients = list(args.client)
        context.client_selection_explicit = args.operation == "install" or bool(args.client)

    if args.operation == "config-ollama":
        return _run_config_ollama(args, reporter)
    if args.operation == "manage-clients":
        return _run_manage_clients(args, context, reporter)

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

    if args.operation in {"install", "reinstall"}:
        _report_path_setup(args, context, result, reporter)
    return _render_result(result, reporter)


if __name__ == "__main__":
    raise SystemExit(main())
