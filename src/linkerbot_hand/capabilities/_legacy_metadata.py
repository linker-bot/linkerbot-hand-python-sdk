"""Pre-1.0 physical metadata retained only for the temporary API adapter."""

from __future__ import annotations

from ..contracts import NumericRange as LegacyNumericRange
from ..contracts import SensorType, SensorValueKind
from .ranges import NumericRange

_L25_POSITION_MAXIMA = (
    80,
    90,
    47.9,
    155.7,
    30,
    75,
    178.8,
    30,
    75,
    75,
    30,
    75,
    75,
    30,
    75,
    75,
)
_L25_CURRENT_RANGES = (
    (400, 700),
    (400, 700),
    (0, 1024),
    (0, 1024),
) + ((450, 800),) * 12
_L25_SPEED_RANGES: tuple[tuple[float, float, str] | None, ...] = (
    (0, 180, "rpm"),
    (0, 180, "rpm"),
    (0, 30000, "rpm"),
    (0, 30000, "rpm"),
    (0.02, 40, "millimeter_per_second"),
    None,
    (0.02, 40, "millimeter_per_second"),
    (0.02, 40, "millimeter_per_second"),
    None,
    (0.02, 40, "millimeter_per_second"),
    (0.02, 40, "millimeter_per_second"),
    None,
    (0.02, 40, "millimeter_per_second"),
    (0.02, 40, "millimeter_per_second"),
    None,
    (0.02, 40, "millimeter_per_second"),
)


def joint_range(
    model: str,
    index: int,
    source: NumericRange,
    kind: str,
) -> LegacyNumericRange:
    """Reproduce one old joint range while the old façade remains testable."""

    unit = "count"
    physical_unit = None
    physical_minimum = None
    physical_maximum = None
    scale = None
    offset = None
    if kind == "angle" and model == "L25":
        physical_unit = "degree"
        physical_minimum = 0
        physical_maximum = _L25_POSITION_MAXIMA[index]
    elif kind == "angle" and model == "O20":
        physical_unit = "degree"
    elif kind == "speed" and model == "O6":
        physical_unit = "rpm"
        physical_minimum = 0
        physical_maximum = 180
    elif kind == "speed" and model == "L25":
        metadata = _L25_SPEED_RANGES[index]
        if metadata is not None:
            physical_minimum, physical_maximum, physical_unit = metadata
    elif kind == "speed" and model == "L30":
        physical_unit = "rpm"
        physical_minimum = 0.732
        physical_maximum = 183
        scale = 0.732
        offset = 0
    elif kind == "limit" and model == "L6":
        physical_unit = "mA"
        physical_minimum = 0
        physical_maximum = 1400
        scale = 1400 / 255
        offset = 0
    elif kind == "limit" and model == "O6":
        physical_unit = "mA"
        physical_minimum = 0
        physical_maximum = 1000
    elif kind == "limit" and model == "L25":
        physical_unit = "mA"
        physical_minimum, physical_maximum = _L25_CURRENT_RANGES[index]
    elif kind == "limit" and model == "L30":
        physical_unit = "mA"
        physical_minimum = 390
        physical_maximum = 5200
        scale = 6.5
        offset = 0
    elif kind == "limit" and model == "O20":
        physical_unit = "mA"
        physical_minimum = 0
        physical_maximum = 6500
        scale = 6.5
        offset = 0
    return LegacyNumericRange(
        source.minimum,
        source.maximum,
        unit,
        integer_only=source.integer_only,
        step=source.step,
        physical_unit=physical_unit,
        physical_minimum=physical_minimum,
        physical_maximum=physical_maximum,
        scale=scale,
        offset=offset,
    )


def sensor_metadata(
    model: str,
    sensor: SensorType,
    unit: str,
    encoding: str,
) -> tuple[str, str, str | None, float | None, float | None, SensorValueKind]:
    """Return the exact metadata formerly exposed by ``SensorCapability``."""

    physical_unit = None
    scale = None
    offset = None
    value_kind = SensorValueKind.NUMERIC
    if unit == "dimensionless" and sensor is not SensorType.FAULTS:
        unit = "count"
    if sensor is SensorType.FORCE:
        value_kind = SensorValueKind.TACTILE_MATRIX
    elif sensor is SensorType.FAULTS:
        value_kind = SensorValueKind.ENUM if model == "O20" else SensorValueKind.BITMASK
        unit = "dimensionless"
        encoding = "u8"
    if sensor is SensorType.TEMPERATURE and model in {
        "L6",
        "O6",
        "L20Lite",
        "L25",
        "L30",
    }:
        unit = "degree_celsius"
        physical_unit = "degree_celsius"
        scale = 1
        offset = 0
    if model == "L6" and sensor in {SensorType.TORQUES, SensorType.CURRENT}:
        unit = "count"
        physical_unit = "mA"
        scale = 1400 / 255
        offset = 0
    elif model == "O6" and sensor is SensorType.SPEEDS:
        physical_unit = "rpm"
    elif model == "O6" and sensor is SensorType.ACCELERATION:
        physical_unit = "degree_per_second_squared"
    elif model == "O6" and sensor is SensorType.TORQUES:
        physical_unit = "mA"
    elif model == "L25" and sensor is SensorType.ANGLES:
        physical_unit = "degree"
    elif model == "L25" and sensor is SensorType.TORQUES:
        unit = "count"
        physical_unit = "mA"
    elif model == "L30" and sensor is SensorType.SPEEDS:
        unit = "count"
        physical_unit = "rpm"
        scale = 0.732
        offset = 0
    elif model == "L30" and sensor is SensorType.CURRENT:
        unit = "count"
        physical_unit = "mA"
        scale = 6.5
        offset = 0
    elif model == "O20" and sensor is SensorType.ANGLES:
        physical_unit = "degree"
    elif model == "O20" and sensor is SensorType.SPEEDS:
        physical_unit = "rpm"
        scale = 0.732
        offset = 0
    elif model == "O20" and sensor is SensorType.CURRENT:
        unit = "mA"
        physical_unit = "mA"
        scale = 1
        offset = 0
    elif model == "O30i" and sensor is SensorType.MOTION_TIME:
        unit = "count"
        physical_unit = "millisecond"
        scale = 10
        offset = 0
    if model == "O30i":
        encoding = "u8"
        if sensor is SensorType.FAULTS:
            unit = "dimensionless"
    return unit, encoding, physical_unit, scale, offset, value_kind


def sensor_range(
    source: NumericRange,
    unit: str,
    physical_unit: str | None,
    scale: float | None,
    offset: float | None,
    *,
    model: str,
    sensor: SensorType,
) -> LegacyNumericRange:
    """Build range metadata consistent with the old sensor view."""

    physical_minimum = None
    physical_maximum = None
    if model == "O30i" and sensor is SensorType.MOTION_TIME:
        physical_minimum = 0
        physical_maximum = 2550
    return LegacyNumericRange(
        source.minimum,
        source.maximum,
        unit,
        integer_only=source.integer_only,
        step=source.step,
        physical_unit=physical_unit,
        physical_minimum=physical_minimum,
        physical_maximum=physical_maximum,
        scale=scale,
        offset=offset,
    )


__all__ = ["joint_range", "sensor_metadata", "sensor_range"]
