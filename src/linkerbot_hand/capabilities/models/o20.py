"""Declared O20 capability profile with sixteen installed joints."""

from ...api.enums import Confidence, HandSide, JointMotionType, TransportKind
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
    ("thumb_mcp", 0, 120, JointMotionType.FLEXION, "flexed", "extended", ()),
    ("thumb_ip", 0, 150, JointMotionType.FLEXION, "flexed", "extended", ()),
    (
        "thumb_roll",
        0,
        180,
        JointMotionType.ROLL,
        "parallel_to_palm",
        "perpendicular_to_palm",
        ("thumb_rotation",),
    ),
    (
        "thumb_rotation",
        0,
        130,
        JointMotionType.ROTATION,
        "away_from_index_finger",
        "toward_index_finger",
        ("thumb_roll",),
    ),
    (
        "index_abd",
        -30,
        30,
        JointMotionType.ABDUCTION,
        "away_from_thumb",
        "toward_thumb",
        (),
    ),
    ("index_mcp", 0, 180, JointMotionType.FLEXION, "flexed", "extended", ()),
    ("index_pip", 0, 180, JointMotionType.FLEXION, "flexed", "extended", ()),
    (
        "middle_abd",
        -30,
        30,
        JointMotionType.ABDUCTION,
        "away_from_thumb",
        "toward_thumb",
        (),
    ),
    ("middle_mcp", 0, 180, JointMotionType.FLEXION, "flexed", "extended", ()),
    ("middle_pip", 0, 180, JointMotionType.FLEXION, "flexed", "extended", ()),
    (
        "ring_abd",
        -20,
        20,
        JointMotionType.ABDUCTION,
        "away_from_thumb",
        "toward_thumb",
        (),
    ),
    ("ring_mcp", 0, 180, JointMotionType.FLEXION, "flexed", "extended", ()),
    ("ring_pip", 0, 180, JointMotionType.FLEXION, "flexed", "extended", ()),
    (
        "pinky_abd",
        -20,
        20,
        JointMotionType.ABDUCTION,
        "away_from_thumb",
        "toward_thumb",
        (),
    ),
    ("pinky_mcp", 0, 180, JointMotionType.FLEXION, "flexed", "extended", ()),
    ("pinky_dip", 0, 180, JointMotionType.FLEXION, "flexed", "extended", ()),
)
_FINGERS = ("thumb", "index", "middle", "ring", "pinky")
_I16_RANGE = NumericRange(-32768, 32767, integer_only=True, step=1)


def declared_capabilities(side: HandSide) -> DeviceCapabilities:
    """Return immutable, unverified O20 metadata for ``side``."""

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
            coupled_with=coupled_with,
        )
        for index, (
            name,
            minimum,
            maximum,
            motion_type,
            zero_label,
            hundred_label,
            coupled_with,
        ) in enumerate(_JOINT_SPECS)
    )
    names = tuple(value.name for value in joints)
    return DeviceCapabilities(
        identity=identity(
            "O20",
            side,
            supported_fields=(
                "serial_number",
                "pcb_version",
                "firmware_version",
                "product_code",
                "side",
            ),
        ),
        joints=joints,
        commands=(
            angle_command(joints, "i16_le"),
            command(
                CommandSemantic.SPEEDS,
                names,
                NumericRange(0, 100, integer_only=True, step=1),
                "i16_le",
                raw_unit="count",
                unit_confidence=Confidence.DOCUMENTED,
            ),
            command(
                CommandSemantic.EFFORT_LIMITS,
                names,
                NumericRange(0, 1000, integer_only=True, step=1),
                "u16_le",
                raw_unit=None,
                raw_scale_note=(
                    "protocol calls this target torque; its physical interpretation "
                    "requires confirmation"
                ),
                unit_confidence=Confidence.CONFLICTING,
            ),
        ),
        telemetry=(
            angle_telemetry(
                joints,
                "i16_le",
                recommended_hz=30,
                estimated_transaction_s=0.004,
            ),
            telemetry(
                TelemetrySemantic.MEASURED_SPEEDS,
                names,
                "i16_le",
                shape=(16,),
                raw_range=_I16_RANGE,
                raw_unit="count",
                raw_scale_note="documentation states 0.732 motor rpm per count",
                unit_confidence=Confidence.DOCUMENTED,
                recommended_hz=30,
                stale_after_s=0.1,
                estimated_transaction_s=0.004,
            ),
            telemetry(
                TelemetrySemantic.CONFIGURED_SPEEDS,
                names,
                "i16_le",
                shape=(16,),
                raw_range=NumericRange(0, 100, integer_only=True, step=1),
                raw_unit="count",
                unit_confidence=Confidence.DOCUMENTED,
                recommended_hz=30,
                stale_after_s=0.1,
                estimated_transaction_s=0.004,
            ),
            telemetry(
                TelemetrySemantic.EFFORT_LIMITS,
                names,
                "u16_le",
                shape=(16,),
                raw_range=NumericRange(0, 1000, integer_only=True, step=1),
                raw_unit=None,
                raw_scale_note=(
                    "protocol calls this target torque; its physical interpretation "
                    "requires confirmation"
                ),
                unit_confidence=Confidence.CONFLICTING,
                recommended_hz=10,
                stale_after_s=0.3,
                estimated_transaction_s=0.004,
            ),
            telemetry(
                TelemetrySemantic.CURRENTS,
                names,
                "i16_le",
                shape=(16,),
                raw_range=_I16_RANGE,
                raw_unit="mA",
                unit_confidence=Confidence.DOCUMENTED,
                recommended_hz=10,
                stale_after_s=0.3,
                estimated_transaction_s=0.004,
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
            ),
            telemetry(
                TelemetrySemantic.TEMPERATURES,
                names,
                "u8",
                shape=(16,),
                raw_range=U8_RANGE,
                raw_unit="count",
                unit_confidence=Confidence.UNKNOWN,
                recommended_hz=1,
                stale_after_s=3,
                estimated_transaction_s=0.004,
            ),
            telemetry(
                TelemetrySemantic.FAULTS,
                names,
                "u8_enum",
                shape=(16,),
                raw_range=NumericRange(0, 4, integer_only=True, step=1),
                raw_unit=None,
                unit_confidence=Confidence.DOCUMENTED,
                recommended_hz=5,
                stale_after_s=0.5,
                estimated_transaction_s=0.004,
            ),
        ),
        stall_protection=None,
        transport=transport(TransportKind.CAN_FD),
        operations=OperationCapabilities(clear_faults=True),
    )


__all__ = ["declared_capabilities"]
