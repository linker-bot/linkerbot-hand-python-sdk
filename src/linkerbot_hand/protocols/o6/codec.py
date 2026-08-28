"""O6 opcode and semantic mappings."""

from ...capabilities.types import CommandSemantic, TelemetrySemantic

COMMAND_OPCODES = {
    CommandSemantic.ANGLES: 0x01,
    CommandSemantic.CURRENT_LIMITS: 0x02,
    CommandSemantic.SPEEDS: 0x05,
    CommandSemantic.ACCELERATIONS: 0x87,
    CommandSemantic.STALL_THRESHOLDS: 0xC5,
    CommandSemantic.STALL_DETECTION_TIMES: 0xC6,
    CommandSemantic.STALL_HOLD_CURRENTS: 0xC7,
}

TELEMETRY_OPCODES = {
    TelemetrySemantic.ANGLES: 0x01,
    TelemetrySemantic.CURRENT_LIMITS: 0x02,
    TelemetrySemantic.CONFIGURED_SPEEDS: 0x05,
    TelemetrySemantic.TEMPERATURES: 0x33,
    TelemetrySemantic.FAULTS: 0x35,
    TelemetrySemantic.CONFIGURED_ACCELERATIONS: 0x87,
    TelemetrySemantic.STALL_THRESHOLDS: 0xC5,
    TelemetrySemantic.STALL_DETECTION_TIMES: 0xC6,
    TelemetrySemantic.STALL_HOLD_CURRENTS: 0xC7,
}

BASELINE_SEMANTICS = (
    TelemetrySemantic.ANGLES,
    TelemetrySemantic.FAULTS,
    TelemetrySemantic.CURRENT_LIMITS,
)

CONFIG_TELEMETRY = (
    TelemetrySemantic.CONFIGURED_SPEEDS,
    TelemetrySemantic.CONFIGURED_ACCELERATIONS,
    TelemetrySemantic.CURRENT_LIMITS,
    TelemetrySemantic.STALL_THRESHOLDS,
    TelemetrySemantic.STALL_DETECTION_TIMES,
    TelemetrySemantic.STALL_HOLD_CURRENTS,
)

__all__ = [
    "BASELINE_SEMANTICS",
    "COMMAND_OPCODES",
    "CONFIG_TELEMETRY",
    "TELEMETRY_OPCODES",
]
