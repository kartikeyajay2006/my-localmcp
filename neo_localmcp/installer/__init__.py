"""Cross-platform installation lifecycle for neo-localmcp."""

from .types import (
    DetectedState,
    InstallStateKind,
    Operation,
    OperationResult,
    OperationStatus,
)
from .paths import ManagedPaths, UnsafeManagedRoot

__all__ = [
    "DetectedState",
    "InstallStateKind",
    "ManagedPaths",
    "Operation",
    "OperationResult",
    "OperationStatus",
    "UnsafeManagedRoot",
]
