"""LHT10 stall settings expose write echoes, not speculative read commands."""

from ...api.commands import AckSupport
from ...api.enums import Confidence, ReadMode
from ..types import (
    CommandCapability,
    CommandSemantic,
    StallFieldCapability,
    StallProtectionCapability,
    TelemetryCapability,
    TelemetrySemantic,
)
from ._common import U8_RANGE, command, telemetry


def stall_capability(names: tuple[str, ...]) -> StallProtectionCapability:
    """Describe torque output separately from other models' hold current."""
    return StallProtectionCapability(
        channels=names,
        detection_time=StallFieldCapability(
            U8_RANGE, "u8", "count", "10 ms/count", Confidence.DOCUMENTED
        ),
        threshold=StallFieldCapability(
            U8_RANGE,
            "u8",
            "count",
            "raw stall protection threshold; physical scale unverified",
            Confidence.UNKNOWN,
        ),
        hold_current=None,
        hold_torque=StallFieldCapability(
            U8_RANGE,
            "u8",
            "count",
            "raw output torque after stall; physical torque scale unverified",
            Confidence.UNKNOWN,
        ),
        physical_semantics_confidence=Confidence.UNKNOWN,
        safety_range_verified=False,
        read_supported=False,
        write_supported=True,
    )


def stall_fields(
    stall: StallProtectionCapability,
) -> tuple[tuple[str, StallFieldCapability], ...]:
    """Return the three named raw field descriptions."""
    assert stall.hold_torque is not None
    return (
        ("stall_thresholds", stall.threshold),
        ("stall_detection_times", stall.detection_time),
        ("stall_hold_torques", stall.hold_torque),
    )


def stall_commands(stall: StallProtectionCapability) -> tuple[CommandCapability, ...]:
    """Create FIFO configuration commands with exact response acknowledgement."""
    return tuple(
        command(
            CommandSemantic(name),
            stall.channels,
            field.raw_range,
            field.raw_encoding,
            raw_unit=field.raw_unit,
            raw_scale_note=field.raw_scale_note,
            unit_confidence=field.unit_confidence,
            ack_support=AckSupport.SUPPORTED,
        )
        for name, field in stall_fields(stall)
    )


def stall_telemetry(
    stall: StallProtectionCapability,
) -> tuple[TelemetryCapability, ...]:
    """Create cache-only views of complete confirmed device write echoes."""
    return tuple(
        telemetry(
            TelemetrySemantic(name),
            stall.channels,
            field.raw_encoding,
            shape=(10,),
            raw_range=field.raw_range,
            raw_unit=field.raw_unit,
            raw_scale_note=field.raw_scale_note,
            unit_confidence=field.unit_confidence,
            read_mode=ReadMode.COMMAND_RESPONSE,
            device_queried=False,
            device_measured=False,
            recommended_hz=0,
            stale_after_s=3,
            estimated_transaction_s=0,
        )
        for name, field in stall_fields(stall)
    )
