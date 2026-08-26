"""Legacy driver value conversion and stable error normalization."""

from __future__ import annotations

from enum import Enum
from numbers import Integral, Real
from typing import Any, NoReturn

from ...contracts import (
    SensorType,
    SensorValue,
    TactileState,
)
from ...errors import (
    ClosedError,
    DeviceFaultError,
    DeviceStateError,
    HandSDKError,
    TransportError,
    ValidationError,
    normalize_error,
)

_FINGERS = ("thumb", "index", "middle", "ring", "pinky")
_POLL_MANAGERS = {
    SensorType.ANGLES: "angle",
    SensorType.SPEEDS: "speed",
    SensorType.TORQUES: "torque",
    SensorType.FORCE: "force_sensor",
    SensorType.TEMPERATURE: "temperature",
    SensorType.CURRENT: "current",
    SensorType.FAULTS: "fault",
    SensorType.VOLTAGE: "voltage",
    SensorType.ACCELERATION: "acceleration",
    SensorType.MOTION_TIME: "motion_time",
}
_VALUE_ATTRIBUTES = {
    SensorType.ANGLES: ("angles",),
    SensorType.SPEEDS: ("speeds",),
    SensorType.TORQUES: ("torques",),
    SensorType.TEMPERATURE: ("temperatures",),
    SensorType.CURRENT: ("currents",),
    SensorType.FAULTS: ("faults",),
    SensorType.VOLTAGE: ("voltages", "voltage"),
    SensorType.ACCELERATION: ("accelerations",),
    SensorType.MOTION_TIME: ("ticks", "motion_times", "motion_time", "values"),
}


def protocol_vector_values(
    payload: Any,
    *,
    sensor: SensorType,
    model: str,
) -> tuple[SensorValue, ...]:
    """Extract decoded protocol-native vector values without unit conversion."""
    values = _protocol_sequence(payload, sensor=sensor, model=model)
    return _integer_protocol_values(values, sensor=sensor, model=model)


def poll_manager_name(sensor: SensorType) -> str:
    """Return the legacy manager attribute used for a sensor channel.

    Args:
        sensor: Sensor channel to resolve.

    Returns:
        Attribute name on a legacy hand object.
    """
    return _POLL_MANAGERS[sensor]


def protocol_angle_values(
    payload: Any,
    *,
    model: str,
) -> tuple[int, ...]:
    """Extract exact protocol angles from a legacy response.

    Args:
        payload: Angle payload extracted from the response.
        model: Model name included in errors.
    Returns:
        Exact integer protocol values in response order.

    Raises:
        TransportError: If no explicit protocol sequence exists or values are invalid.
    """
    values = _protocol_sequence(payload, sensor=SensorType.ANGLES, model=model)
    return _integer_protocol_values(values, sensor=SensorType.ANGLES, model=model)


def _protocol_sequence(
    payload: Any,
    *,
    sensor: SensorType,
    model: str,
) -> tuple[Any, ...]:
    """Require an explicit decoded protocol sequence without conversion hooks."""
    if isinstance(payload, (list, tuple)):
        return tuple(payload)
    values = getattr(payload, "values", None)
    if isinstance(values, (list, tuple)):
        return tuple(values)
    raise TransportError(
        f"{model} {sensor.value} response has no explicit protocol sequence"
    )


def _integer_protocol_values(
    values: tuple[Any, ...],
    *,
    sensor: SensorType,
    model: str,
) -> tuple[int, ...]:
    result: list[int] = []
    for value in values:
        normalized = normalize_sensor_value(value)
        if not isinstance(normalized, int) or isinstance(normalized, bool):
            raise TransportError(
                f"{model} {sensor.value} response contains non-integer protocol values"
            )
        result.append(normalized)
    return tuple(result)


def normalize_tactile(data: Any) -> tuple[TactileState, ...]:
    """Convert a legacy five-finger force response to tactile contracts.

    Args:
        data: Legacy force manager response.

    Returns:
        Tactile matrices ordered thumb through pinky.

    Raises:
        TransportError: If finger data is missing, non-numeric, or malformed.
    """
    result: list[TactileState] = []
    for finger in _FINGERS:
        if not hasattr(data, finger):
            raise TransportError(f"force response is missing {finger} data")
        finger_data = getattr(data, finger)
        matrix = getattr(finger_data, "values", finger_data)
        to_list = getattr(matrix, "tolist", None)
        if callable(to_list):
            matrix = to_list()
        try:
            rows = tuple(tuple(int(value) for value in row) for row in matrix)
        except (TypeError, ValueError) as exc:
            raise TransportError(
                f"force response for {finger} is not a numeric matrix"
            ) from exc
        columns = len(rows[0]) if rows else 0
        try:
            tactile = TactileState(
                finger=finger,
                rows=len(rows),
                columns=columns,
                values=rows,
                online=(
                    bool(finger_data.online) if hasattr(finger_data, "online") else None
                ),
            )
        except ValidationError as exc:
            raise TransportError(
                f"force response for {finger} has an invalid matrix shape"
            ) from exc
        result.append(tactile)
    return tuple(result)


def extract_payload(data: Any, sensor: SensorType) -> Any:
    """Extract a sensor-specific value field from a legacy response.

    Args:
        data: Legacy manager response object.
        sensor: Sensor channel being normalized.

    Returns:
        First recognized payload attribute, or the original response.
    """
    for name in _VALUE_ATTRIBUTES[sensor]:
        if hasattr(data, name):
            return getattr(data, name)
    return data


def as_tuple(payload: Any) -> tuple[Any, ...]:
    """Normalize common legacy payload containers to a tuple.

    Args:
        payload: Scalar, sequence, or legacy value wrapper.

    Returns:
        Materialized tuple representation of the payload.
    """
    to_list = getattr(payload, "to_list", None)
    if callable(to_list):
        return tuple(to_list())
    if isinstance(payload, tuple):
        return payload
    if isinstance(payload, list):
        return tuple(payload)
    values = getattr(payload, "values", None)
    if isinstance(values, (tuple, list)):
        return tuple(values)
    return (payload,)


def normalize_sensor_value(value: Any) -> SensorValue:
    """Convert a legacy or NumPy scalar to a public sensor value.

    Args:
        value: Legacy scalar value to normalize.

    Returns:
        Supported built-in scalar or tactile value.

    Raises:
        TransportError: If the value type cannot be represented publicly.
    """
    if isinstance(value, Enum):
        value = value.value
    item = getattr(value, "item", None)
    if callable(item):
        value = item()
    if value is None or isinstance(value, (bool, str, bytes, TactileState)):
        return value
    if isinstance(value, bytearray):
        return bytes(value)
    if isinstance(value, Integral):
        return int(value)
    if isinstance(value, Real):
        return float(value)
    raise TransportError(f"unsupported sensor value type {type(value).__name__}")


def raise_mapped(error: BaseException) -> NoReturn:
    """Raise a legacy exception as a stable unified SDK error.

    Args:
        error: Exception raised by a legacy driver or transport.

    Raises:
        HandSDKError: Always; the exact subclass reflects the stable category.
    """
    from linkerbot.hand.hand_protocol_v1.protocol import HandProtocolDeviceError
    from linkerbot.hand.l30.protocol import DeviceResponseError

    mapped: HandSDKError
    if type(error) is DeviceStateError:
        mapped = ClosedError(str(error), cause=error)
    elif isinstance(error, (HandProtocolDeviceError, DeviceResponseError)):
        mapped = DeviceFaultError(str(error), cause=error)
    else:
        mapped = normalize_error(error)
    if mapped is error:
        raise error
    raise mapped from error


__all__ = [
    "as_tuple",
    "extract_payload",
    "normalize_sensor_value",
    "normalize_tactile",
    "poll_manager_name",
    "protocol_angle_values",
    "protocol_vector_values",
    "raise_mapped",
]
