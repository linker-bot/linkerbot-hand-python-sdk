"""Legacy driver value conversion and stable error normalization."""

from __future__ import annotations

from enum import Enum
from numbers import Integral, Real
from typing import Any, NoReturn

from ...contracts import (
    CommandKind,
    DeviceCapabilities,
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
    UnsupportedOperationError,
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


def command_values_for_legacy(
    model: str, kind: CommandKind, values: list[float | int]
) -> object:
    """Convert unified physical command values to legacy manager values.

    Legacy managers intentionally keep their historical public conventions:
    classic hands use normalized percentages, O6 exposes helper objects, and
    L30 accepts protocol current-limit integers.  The unified capability
    contract is allowed to use physical units, so this conversion is the one
    boundary where those conventions are translated.
    """
    if kind is CommandKind.SPEEDS and model == "O6":
        from linkerbot.hand.o6.speed import O6Speed

        return O6Speed.from_rpm(list(values))
    if kind is CommandKind.TORQUES:
        if model == "L6":
            from linkerbot.hand.l6.torque import L6Torque

            return L6Torque.from_milliamps(list(values))
        if model == "O6":
            from linkerbot.hand.o6.torque import O6Torque

            return O6Torque.from_milliamps(list(values))
        if model == "L30":
            # L30's so-called torque field is a current limit, 6.5 mA/LSB.
            return [round(float(value) / 6.5) for value in values]
    return values


def _raw_vector(data: Any, payload: Any, sensor: SensorType) -> tuple[int, ...]:
    """Extract exact protocol vector values from a legacy response object."""
    raw_attribute = {
        SensorType.SPEEDS: "raw_speeds",
        SensorType.TORQUES: "raw_torques",
        SensorType.ACCELERATION: "raw_accelerations",
    }.get(sensor)
    if raw_attribute is not None:
        raw_values = getattr(data, raw_attribute, None)
        if raw_values is not None:
            return tuple(int(value) for value in raw_values)
    to_raw = getattr(payload, "to_raw", None)
    if callable(to_raw):
        return tuple(int(value) for value in to_raw())
    return tuple(int(value) for value in as_tuple(payload))


def normalize_vector_sensor(
    data: Any,
    payload: Any,
    *,
    sensor: SensorType,
    model: str,
) -> tuple[tuple[SensorValue, ...], tuple[int, ...] | None]:
    """Normalize vector telemetry and retain exact protocol values."""
    if sensor not in (
        SensorType.SPEEDS,
        SensorType.TORQUES,
        SensorType.ACCELERATION,
        SensorType.CURRENT,
    ):
        values = tuple(normalize_sensor_value(value) for value in as_tuple(payload))
        return values, None

    raw_values = _raw_vector(data, payload, sensor)
    if sensor is SensorType.SPEEDS:
        if model == "O6":
            values = tuple(value * 180.0 / 255.0 for value in raw_values)
        elif model == "O20":
            values = tuple(value * 0.732 for value in raw_values)
        else:
            values = tuple(normalize_sensor_value(value) for value in as_tuple(payload))
    elif sensor is SensorType.ACCELERATION:
        if model == "O6":
            values = tuple(
                254.0 * 8.7 if value == 0 else value * 8.7 for value in raw_values
            )
        else:
            values = tuple(normalize_sensor_value(value) for value in as_tuple(payload))
    elif sensor is SensorType.TORQUES and model == "L6":
        values = tuple(value * 1400.0 / 255.0 for value in raw_values)
    elif sensor is SensorType.TORQUES and model == "O6":
        values = tuple(value * 1000.0 / 255.0 for value in raw_values)
    elif sensor is SensorType.CURRENT and model == "L30":
        values = tuple(value * 6.5 for value in raw_values)
    else:
        values = tuple(normalize_sensor_value(value) for value in as_tuple(payload))
    return tuple(normalize_sensor_value(value) for value in values), raw_values


def poll_manager_name(sensor: SensorType) -> str:
    """Return the legacy manager attribute used for a sensor channel.

    Args:
        sensor: Sensor channel to resolve.

    Returns:
        Attribute name on a legacy hand object.
    """
    return _POLL_MANAGERS[sensor]


def percent_to_raw(
    values: list[float | int], capabilities: DeviceCapabilities
) -> tuple[int, ...]:
    """Convert validated normalized targets to legacy protocol values.

    Args:
        values: One normalized target per joint.
        capabilities: Active model and side mapping metadata.

    Returns:
        Rounded raw protocol values in joint order.

    Raises:
        ValidationError: If the joint value count is incorrect.
        UnsupportedOperationError: If any joint lacks a verified mapping.
    """
    if len(values) != capabilities.joint_count:
        raise ValidationError(f"expected {capabilities.joint_count} joint values")
    result: list[int] = []
    for value, joint in zip(values, capabilities.joints, strict=True):
        mapping = joint.percentage_mapping
        if mapping is None:
            raise UnsupportedOperationError(
                "percentage angle control is not supported for this hand side"
            )
        result.append(
            round(
                mapping.raw_closed
                + float(value) / 100.0 * (mapping.raw_open - mapping.raw_closed)
            )
        )
    return tuple(result)


def raw_to_percent(
    values: tuple[int, ...], capabilities: DeviceCapabilities
) -> tuple[float | None, ...]:
    """Convert legacy raw angle readings to normalized positions.

    Args:
        values: Exact raw protocol readings in joint order.
        capabilities: Active model and side mapping metadata.

    Returns:
        Normalized values, with ``None`` only for joints without a mapping.

    Raises:
        TransportError: If the response joint count is incorrect.
    """
    if len(values) != capabilities.joint_count:
        raise TransportError(
            f"expected {capabilities.joint_count} angle values, got {len(values)}"
        )
    percentages: list[float | None] = []
    for value, joint in zip(values, capabilities.joints, strict=True):
        mapping = joint.percentage_mapping
        if mapping is None:
            percentages.append(None)
            continue
        span = mapping.raw_open - mapping.raw_closed
        percentages.append(
            None if span == 0 else 100.0 * (value - mapping.raw_closed) / span
        )
    return tuple(percentages)


def raw_angles(
    data: Any,
    payload: Any,
    *,
    model: str,
    classic_raw_fallback: bool,
) -> tuple[int, ...]:
    """Extract exact protocol angles from a legacy response.

    Args:
        data: Full legacy manager response.
        payload: Angle payload extracted from the response.
        model: Model name included in errors.
        classic_raw_fallback: Whether classic percent-like payloads may be
            converted back to unsigned-byte protocol values.

    Returns:
        Exact integer protocol values in response order.

    Raises:
        TransportError: If no raw representation exists or values are invalid.
    """
    hardware_raw = getattr(data, "raw_angles", None)
    to_raw = getattr(payload, "to_raw", None)
    if hardware_raw is not None:
        values = tuple(hardware_raw)
    elif callable(to_raw):
        values = tuple(to_raw())
    elif classic_raw_fallback:
        values = tuple(
            round(float(value) * 255.0 / 100.0) for value in as_tuple(payload)
        )
    else:
        raise TransportError(f"{model} angle response has no raw representation")
    try:
        return tuple(int(value) for value in values)
    except (TypeError, ValueError) as exc:
        raise TransportError("angle response contains non-integer raw values") from exc


def set_protocol_raw_angles(manager: Any, values: list[float | int]) -> None:
    """Send exact raw positions through a legacy angle manager.

    Args:
        manager: Legacy angle manager instance.
        values: Raw protocol targets in joint order.
    """
    setter = getattr(manager, "set_hardware_raw_angles", None)
    if not callable(setter):
        setter = manager.set_raw_angles
    setter(values)


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
    to_raw = getattr(payload, "to_raw", None)
    if callable(to_raw):
        return tuple(to_raw())
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
    "percent_to_raw",
    "poll_manager_name",
    "raise_mapped",
    "raw_angles",
    "raw_to_percent",
    "set_protocol_raw_angles",
]
