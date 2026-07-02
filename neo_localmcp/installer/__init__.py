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
from .output import (
    FULL_WIPE_CONFIRMATION,
    PRESERVED_MEMORY_MESSAGE,
    ReportEvent,
    Reporter,
    confirm_full_wipe,
    operation_explanation,
)
from .processes import (
    OwnedProcess,
    ProcessIdentity,
    ProcessSnapshot,
    PsutilProcessProvider,
    ShutdownResult,
    discover_owned_processes,
    stop_owned_processes,
)

__all__ = [
    "DetectedState",
    "FULL_WIPE_CONFIRMATION",
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
    "OwnedProcess",
    "PRESERVED_MEMORY_MESSAGE",
    "ProcessIdentity",
    "ProcessSnapshot",
    "PsutilProcessProvider",
    "ReportEvent",
    "Reporter",
    "ShutdownResult",
    "UnsafeManagedRoot",
    "begin_operation",
    "apply_migration",
    "complete_operation",
    "confirm_full_wipe",
    "detect_state",
    "discover_owned_processes",
    "fail_operation",
    "plan_migration",
    "operation_explanation",
    "stop_owned_processes",
]
