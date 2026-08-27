"""L20Lite multi-frame opcode layouts."""

from ...capabilities.types import CommandSemantic, TelemetrySemantic
from ..common.classic_layout import ClassicFrameLayout, ClassicObjectLayout


def _layout(*frames: tuple[int, tuple[int | None, ...]]) -> ClassicObjectLayout:
    return ClassicObjectLayout(
        channel_count=10,
        frames=tuple(ClassicFrameLayout(opcode, slots) for opcode, slots in frames),
    )


ANGLE_LAYOUT = _layout(
    (0x01, (0, 1, 2, 3, 4, 5)),
    (0x04, (6, 7, 8, 9)),
)
EFFORT_LAYOUT = _layout(
    (0x02, (0, 1, 2, 3, 4)),
    (0x03, (5, 6, 7, 8, 9)),
)
SPEED_LAYOUT = _layout(
    (0x05, (0, 1, 2, 3, 4)),
    (0x06, (5, 6, 7, 8, 9)),
)
TEMPERATURE_LAYOUT = _layout(
    (0x33, (0, 1, 2, 3, 4)),
    (0x34, (5, 6, 7, 8, 9)),
)
FAULT_LAYOUT = _layout(
    (0x35, (0, 1, 2, 3, 4)),
    (0x36, (5, 6, 7, 8, 9)),
)

COMMAND_LAYOUTS = {
    CommandSemantic.ANGLES: ANGLE_LAYOUT,
    CommandSemantic.SPEEDS: SPEED_LAYOUT,
    CommandSemantic.EFFORT_LIMITS: EFFORT_LAYOUT,
}
TELEMETRY_LAYOUTS = {
    TelemetrySemantic.ANGLES: ANGLE_LAYOUT,
    TelemetrySemantic.CONFIGURED_SPEEDS: SPEED_LAYOUT,
    TelemetrySemantic.EFFORT_LIMITS: EFFORT_LAYOUT,
    TelemetrySemantic.TEMPERATURES: TEMPERATURE_LAYOUT,
    TelemetrySemantic.FAULTS: FAULT_LAYOUT,
}
BASELINE_SEMANTICS = (
    TelemetrySemantic.ANGLES,
    TelemetrySemantic.FAULTS,
    TelemetrySemantic.EFFORT_LIMITS,
)
CONFIG_TELEMETRY = (
    TelemetrySemantic.CONFIGURED_SPEEDS,
    TelemetrySemantic.EFFORT_LIMITS,
)

__all__ = [
    "BASELINE_SEMANTICS",
    "COMMAND_LAYOUTS",
    "CONFIG_TELEMETRY",
    "TELEMETRY_LAYOUTS",
]
