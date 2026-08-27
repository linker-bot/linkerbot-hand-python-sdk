"""L6 opcode and semantic mappings."""

from ...capabilities.types import CommandSemantic, TelemetrySemantic

COMMAND_OPCODES = {
    CommandSemantic.ANGLES: 0x01,
    CommandSemantic.CURRENT_LIMITS: 0x02,
    CommandSemantic.SPEEDS: 0x05,
}

TELEMETRY_OPCODES = {
    TelemetrySemantic.ANGLES: 0x01,
    TelemetrySemantic.CURRENT_LIMITS: 0x02,
    TelemetrySemantic.CONFIGURED_SPEEDS: 0x05,
    TelemetrySemantic.TEMPERATURES: 0x33,
    TelemetrySemantic.FAULTS: 0x35,
    TelemetrySemantic.CURRENTS: 0x36,
}

BASELINE_SEMANTICS = (
    TelemetrySemantic.ANGLES,
    TelemetrySemantic.FAULTS,
    TelemetrySemantic.CURRENT_LIMITS,
)

CONFIG_TELEMETRY = (
    TelemetrySemantic.CONFIGURED_SPEEDS,
    TelemetrySemantic.CURRENT_LIMITS,
)

CLEAR_FAULTS_PAYLOAD = b"\x83\x01\x01\x01\x01\x01\x01"

__all__ = [
    "BASELINE_SEMANTICS",
    "CLEAR_FAULTS_PAYLOAD",
    "COMMAND_OPCODES",
    "CONFIG_TELEMETRY",
    "TELEMETRY_OPCODES",
]
