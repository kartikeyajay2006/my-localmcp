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
from .migration import (
    MigrationAction,
    MigrationConflict,
    MigrationPlan,
    MigrationResult,
    apply_migration,
    plan_migration,
)

__all__ = [
    "DetectedState",
    "InstallStateKind",
    "ManagedPaths",
    "MigrationAction",
    "MigrationConflict",
    "MigrationPlan",
    "MigrationResult",
    "MetadataCorruptError",
    "MetadataError",
    "MetadataMissingError",
    "Operation",
    "OperationResult",
    "OperationStatus",
    "UnsafeManagedRoot",
    "begin_operation",
    "apply_migration",
    "complete_operation",
    "detect_state",
    "fail_operation",
    "plan_migration",
]
