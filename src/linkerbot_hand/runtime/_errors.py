"""Single error-normalization boundary from transport to Hand API errors."""

from __future__ import annotations

import builtins

from linkerbot_transport import errors as transport_errors

from ..api import errors as hand_errors


def as_hand_error(
    error: BaseException,
    *,
    operation: str,
    model: str,
    generation: int,
) -> hand_errors.HandError:
    """Classify an internal/transport failure once and retain its cause."""

    if isinstance(error, hand_errors.HandError):
        return error
    context = {
        "operation": operation,
        "model": model,
        "generation": generation,
        "cause": error,
    }
    if isinstance(error, transport_errors.QueueFullError):
        return hand_errors.QueueFullError(str(error), **context)
    if isinstance(error, transport_errors.DeviceFaultError):
        return hand_errors.DeviceFaultError(str(error), **context)
    if isinstance(error, transport_errors.ClosedError):
        return hand_errors.DeviceNotReadyError(str(error), **context)
    if isinstance(error, transport_errors.DeviceNotReadyError):
        return hand_errors.DeviceNotReadyError(str(error), **context)
    if isinstance(error, (transport_errors.TimeoutError, builtins.TimeoutError)):
        return hand_errors.TimeoutError(str(error), **context)
    if isinstance(error, transport_errors.LinkerbotError):
        return hand_errors.TransportError(str(error), **context)
    return hand_errors.ProtocolError(str(error), **context)


__all__ = ["as_hand_error"]
