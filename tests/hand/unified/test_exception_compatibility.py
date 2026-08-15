from __future__ import annotations

import pytest

import linkerbot_hand
from linkerbot import exceptions as legacy_errors


@pytest.mark.parametrize(
    "name",
    [
        "ClosedError",
        "DeviceFaultError",
        "DeviceNotReadyError",
        "QueueFullError",
        "TimeoutError",
        "TransportError",
        "UnsupportedOperationError",
        "ValidationError",
    ],
)
def test_legacy_and_split_packages_export_the_same_error_classes(name: str) -> None:
    assert getattr(legacy_errors, name) is getattr(linkerbot_hand, name)


def test_legacy_base_error_aliases_are_the_unified_types() -> None:
    assert legacy_errors.LinkerbotError is linkerbot_hand.HandSDKError
    assert legacy_errors.CANError is linkerbot_hand.TransportError
    assert legacy_errors.StateError is linkerbot_hand.DeviceStateError
