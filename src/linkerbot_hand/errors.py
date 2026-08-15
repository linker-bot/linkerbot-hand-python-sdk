"""Stable hand SDK errors, re-exported from ``linkerbot-transport``."""

from linkerbot_transport.errors import (
    ClosedError,
    DeviceFaultError,
    DeviceNotReadyError,
    DeviceStateError,
    ErrorCode,
    HandSDKError,
    QueueFullError,
    TimeoutError,
    TransportError,
    UnsupportedOperationError,
    ValidationError,
    normalize_error,
)

__all__ = [
    "ClosedError",
    "DeviceFaultError",
    "DeviceNotReadyError",
    "DeviceStateError",
    "ErrorCode",
    "HandSDKError",
    "QueueFullError",
    "TimeoutError",
    "TransportError",
    "UnsupportedOperationError",
    "ValidationError",
    "normalize_error",
]
