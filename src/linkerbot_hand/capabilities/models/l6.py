"""Declared L6 capability profile available before device identification."""

from ...api.commands import AckSupport
from ...api.enums import (
    Confidence,
    HandSide,
    JointMotionType,
    ReadMode,
    SampleProjection,
    TransportKind,
)
from ..ranges import NumericRange
from ..types import (
    CommandSemantic,
    DeviceCapabilities,
    OperationCapabilities,
    StallFieldCapability,
    StallProtectionCapability,
    TelemetrySemantic,
)
from ._common import (
    U8_RANGE,
    angle_command,
    angle_telemetry,
    command,
    identity,
    joint,
    telemetry,
    transport,
)

_JOINT_NAMES = ("thumb_flex", "thumb_abd", "index", "middle", "ring", "pinky")
_FINGERS = ("thumb", "index", "middle", "ring", "pinky")
_NONZERO_U8_RANGE = NumericRange(1, 255, integer_only=True, step=1)
_POSITION_KP_RANGE = NumericRange(0, 200, integer_only=True, step=1)
_CONTROL_FIELDS = (
    (
        CommandSemantic.TORQUES,
        TelemetrySemantic.TORQUES,
        U8_RANGE,
        (
            "0 selects position mode per joint; 1..255 selects torque-mode "
            "output. Documented output range 0..2000 has no stated unit or "
            "conversion formula; measured physical torque is unverified. "
            "V2.4.5 queries track the output/mode setting; write replies may "
            "contain the previous value. Position-to-torque settling: 20 ms; "
            "torque-to-position: 150 ms. Independent of 0x02 current limits "
            "and 0x36 measured current"
        ),
        Confidence.UNKNOWN,
    ),
    (
        CommandSemantic.POSITION_KP,
        TelemetrySemantic.POSITION_KP,
        _POSITION_KP_RANGE,
        (
            "position mode only; actual Kp = raw * 10; default raw 50 (Kp 500). "
            "SDK writes 0..200 to respect the stated Kp range 0..2000, despite "
            "the conflicting generic byte range 0..255"
        ),
        Confidence.CONFLICTING,
    ),
    (
        CommandSemantic.POSITION_KD,
        TelemetrySemantic.POSITION_KD,
        U8_RANGE,
        (
            "position mode only; default 21; protocol recommends at most 40 "
            "(not a hard limit); no further scale or physical unit stated. "
            "Overview permits 0..255 whereas per-byte text says 1..255"
        ),
        Confidence.CONFLICTING,
    ),
    (
        CommandSemantic.TORQUE_KD,
        TelemetrySemantic.TORQUE_KD,
        U8_RANGE,
        (
            "torque mode only; default 1; no further scale or "
            "physical unit stated. Overview permits 0..255 whereas per-byte "
            "text says 1..255"
        ),
        Confidence.CONFLICTING,
    ),
)


def declared_capabilities(side: HandSide) -> DeviceCapabilities:
    """Return immutable, unverified L6 metadata for ``side``."""

    joints = tuple(
        joint(
            index,
            name,
            JointMotionType.ABDUCTION if index == 1 else JointMotionType.FLEXION,
            U8_RANGE,
            raw_at_zero=0,
            raw_at_hundred=255,
            zero_label="toward_thumb_web" if index == 1 else "flexed",
            hundred_label="away_from_thumb_web" if index == 1 else "extended",
        )
        for index, name in enumerate(_JOINT_NAMES)
    )
    stall = StallProtectionCapability(
        channels=_JOINT_NAMES,
        detection_time=StallFieldCapability(
            raw_range=U8_RANGE,
            raw_encoding="u8",
            raw_unit="count",
            raw_scale_note=(
                "10 ms/count; writing raw 0 is documented as having no effect"
            ),
            unit_confidence=Confidence.DOCUMENTED,
        ),
        threshold=StallFieldCapability(
            raw_range=U8_RANGE,
            raw_encoding="u8",
            raw_unit="count",
            raw_scale_note=(
                "1400/255 protocol-units/count; the protocol does not state the "
                "physical unit"
            ),
            unit_confidence=Confidence.UNKNOWN,
        ),
        hold_current=StallFieldCapability(
            raw_range=U8_RANGE,
            raw_encoding="u8",
            raw_unit="count",
            raw_scale_note=(
                "1400/255 protocol-units/count; the protocol labels this field "
                "stall torque but gives no physical unit; exposed as hold "
                "current, with physical current scale unverified"
            ),
            unit_confidence=Confidence.CONFLICTING,
        ),
        physical_semantics_confidence=Confidence.CONFLICTING,
        safety_range_verified=False,
        read_supported=True,
        write_supported=True,
    )
    assert stall.hold_current is not None
    return DeviceCapabilities(
        identity=identity(
            "L6",
            side,
            supported_fields=(
                "serial_number",
                "pcb_version",
                "firmware_version",
                "mechanical_version",
                "product_code",
                "sensor_variant",
                "side",
            ),
        ),
        joints=joints,
        commands=(
            angle_command(joints, "u8", returns_current_state=True),
            *(
                command(
                    semantic,
                    _JOINT_NAMES,
                    raw_range,
                    "u8",
                    raw_unit="count",
                    raw_scale_note=note,
                    unit_confidence=confidence,
                    ack_support=AckSupport.SUPPORTED,
                )
                for semantic, _, raw_range, note, confidence in _CONTROL_FIELDS
            ),
            command(
                CommandSemantic.SPEEDS,
                _JOINT_NAMES,
                U8_RANGE,
                "u8",
                raw_unit="count",
                raw_scale_note=(
                    "step-mode speed configuration; larger values command faster "
                    "motion; not measured RPM"
                ),
                unit_confidence=Confidence.DOCUMENTED,
                ack_support=AckSupport.SUPPORTED,
            ),
            command(
                CommandSemantic.CURRENT_LIMITS,
                _JOINT_NAMES,
                U8_RANGE,
                "u8",
                raw_unit="count",
                raw_scale_note="1400/255 mA/count; maximum motor-current setting",
                unit_confidence=Confidence.DOCUMENTED,
                ack_support=AckSupport.SUPPORTED,
            ),
            command(
                CommandSemantic.STALL_DETECTION_TIMES,
                _JOINT_NAMES,
                _NONZERO_U8_RANGE,
                "u8",
                raw_unit="count",
                raw_scale_note=(
                    "10 ms/count; writing raw 0 is documented as having no effect"
                ),
                unit_confidence=Confidence.DOCUMENTED,
                ack_support=AckSupport.SUPPORTED,
            ),
            command(
                CommandSemantic.STALL_THRESHOLDS,
                _JOINT_NAMES,
                U8_RANGE,
                "u8",
                raw_unit="count",
                raw_scale_note=(
                    "1400/255 protocol-units/count; the protocol does not state "
                    "the physical unit"
                ),
                unit_confidence=Confidence.UNKNOWN,
                ack_support=AckSupport.SUPPORTED,
            ),
            command(
                CommandSemantic.STALL_HOLD_CURRENTS,
                _JOINT_NAMES,
                U8_RANGE,
                "u8",
                raw_unit="count",
                raw_scale_note=(
                    "1400/255 protocol-units/count; the protocol labels this "
                    "field stall torque but gives no physical unit"
                ),
                unit_confidence=Confidence.CONFLICTING,
                ack_support=AckSupport.SUPPORTED,
            ),
            command(
                CommandSemantic.LIMIT_COMPENSATIONS,
                _JOINT_NAMES,
                U8_RANGE,
                "u8",
                raw_unit="count",
                raw_scale_note=(
                    "per-joint limit compensation; physical scale and direction "
                    "are not documented"
                ),
                unit_confidence=Confidence.UNKNOWN,
                ack_support=AckSupport.SUPPORTED,
            ),
        ),
        telemetry=(
            *(
                telemetry(
                    semantic,
                    _JOINT_NAMES,
                    "u8",
                    shape=(6,),
                    raw_range=raw_range,
                    raw_unit="count",
                    raw_scale_note=note,
                    unit_confidence=confidence,
                    recommended_hz=1,
                    stale_after_s=3,
                    estimated_transaction_s=0.005,
                    device_measured=False,
                )
                for _, semantic, raw_range, note, confidence in _CONTROL_FIELDS
            ),
            angle_telemetry(
                joints,
                "u8",
                recommended_hz=60,
                estimated_transaction_s=0.005,
            ),
            telemetry(
                TelemetrySemantic.CONFIGURED_SPEEDS,
                _JOINT_NAMES,
                "u8",
                shape=(6,),
                raw_range=U8_RANGE,
                raw_unit="count",
                raw_scale_note=(
                    "step-mode speed configuration; larger values command faster "
                    "motion; not measured RPM"
                ),
                unit_confidence=Confidence.DOCUMENTED,
                recommended_hz=0,
                stale_after_s=1,
                estimated_transaction_s=0,
                read_mode=ReadMode.COMMAND_RESPONSE,
                device_measured=False,
                device_queried=False,
            ),
            telemetry(
                TelemetrySemantic.CURRENT_LIMITS,
                _JOINT_NAMES,
                "u8",
                shape=(6,),
                raw_range=U8_RANGE,
                raw_unit="count",
                raw_scale_note="1400/255 mA/count; maximum motor-current setting",
                unit_confidence=Confidence.DOCUMENTED,
                recommended_hz=30,
                stale_after_s=0.1,
                estimated_transaction_s=0.005,
                read_mode=ReadMode.QUERY,
                device_measured=False,
            ),
            telemetry(
                TelemetrySemantic.FORCE,
                _FINGERS,
                "u8",
                shape=(5, 12, 6),
                raw_range=U8_RANGE,
                raw_unit="count",
                raw_scale_note="raw tactile ADC count; physical calibration unknown",
                unit_confidence=Confidence.UNKNOWN,
                recommended_hz=5,
                stale_after_s=0.5,
                estimated_transaction_s=0.055,
                payload_projection=SampleProjection.TACTILE_MATRIX,
            ),
            telemetry(
                TelemetrySemantic.TEMPERATURES,
                _JOINT_NAMES,
                "u8",
                shape=(6,),
                raw_range=U8_RANGE,
                raw_unit="degree_celsius",
                raw_scale_note="1 degree Celsius/count",
                unit_confidence=Confidence.DOCUMENTED,
                recommended_hz=1,
                stale_after_s=3,
                estimated_transaction_s=0.005,
            ),
            telemetry(
                TelemetrySemantic.CURRENTS,
                _JOINT_NAMES,
                "u8",
                shape=(6,),
                raw_range=U8_RANGE,
                raw_unit="count",
                raw_scale_note=(
                    "1400/255 mA/count; absolute current magnitude, sign unavailable"
                ),
                unit_confidence=Confidence.DOCUMENTED,
                recommended_hz=10,
                stale_after_s=0.3,
                estimated_transaction_s=0.005,
            ),
            telemetry(
                TelemetrySemantic.FAULTS,
                _JOINT_NAMES,
                "u8_bitmask",
                decoded_statuses=True,
                shape=(6,),
                raw_range=U8_RANGE,
                raw_unit=None,
                raw_scale_note=(
                    "bit6 MCU over-temperature; bit5 motor over-temperature; "
                    "bit4 overload-2; bit3 overload-1; bit2 phase-A over-current; "
                    "bit1 phase-C over-current; bit0 phase-B over-current; "
                    "bit7 reserved"
                ),
                unit_confidence=Confidence.DOCUMENTED,
                recommended_hz=5,
                stale_after_s=0.5,
                estimated_transaction_s=0.005,
            ),
            *(
                telemetry(
                    semantic,
                    _JOINT_NAMES,
                    "u8",
                    shape=(6,),
                    raw_range=U8_RANGE,
                    raw_unit=field.raw_unit,
                    raw_scale_note=field.raw_scale_note,
                    unit_confidence=field.unit_confidence,
                    recommended_hz=1,
                    stale_after_s=3,
                    estimated_transaction_s=0.005,
                    device_measured=False,
                )
                for semantic, field in (
                    (TelemetrySemantic.STALL_DETECTION_TIMES, stall.detection_time),
                    (TelemetrySemantic.STALL_THRESHOLDS, stall.threshold),
                    (TelemetrySemantic.STALL_HOLD_CURRENTS, stall.hold_current),
                )
            ),
            telemetry(
                TelemetrySemantic.LIMIT_COMPENSATIONS,
                _JOINT_NAMES,
                "u8",
                shape=(6,),
                raw_range=U8_RANGE,
                raw_unit="count",
                raw_scale_note=(
                    "per-joint limit compensation; physical scale and direction "
                    "are not documented"
                ),
                unit_confidence=Confidence.UNKNOWN,
                recommended_hz=1,
                stale_after_s=3,
                estimated_transaction_s=0.005,
                device_measured=False,
            ),
        ),
        stall_protection=stall,
        transport=transport(TransportKind.CAN),
        operations=OperationCapabilities(
            clear_faults=True,
            save_parameters=True,
            read_stall_protection=True,
            write_stall_protection=True,
            acknowledged_operations=("save_parameters",),
        ),
    )


__all__ = ["declared_capabilities"]
