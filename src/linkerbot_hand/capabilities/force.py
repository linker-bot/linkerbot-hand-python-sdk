"""Map public tactile quantities to physical queries without model switches."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from ..api._force_names import FORCE_FIELDS
from ..api.enums import SampleProjection
from .values import TelemetrySemantic

if TYPE_CHECKING:
    from .types import DeviceCapabilities

FORCE_QUERIES = frozenset(
    (
        TelemetrySemantic.FORCE,
        TelemetrySemantic.FORCE_FNS,
        TelemetrySemantic.FORCE_FT,
        TelemetrySemantic.FORCE_FTS,
        TelemetrySemantic.FORCE_FD,
        TelemetrySemantic.FORCE_FDS,
        TelemetrySemantic.FORCE_PX,
    )
)


@dataclass(frozen=True, slots=True)
class ForceFieldCapability:
    """One lowercase field; ``supported=None`` awaits sensor descriptors.

    ``query_channel`` identifies the shared physical transaction for scheduling
    and metrics. Equal channels mean one read, even with different field rates.
    Descriptor-specific units/encodings remain in each sensor and field sample.
    """

    name: str
    supported: bool | None
    query_channel: TelemetrySemantic | None
    shared_fields: tuple[str, ...] = ()
    sensor_names: tuple[str, ...] = ()
    raw_unit: str | None = None
    raw_encoding: str | None = None
    raw_scale_note: str | None = None
    firmware_unsupported: bool = False


def force_field_capabilities(
    capabilities: DeviceCapabilities,
) -> tuple[ForceFieldCapability, ...]:
    result: dict[str, ForceFieldCapability] = {
        name: ForceFieldCapability(name, False, None) for name in FORCE_FIELDS
    }
    for telemetry in capabilities.telemetry:
        if telemetry.semantic not in FORCE_QUERIES:
            continue
        descriptor = telemetry.payload_projection is SampleProjection.TACTILE_DESCRIPTOR
        names = telemetry.force_fields
        if descriptor:
            names = FORCE_FIELDS
        elif (
            not names
            and telemetry.payload_projection is SampleProjection.TACTILE_MATRIX
        ):
            names = ("fn",)
        disabled = telemetry.semantic in capabilities.firmware_unsupported_telemetry
        shared = names
        if descriptor and capabilities.tactile_sensors:
            present = {
                field.semantic.lower()
                for sensor in capabilities.tactile_sensors
                if sensor.available
                for field in sensor.field_descriptors
            }
            shared = tuple(name for name in names if name in present)
        for name in names:
            sensors = telemetry.channels
            supported: bool | None = True
            if descriptor:
                if capabilities.tactile_sensors:
                    sensors = tuple(
                        sensor.sensor_name
                        for sensor in capabilities.tactile_sensors
                        if sensor.available
                        and any(
                            field.semantic.lower() == name
                            for field in sensor.field_descriptors
                        )
                    )
                    supported = bool(sensors)
                else:
                    supported = None
            result[name] = ForceFieldCapability(
                name=name,
                supported=False if disabled else supported,
                query_channel=telemetry.semantic,
                shared_fields=shared,
                sensor_names=sensors,
                raw_unit=telemetry.raw_unit,
                raw_encoding=telemetry.raw_encoding,
                raw_scale_note=telemetry.raw_scale_note,
                firmware_unsupported=disabled,
            )
    return tuple(result.values())
