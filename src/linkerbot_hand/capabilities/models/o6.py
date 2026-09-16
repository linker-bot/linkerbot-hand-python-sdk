"""Declared O6 capability profile available before device identification."""

from ...api.commands import AckSupport
from ...api.enums import (
    Confidence,
    HandSide,
    JointMotionType,
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
_ACCELERATION_RANGE = NumericRange(0, 254, integer_only=True, step=1)


def declared_capabilities(side: HandSide) -> DeviceCapabilities:
    """Return immutable, unverified O6 metadata for ``side``."""

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
            raw_scale_note="10 ms/count",
            unit_confidence=Confidence.DOCUMENTED,
        ),
        threshold=StallFieldCapability(
            raw_range=U8_RANGE,
            raw_encoding="u8",
            raw_unit="count",
            raw_scale_note="1000/255 mA/count; lock/stall trigger threshold",
            unit_confidence=Confidence.DOCUMENTED,
        ),
        hold_current=StallFieldCapability(
            raw_range=U8_RANGE,
            raw_encoding="u8",
            raw_unit="count",
            raw_scale_note=(
                "1000/255 mA/count according to the formula; the protocol calls "
                "this field lock/stall torque, so its physical semantics conflict"
            ),
            unit_confidence=Confidence.CONFLICTING,
        ),
        physical_semantics_confidence=Confidence.CONFLICTING,
        safety_range_verified=False,
        read_supported=True,
        write_supported=True,
    )
    return DeviceCapabilities(
        identity=identity(
            "O6",
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
            command(
                CommandSemantic.SPEEDS,
                _JOINT_NAMES,
                U8_RANGE,
                "u8",
                raw_unit="count",
                raw_scale_note=(
                    "the title states 0.732 rpm/count, while the formula states "
                    "raw * 180 / 255 rpm; the physical scale is unresolved"
                ),
                unit_confidence=Confidence.CONFLICTING,
                ack_support=AckSupport.SUPPORTED,
            ),
            command(
                CommandSemantic.ACCELERATIONS,
                _JOINT_NAMES,
                _ACCELERATION_RANGE,
                "u8",
                raw_unit="count",
                raw_scale_note=(
                    "the title states 8.7 degree_per_second_squared/count, while "
                    "the formula states raw * 254 / 255; raw 0 is a special "
                    "maximum-acceleration encoding and raw 1..254 increases"
                ),
                unit_confidence=Confidence.CONFLICTING,
                ack_support=AckSupport.SUPPORTED,
            ),
            command(
                CommandSemantic.CURRENT_LIMITS,
                _JOINT_NAMES,
                U8_RANGE,
                "u8",
                raw_unit="count",
                raw_scale_note=(
                    "the title states 6.5 mA/count, while the formula states "
                    "raw * 1000 / 255 mA; this is a current-limit configuration, "
                    "not measured torque"
                ),
                unit_confidence=Confidence.CONFLICTING,
                ack_support=AckSupport.SUPPORTED,
            ),
            command(
                CommandSemantic.STALL_DETECTION_TIMES,
                _JOINT_NAMES,
                U8_RANGE,
                "u8",
                raw_unit=stall.detection_time.raw_unit,
                raw_scale_note=stall.detection_time.raw_scale_note,
                unit_confidence=stall.detection_time.unit_confidence,
                ack_support=AckSupport.SUPPORTED,
            ),
            command(
                CommandSemantic.STALL_THRESHOLDS,
                _JOINT_NAMES,
                U8_RANGE,
                "u8",
                raw_unit=stall.threshold.raw_unit,
                raw_scale_note=stall.threshold.raw_scale_note,
                unit_confidence=stall.threshold.unit_confidence,
                ack_support=AckSupport.SUPPORTED,
            ),
            command(
                CommandSemantic.STALL_HOLD_CURRENTS,
                _JOINT_NAMES,
                U8_RANGE,
                "u8",
                raw_unit=stall.hold_current.raw_unit,
                raw_scale_note=stall.hold_current.raw_scale_note,
                unit_confidence=stall.hold_current.unit_confidence,
                ack_support=AckSupport.SUPPORTED,
            ),
        ),
        telemetry=(
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
                    "configuration response, not measured joint speed; the title "
                    "states 0.732 rpm/count while the formula states raw * 180 / "
                    "255 rpm"
                ),
                unit_confidence=Confidence.CONFLICTING,
                recommended_hz=30,
                stale_after_s=0.1,
                estimated_transaction_s=0.005,
                device_measured=False,
            ),
            telemetry(
                TelemetrySemantic.CONFIGURED_ACCELERATIONS,
                _JOINT_NAMES,
                "u8",
                shape=(6,),
                raw_range=_ACCELERATION_RANGE,
                raw_unit="count",
                raw_scale_note=(
                    "configuration response, not measured acceleration; raw 0 is "
                    "a special maximum encoding and the documented physical scales "
                    "conflict"
                ),
                unit_confidence=Confidence.CONFLICTING,
                recommended_hz=10,
                stale_after_s=0.3,
                estimated_transaction_s=0.005,
                device_measured=False,
            ),
            telemetry(
                TelemetrySemantic.CURRENT_LIMITS,
                _JOINT_NAMES,
                "u8",
                shape=(6,),
                raw_range=U8_RANGE,
                raw_unit="count",
                raw_scale_note=(
                    "current-limit configuration response; 6.5 mA/count in the "
                    "title conflicts with the raw * 1000 / 255 mA formula"
                ),
                unit_confidence=Confidence.CONFLICTING,
                recommended_hz=30,
                stale_after_s=0.1,
                estimated_transaction_s=0.005,
                device_measured=False,
            ),
            telemetry(
                TelemetrySemantic.FORCE,
                _FINGERS,
                "u8",
                shape=(5, 10, 4),
                raw_range=U8_RANGE,
                raw_unit="count",
                raw_scale_note=(
                    "raw tactile normal-force sensor count; physical calibration "
                    "is unknown; SDK assembles the five-finger 10 by 4 layout, and "
                    "firmware that does not answer the query is downgraded at "
                    "runtime"
                ),
                unit_confidence=Confidence.UNKNOWN,
                recommended_hz=30,
                stale_after_s=0.1,
                estimated_transaction_s=0.012,
                payload_projection=SampleProjection.TACTILE_MATRIX,
            ),
            telemetry(
                TelemetrySemantic.TEMPERATURES,
                _JOINT_NAMES,
                "u8",
                shape=(6,),
                raw_range=U8_RANGE,
                raw_unit="degree_celsius",
                raw_scale_note="protocol byte is expressed as 1 degree Celsius/count",
                unit_confidence=Confidence.DOCUMENTED,
                recommended_hz=1,
                stale_after_s=3,
                estimated_transaction_s=0.005,
            ),
            telemetry(
                TelemetrySemantic.FAULTS,
                _JOINT_NAMES,
                "u8_bitmask",
                shape=(6,),
                raw_range=U8_RANGE,
                raw_unit=None,
                raw_scale_note=(
                    "bit5 load overload; bit3 over-current; bit2 over-temperature; "
                    "bit1 magnetic-encoder fault; bit0 over/under-voltage; bits "
                    "7, 6, and 4 reserved"
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
                    raw_range=field.raw_range,
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
        ),
        stall_protection=stall,
        transport=transport(TransportKind.CAN),
        operations=OperationCapabilities(
            save_parameters=True,
            read_stall_protection=True,
            write_stall_protection=True,
            acknowledged_operations=("save_parameters",),
        ),
    )


__all__ = ["declared_capabilities"]
