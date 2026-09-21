"""L20Lite multi-frame opcode layouts."""

from ...api.readings import L20LiteFaultStatus
from ...capabilities.types import CommandSemantic, TelemetrySemantic
from ..common.classic_layout import ClassicFrameLayout, ClassicObjectLayout


def decode_fault_statuses(raw: tuple[int, ...]) -> tuple[L20LiteFaultStatus, ...]:
    """Decode LHT10 bits without applying another model's fault layout."""
    return tuple(L20LiteFaultStatus(value) for value in raw)


def _layout(*frames: tuple[int, tuple[int | None, ...]]) -> ClassicObjectLayout:
    return ClassicObjectLayout(
        channel_count=10,
        frames=tuple(ClassicFrameLayout(opcode, slots) for opcode, slots in frames),
    )


ANGLE_LAYOUT = _layout(
    (0x01, (0, 1, 2, 3, 4, 5)),
    (0x04, (6, 7, 8, 9)),
)
CURRENT_LAYOUT = _layout(
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

# LHT10 fingertip quantities are five sensor values, not ten joint channels.
# Only the category queries are used; per-finger 0x28..0x32 are alternatives.
FORCE_SCALAR_LAYOUTS = {
    semantic: ClassicObjectLayout(
        channel_count=5,
        frames=(ClassicFrameLayout(opcode, (0, 1, 2, 3, 4)),),
    )
    for semantic, opcode in (
        (TelemetrySemantic.FORCE_FNS, 0x20),
        (TelemetrySemantic.FORCE_FTS, 0x21),
        (TelemetrySemantic.FORCE_FDS, 0x22),
        (TelemetrySemantic.FORCE_PX, 0x23),
    )
}

COMMAND_LAYOUTS = {
    CommandSemantic.ANGLES: ANGLE_LAYOUT,
    CommandSemantic.SPEEDS: SPEED_LAYOUT,
    CommandSemantic.CURRENT_LIMITS: CURRENT_LAYOUT,
}
STALL_COMMANDS = {
    CommandSemantic.STALL_THRESHOLDS: (0xC5, TelemetrySemantic.STALL_THRESHOLDS),
    CommandSemantic.STALL_DETECTION_TIMES: (
        0xC6,
        TelemetrySemantic.STALL_DETECTION_TIMES,
    ),
    CommandSemantic.STALL_HOLD_CURRENTS: (0xC7, TelemetrySemantic.STALL_HOLD_CURRENTS),
}
TELEMETRY_LAYOUTS = {
    **FORCE_SCALAR_LAYOUTS,
    TelemetrySemantic.ANGLES: ANGLE_LAYOUT,
    TelemetrySemantic.MEASURED_SPEEDS: SPEED_LAYOUT,
    TelemetrySemantic.CURRENTS: CURRENT_LAYOUT,
    TelemetrySemantic.TEMPERATURES: TEMPERATURE_LAYOUT,
    TelemetrySemantic.FAULTS: FAULT_LAYOUT,
}
BASELINE_SEMANTICS = (
    TelemetrySemantic.ANGLES,
    TelemetrySemantic.FAULTS,
    TelemetrySemantic.CURRENTS,
)
# Empty on purpose. The protocol states that a bare 0x05/0x06 or 0x02/0x03 query
# returns real-time speed and current; only a write is
# echoed back. Listing either here would label an actual measurement as
# DEVICE_CONFIG_RESPONSE, i.e. "the value you just wrote".
CONFIG_TELEMETRY: tuple[TelemetrySemantic, ...] = ()

__all__ = [
    "BASELINE_SEMANTICS",
    "COMMAND_LAYOUTS",
    "CONFIG_TELEMETRY",
    "FORCE_SCALAR_LAYOUTS",
    "TELEMETRY_LAYOUTS",
]
