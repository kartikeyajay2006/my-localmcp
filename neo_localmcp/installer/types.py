"""Shared immutable types for setup lifecycle modules."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from types import MappingProxyType
from typing import Any, Mapping


class Operation(str, Enum):
    INSTALL = "install"
    REINSTALL = "reinstall"
    UNINSTALL = "uninstall"


class InstallStateKind(str, Enum):
    ABSENT = "absent"
    DATA_ONLY = "data-only"
    HEALTHY = "healthy"
    BROKEN_RUNTIME = "broken-runtime"
    LEGACY_LAYOUT = "legacy-layout"
    PARTIAL_OPERATION = "partial-operation"


class OperationStatus(str, Enum):
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass(frozen=True)
class OperationResult:
    operation: Operation
    status: OperationStatus
    actions: tuple[str, ...]
    warnings: tuple[str, ...]


@dataclass(frozen=True)
class DetectedState:
    kind: InstallStateKind
    details: Mapping[str, Any]

    def __post_init__(self) -> None:
        object.__setattr__(self, "details", MappingProxyType(dict(self.details)))
