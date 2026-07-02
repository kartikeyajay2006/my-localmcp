from __future__ import annotations

from pathlib import Path

import pytest

from neo_localmcp.installer.output import (
    FULL_WIPE_CONFIRMATION,
    PRESERVED_MEMORY_MESSAGE,
    Reporter,
    confirm_full_wipe,
    operation_explanation,
)
from neo_localmcp.installer.paths import ManagedPaths, UnsafeManagedRoot
from neo_localmcp.installer.types import Operation


def _paths(tmp_path: Path) -> ManagedPaths:
    return ManagedPaths(
        root=tmp_path / ".neo-localmcp",
        platform="posix",
        home=tmp_path,
    )


def test_operation_explanations_use_exact_lifecycle_terminology() -> None:
    assert operation_explanation(Operation.UNINSTALL) == (
        "Uninstall removes the managed runtime. It does not recreate it."
    )
    assert operation_explanation(Operation.REINSTALL) == (
        "Reinstall replaces the managed runtime and preserves memory/data."
    )
    assert operation_explanation(Operation.INSTALL) == (
        "Install creates or updates the managed runtime and reuses preserved memory/data."
    )


def test_reporter_emits_exact_preserved_memory_message() -> None:
    lines: list[str] = []
    reporter = Reporter(output_fn=lines.append)

    event = reporter.existing_memory_detected()

    assert PRESERVED_MEMORY_MESSAGE == (
        "Existing neo-localmcp memory detected. Reusing preserved memory/data."
    )
    assert event.message == PRESERVED_MEMORY_MESSAGE
    assert lines == [f"INFO: {PRESERVED_MEMORY_MESSAGE}"]


def test_reporter_keeps_structured_events_and_deterministic_summary() -> None:
    lines: list[str] = []
    reporter = Reporter(output_fn=lines.append)

    reporter.info("inspected state")
    reporter.warn("model unload timed out")
    reporter.error("runtime verification failed")
    reporter.action("preserved durable data")
    summary = reporter.summary(
        "Install result",
        {"version": "1.0.10", "runtime": "created"},
    )

    assert [event.level for event in reporter.events] == [
        "info",
        "warning",
        "error",
        "action",
        "summary",
    ]
    assert summary.details == {"runtime": "created", "version": "1.0.10"}
    assert lines == [
        "INFO: inspected state",
        "WARNING: model unload timed out",
        "ERROR: runtime verification failed",
        "ACTION: preserved durable data",
        "SUMMARY: Install result",
        "  runtime: created",
        "  version: 1.0.10",
    ]
    with pytest.raises(TypeError):
        summary.details["runtime"] = "changed"  # type: ignore[index]


def test_full_wipe_prompt_lists_root_and_every_data_category(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    lines: list[str] = []
    prompts: list[str] = []

    def answer(prompt: str) -> str:
        prompts.append(prompt)
        return FULL_WIPE_CONFIRMATION

    confirmed = confirm_full_wipe(paths, input_fn=answer, output_fn=lines.append)

    output = "\n".join(lines)
    assert confirmed is True
    assert str(paths.root.resolve()) in output
    for name in ("venv", "memory", "sqlite", "config", "clients", "logs", "cache"):
        assert f"- {name}:" in output
        assert str(getattr(paths, name)) in output
    assert prompts == [f"Type {FULL_WIPE_CONFIRMATION} to confirm: "]


def test_full_wipe_rejects_any_other_interactive_answer(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    lines: list[str] = []

    confirmed = confirm_full_wipe(
        paths,
        input_fn=lambda _prompt: "DELETE",
        output_fn=lines.append,
    )

    assert confirmed is False
    assert lines[-1] == "CANCELLED: Full wipe was not confirmed. No data was deleted."


def test_assume_yes_bypasses_input_but_still_prints_scope(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    lines: list[str] = []

    def unexpected_input(_prompt: str) -> str:
        raise AssertionError("--yes must bypass interactive input")

    confirmed = confirm_full_wipe(
        paths,
        input_fn=unexpected_input,
        output_fn=lines.append,
        assume_yes=True,
    )

    assert confirmed is True
    assert lines[-1] == "CONFIRMED: Full wipe authorized by --yes."
    assert any(str(paths.root.resolve()) in line for line in lines)


def test_assume_yes_never_bypasses_destructive_root_validation(tmp_path: Path) -> None:
    paths = ManagedPaths(
        root=Path("/"),
        platform="posix",
        home=tmp_path,
        allow_test_root=True,
    )

    with pytest.raises(UnsafeManagedRoot):
        confirm_full_wipe(
            paths,
            input_fn=lambda _prompt: FULL_WIPE_CONFIRMATION,
            output_fn=lambda _line: None,
            assume_yes=True,
        )
