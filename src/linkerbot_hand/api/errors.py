"""Stable, structured errors exposed by the Hand SDK 1.0 API."""

from __future__ import annotations

import builtins
from dataclasses import dataclass
from enum import Enum
from typing import ClassVar


class ErrorCode(str, Enum):
    """Machine-readable error categories for callers such as ROS 2."""

    VALIDATION = "validation"
    UNSUPPORTED_OPERATION = "unsupported_operation"
    UNSUPPORTED_FIRMWARE = "unsupported_firmware"
    IDENTITY_MISMATCH = "identity_mismatch"
    DEVICE_NOT_READY = "device_not_ready"
    TRANSPORT = "transport"
    PROTOCOL = "protocol"
    TIMEOUT = "timeout"
    DEVICE_FAULT = "device_fault"
    QUEUE_FULL = "queue_full"
    POLLING_PLAN = "polling_plan"
    CLOSED = "closed"
    LIFECYCLE = "lifecycle"
    INTERNAL = "internal"


class HandError(Exception):
    """Base error carrying stable device and operation context."""

    code: ClassVar[ErrorCode] = ErrorCode.INTERNAL

    def __init__(
        self,
        message: str = "",
        *,
        operation: str | None = None,
        model: str | None = None,
        generation: int | None = None,
        cause: BaseException | None = None,
    ) -> None:
        super().__init__(message)
        self.operation = operation
        self.model = model
        self.generation = generation
        self.cause = cause


class ValidationError(HandError, ValueError):
    """Raised when public input fails validation."""

    code = ErrorCode.VALIDATION


class UnsupportedOperationError(HandError):
    """Raised when the active device profile lacks an operation."""

    code = ErrorCode.UNSUPPORTED_OPERATION


class UnsupportedFirmwareError(HandError):
    """Raised when firmware lacks a requested protocol feature or profile."""

    code = ErrorCode.UNSUPPORTED_FIRMWARE


class IdentityMismatchError(HandError):
    """Raised when a connection resolves to a different physical device."""

    code = ErrorCode.IDENTITY_MISMATCH


class DeviceNotReadyError(HandError):
    """Raised when an operation requires an open, verified device."""

    code = ErrorCode.DEVICE_NOT_READY


class TransportError(HandError, OSError):
    """Raised when physical message transport fails."""

    code = ErrorCode.TRANSPORT


class ProtocolError(HandError):
    """Raised for malformed, rejected, or incomplete protocol traffic."""

    code = ErrorCode.PROTOCOL


class TimeoutError(HandError, builtins.TimeoutError):
    """Raised when a bounded SDK operation misses its deadline."""

    code = ErrorCode.TIMEOUT


class DeviceFaultError(HandError):
    """Raised when the device explicitly reports a fault."""

    code = ErrorCode.DEVICE_FAULT


class QueueFullError(HandError):
    """Raised when a bounded discrete queue cannot accept work."""

    code = ErrorCode.QUEUE_FULL


class PollingPlanError(HandError):
    """Raised when a polling plan cannot be installed for the device profile."""

    code = ErrorCode.POLLING_PLAN


class ClosedError(HandError):
    """Raised when an operation targets a closed device."""

    code = ErrorCode.CLOSED


class LifecycleError(HandError):
    """Raised when a lifecycle transition cannot complete safely."""

    code = ErrorCode.LIFECYCLE


@dataclass(frozen=True, slots=True)
class ErrorInfo:
    """Immutable, serializable error data stored in commands and health."""

    code: ErrorCode
    message: str
    error_type: str
    operation: str | None = None
    model: str | None = None
    generation: int | None = None

    @classmethod
    def from_exception(cls, error: BaseException) -> ErrorInfo:
        """Convert an exception without requiring callers to parse its text."""

        if isinstance(error, HandError):
            return cls(
                code=error.code,
                message=str(error),
                error_type=type(error).__name__,
                operation=error.operation,
                model=error.model,
                generation=error.generation,
            )
        return cls(
            code=ErrorCode.INTERNAL,
            message=str(error),
            error_type=type(error).__name__,
        )


__all__ = [
    "ClosedError",
    "DeviceFaultError",
    "DeviceNotReadyError",
    "ErrorCode",
    "ErrorInfo",
    "HandError",
    "IdentityMismatchError",
    "LifecycleError",
    "PollingPlanError",
    "ProtocolError",
    "QueueFullError",
    "TimeoutError",
    "TransportError",
    "UnsupportedFirmwareError",
    "UnsupportedOperationError",
    "ValidationError",
]
