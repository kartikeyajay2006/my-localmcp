"""Cross-platform installation lifecycle for neo-localmcp."""

from .types import (
    DetectedState,
    InstallStateKind,
    Operation,
    OperationResult,
    OperationStatus,
)
from .paths import ManagedPaths, UnsafeManagedRoot
from .state import (
    MetadataCorruptError,
    MetadataError,
    MetadataMissingError,
    begin_operation,
    complete_operation,
    detect_state,
    fail_operation,
)

__all__ = [
    "DetectedState",
    "InstallStateKind",
    "ManagedPaths",
    "MetadataCorruptError",
    "MetadataError",
    "MetadataMissingError",
    "Operation",
    "OperationResult",
    "OperationStatus",
    "UnsafeManagedRoot",
    "begin_operation",
    "complete_operation",
    "detect_state",
    "fail_operation",
]
