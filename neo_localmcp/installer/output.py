"""Consistent user-facing lifecycle messages and structured progress events."""

from __future__ import annotations

from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Callable, Mapping

from .paths import ManagedPaths
from .types import Operation

PRESERVED_MEMORY_MESSAGE = (
    "Existing neo-localmcp memory detected. Reusing preserved memory/data."
)
FULL_WIPE_CONFIRMATION = "DELETE ALL NEO-LOCALMCP DATA"

_OPERATION_EXPLANATIONS = {
    Operation.INSTALL: (
        "Install creates or updates the managed runtime and reuses preserved memory/data."
    ),
    Operation.REINSTALL: (
        "Reinstall replaces the managed runtime and preserves memory/data."
    ),
    Operation.UNINSTALL: (
        "Uninstall removes the managed runtime. It does not recreate it."
    ),
}

OutputFn = Callable[[str], None]
InputFn = Callable[[str], str]


def operation_explanation(operation: Operation) -> str:
    return _OPERATION_EXPLANATIONS[operation]


@dataclass(frozen=True)
class ReportEvent:
    level: str
    message: str
    details: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "details",
            MappingProxyType(dict(sorted(self.details.items()))),
        )


class Reporter:
    # prints each message via output_fn and also records it as a ReportEvent, so tests can assert on structured output instead of parsing printed strings
    def __init__(self, output_fn: OutputFn = print):
        self._output_fn = output_fn
        self._events: list[ReportEvent] = []

    @property
    def events(self) -> tuple[ReportEvent, ...]:
        return tuple(self._events)

    def _emit(self, level: str, message: str, prefix: str) -> ReportEvent:
        event = ReportEvent(level=level, message=str(message))
        self._events.append(event)
        self._output_fn(f"{prefix}: {event.message}")
        return event

    def info(self, message: str) -> ReportEvent:
        return self._emit("info", message, "INFO")

    def warn(self, message: str) -> ReportEvent:
        return self._emit("warning", message, "WARNING")

    def error(self, message: str) -> ReportEvent:
        return self._emit("error", message, "ERROR")

    def action(self, message: str) -> ReportEvent:
        return self._emit("action", message, "ACTION")

    def existing_memory_detected(self) -> ReportEvent:
        return self.info(PRESERVED_MEMORY_MESSAGE)

    def summary(
        self,
        title: str,
        details: Mapping[str, object],
    ) -> ReportEvent:
        normalized = {
            str(key): str(value)
            for key, value in sorted(details.items(), key=lambda item: str(item[0]))
        }
        event = ReportEvent(level="summary", message=str(title), details=normalized)
        self._events.append(event)
        self._output_fn(f"SUMMARY: {event.message}")
        for key, value in event.details.items():
            self._output_fn(f"  {key}: {value}")
        return event


def confirm_full_wipe(
    paths: ManagedPaths,
    *,
    input_fn: InputFn = input,
    output_fn: OutputFn = print,
    assume_yes: bool = False,
) -> bool:
    # --yes -> auto-confirm; else requires typing the exact FULL_WIPE_CONFIRMATION phrase, any other input cancels
    resolved_root = paths.validate_destructive_root()
    output_fn("FULL WIPE REQUESTED")
    output_fn("This permanently deletes the entire managed neo-localmcp root.")
    output_fn(f"Managed root: {resolved_root}")
    output_fn("Data categories that will be deleted:")
    for name in ("venv", "memory", "sqlite", "config", "clients", "logs", "cache"):
        output_fn(f"- {name}: {getattr(paths, name)}")

    if assume_yes:
        output_fn("CONFIRMED: Full wipe authorized by --yes.")
        return True

    answer = input_fn(f"Type {FULL_WIPE_CONFIRMATION} to confirm: ")
    if answer == FULL_WIPE_CONFIRMATION:
        output_fn("CONFIRMED: Full wipe authorized interactively.")
        return True
    output_fn("CANCELLED: Full wipe was not confirmed. No data was deleted.")
    return False
