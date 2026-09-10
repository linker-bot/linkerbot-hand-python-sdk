"""Declared side-independent L30 capability profile."""

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

_JOINT_SPECS = (
    ("thumb_root", 0, 900, JointMotionType.FLEXION, "flexed", "extended"),
    ("thumb_tip", 0, 1200, JointMotionType.FLEXION, "flexed", "extended"),
    (
        "thumb_abd",
        0,
        900,
        JointMotionType.ABDUCTION,
        "away_from_thumb_web",
        "toward_thumb_web",
    ),
    (
        "thumb_rotation",
        0,
        800,
        JointMotionType.ROTATION,
        "away_from_palm",
        "toward_palm",
    ),
    (
        "ring_abd",
        -200,
        200,
        JointMotionType.ABDUCTION,
        "toward_little_finger",
        "toward_index_finger",
    ),
    ("ring_tip", 0, 1500, JointMotionType.FLEXION, "flexed", "extended"),
    ("ring_root", 0, 1600, JointMotionType.FLEXION, "flexed", "extended"),
    ("middle_root", 0, 1600, JointMotionType.FLEXION, "flexed", "extended"),
    ("middle_tip", 0, 1500, JointMotionType.FLEXION, "flexed", "extended"),
    ("pinky_root", 0, 1600, JointMotionType.FLEXION, "flexed", "extended"),
    ("pinky_tip", 0, 1500, JointMotionType.FLEXION, "flexed", "extended"),
    (
        "pinky_abd",
        -200,
        200,
        JointMotionType.ABDUCTION,
        "toward_little_finger",
        "toward_index_finger",
    ),
    (
        "middle_abd",
        -200,
        200,
        JointMotionType.ABDUCTION,
        "toward_little_finger",
        "toward_index_finger",
    ),
    (
        "index_abd",
        -200,
        200,
        JointMotionType.ABDUCTION,
        "toward_little_finger",
        "toward_index_finger",
    ),
    ("index_root", 0, 1600, JointMotionType.FLEXION, "flexed", "extended"),
    ("index_tip", 0, 1500, JointMotionType.FLEXION, "flexed", "extended"),
    ("wrist", -1000, 1000, JointMotionType.WRIST, "forward", "backward"),
)
_FINGERS = ("thumb", "index", "middle", "ring", "pinky")
_I16_RANGE = NumericRange(-32768, 32767, integer_only=True, step=1)


def declared_capabilities(side: HandSide) -> DeviceCapabilities:
    """Return immutable, unverified L30 metadata.

    Args:
        side: Must be :attr:`HandSide.NOT_APPLICABLE`; L30 has one mapping.
    """

    joints = tuple(
        joint(
            index,
            name,
            motion_type,
            NumericRange(minimum, maximum, integer_only=True, step=1),
            raw_at_zero=(
                maximum if motion_type is JointMotionType.FLEXION else minimum
            ),
            raw_at_hundred=(
                minimum if motion_type is JointMotionType.FLEXION else maximum
            ),
            zero_label=zero_label,
            hundred_label=hundred_label,
        )
        for index, (
            name,
            minimum,
            maximum,
            motion_type,
            zero_label,
            hundred_label,
        ) in enumerate(_JOINT_SPECS)
    )
    names = tuple(value.name for value in joints)
    return DeviceCapabilities(
        identity=identity(
            "L30",
            side,
            supported_fields=(
                "serial_number",
                "pcb_version",
                "firmware_version",
                "mechanical_version",
                "product_code",
                "sensor_variant",
            ),
        ),
        joints=joints,
        commands=(
            angle_command(joints, "i16_be", returns_current_state=False),
            command(
                CommandSemantic.SPEEDS,
                names,
                NumericRange(1, 250, integer_only=True, step=1),
                "u16_be",
                raw_unit="count",
                raw_scale_note="0.732 rpm/count is documented but awaits hardware confirmation",
                unit_confidence=Confidence.DOCUMENTED,
            ),
            command(
                CommandSemantic.CURRENT_LIMITS,
                names,
                NumericRange(60, 800, integer_only=True, step=1),
                "u16_be",
                raw_unit="count",
                raw_scale_note="6.5 mA/count is documented but not converted by the SDK",
                unit_confidence=Confidence.DOCUMENTED,
            ),
        ),
        telemetry=(
            angle_telemetry(
                joints,
                "i16_be",
                recommended_hz=60,
                estimated_transaction_s=0.004,
            ),
            telemetry(
                TelemetrySemantic.CONFIGURED_SPEEDS,
                names,
                "u16_be",
                shape=(17,),
                raw_range=NumericRange(1, 250, integer_only=True, step=1),
                raw_unit="count",
                raw_scale_note="0.732 rpm/count is documented but awaits hardware confirmation",
                unit_confidence=Confidence.DOCUMENTED,
                recommended_hz=0,
                stale_after_s=1,
                estimated_transaction_s=0,
                read_mode=ReadMode.COMMAND_CACHE,
                device_measured=False,
                device_queried=False,
            ),
            telemetry(
                TelemetrySemantic.MEASURED_SPEEDS,
                names,
                "i16_be",
                shape=(17,),
                raw_range=_I16_RANGE,
                raw_unit="count",
                raw_scale_note="0.732 rpm/count is documented but awaits hardware confirmation",
                unit_confidence=Confidence.DOCUMENTED,
                recommended_hz=30,
                stale_after_s=0.1,
                estimated_transaction_s=0.004,
            ),
            telemetry(
                TelemetrySemantic.CURRENTS,
                names,
                "i16_be",
                shape=(17,),
                raw_range=_I16_RANGE,
                raw_unit="count",
                raw_scale_note="6.5 mA/count is documented but not converted by the SDK",
                unit_confidence=Confidence.DOCUMENTED,
                recommended_hz=10,
                stale_after_s=0.3,
                estimated_transaction_s=0.004,
            ),
            telemetry(
                TelemetrySemantic.CURRENT_LIMITS,
                names,
                "u16_be",
                shape=(17,),
                raw_range=NumericRange(60, 800, integer_only=True, step=1),
                raw_unit="count",
                raw_scale_note="6.5 mA/count is documented but not converted by the SDK",
                unit_confidence=Confidence.DOCUMENTED,
                recommended_hz=0,
                stale_after_s=1,
                estimated_transaction_s=0,
                read_mode=ReadMode.COMMAND_CACHE,
                device_measured=False,
                device_queried=False,
            ),
            telemetry(
                TelemetrySemantic.FORCE,
                _FINGERS,
                "u8",
                shape=(5, 12, 6),
                raw_range=U8_RANGE,
                raw_unit="count",
                unit_confidence=Confidence.DOCUMENTED,
                recommended_hz=30,
                stale_after_s=0.1,
                estimated_transaction_s=0.02,
                payload_projection=SampleProjection.TACTILE_MATRIX,
            ),
            telemetry(
                TelemetrySemantic.TEMPERATURES,
                names,
                "u8",
                shape=(17,),
                raw_range=U8_RANGE,
                raw_unit="degree_celsius",
                unit_confidence=Confidence.DOCUMENTED,
                recommended_hz=1,
                stale_after_s=3,
                estimated_transaction_s=0.004,
            ),
            telemetry(
                TelemetrySemantic.FAULTS,
                names,
                "u8_bitmask",
                shape=(17,),
                raw_range=U8_RANGE,
                raw_unit=None,
                unit_confidence=Confidence.DOCUMENTED,
                recommended_hz=5,
                stale_after_s=0.5,
                estimated_transaction_s=0.004,
            ),
        ),
        stall_protection=None,
        transport=transport(TransportKind.CAN_FD),
        operations=OperationCapabilities(
            enable=True,
            disable=True,
            calibrate=True,
            acknowledged_operations=("enable", "disable", "calibrate"),
        ),
    )


__all__ = ["declared_capabilities"]
