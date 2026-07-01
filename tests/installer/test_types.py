from __future__ import annotations

import dataclasses

import pytest

from neo_localmcp.installer.types import (
    DetectedState,
    InstallStateKind,
    Operation,
    OperationResult,
    OperationStatus,
)


def test_lifecycle_enum_values_are_stable() -> None:
    assert [operation.value for operation in Operation] == [
        "install",
        "reinstall",
        "uninstall",
    ]
    assert [state.value for state in InstallStateKind] == [
        "absent",
        "data-only",
        "healthy",
        "broken-runtime",
        "legacy-layout",
        "partial-operation",
    ]
    assert [status.value for status in OperationStatus] == [
        "succeeded",
        "failed",
        "cancelled",
    ]


def test_operation_result_is_immutable() -> None:
    result = OperationResult(
        operation=Operation.INSTALL,
        status=OperationStatus.SUCCEEDED,
        actions=("created runtime",),
        warnings=(),
    )

    with pytest.raises(dataclasses.FrozenInstanceError):
        result.status = OperationStatus.FAILED  # type: ignore[misc]


def test_detected_state_copies_details_into_immutable_mapping() -> None:
    original = {"reason": "venv_missing"}
    state = DetectedState(kind=InstallStateKind.DATA_ONLY, details=original)

    original["reason"] = "changed"

    assert state.details == {"reason": "venv_missing"}
    with pytest.raises(TypeError):
        state.details["reason"] = "changed"  # type: ignore[index]
