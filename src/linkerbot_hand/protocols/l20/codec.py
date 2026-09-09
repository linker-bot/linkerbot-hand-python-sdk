"""L20 per-finger classic CAN object layouts."""

from ...capabilities.types import CommandSemantic, TelemetrySemantic
from ..common.classic_layout import ClassicFrameLayout, ClassicObjectLayout

_SLOTS = (
    (0, 1, 2, None, None, 3),
    (4, None, 5, None, None, 6),
    (7, None, 8, None, None, 9),
    (10, None, 11, None, None, 12),
    (13, None, 14, None, None, 15),
)


def _layout(first_opcode: int) -> ClassicObjectLayout:
    return ClassicObjectLayout(
        channel_count=16,
        frames=tuple(
            ClassicFrameLayout(first_opcode + index, slots)
            for index, slots in enumerate(_SLOTS)
        ),
    )


ANGLE_LAYOUT = _layout(0x41)
SPEED_LAYOUT = _layout(0x49)
EFFORT_LAYOUT = _layout(0x51)
FAULT_LAYOUT = _layout(0x59)
TEMPERATURE_LAYOUT = _layout(0x61)

COMMAND_LAYOUTS = {
    CommandSemantic.ANGLES: ANGLE_LAYOUT,
    CommandSemantic.SPEEDS: SPEED_LAYOUT,
    CommandSemantic.EFFORT_LIMITS: EFFORT_LAYOUT,
}
TELEMETRY_LAYOUTS = {
    TelemetrySemantic.ANGLES: ANGLE_LAYOUT,
    TelemetrySemantic.MEASURED_SPEEDS: SPEED_LAYOUT,
    TelemetrySemantic.EFFORT_LIMITS: EFFORT_LAYOUT,
    TelemetrySemantic.TEMPERATURES: TEMPERATURE_LAYOUT,
    TelemetrySemantic.FAULTS: FAULT_LAYOUT,
}
BASELINE_SEMANTICS = (
    TelemetrySemantic.ANGLES,
    TelemetrySemantic.FAULTS,
    TelemetrySemantic.EFFORT_LIMITS,
)
# Empty on purpose. The protocol states that position, speed, and torque writes
# carry targets while the reply carries the live value, and every speed/torque
# query row documents its response as ``current_*``. Listing either here would
# label an actual measurement as DEVICE_CONFIG_RESPONSE.
CONFIG_TELEMETRY: tuple[TelemetrySemantic, ...] = ()
CLEAR_FAULT_PAYLOADS = tuple(
    bytes((opcode, 1, 1, 1, 1, 1, 1)) for opcode in FAULT_LAYOUT.opcodes
)

__all__ = [
    "BASELINE_SEMANTICS",
    "CLEAR_FAULT_PAYLOADS",
    "COMMAND_LAYOUTS",
    "CONFIG_TELEMETRY",
    "TELEMETRY_LAYOUTS",
]
