"""Declared L25 capability profile."""

from ...api.enums import Confidence, HandSide, JointMotionType, TransportKind
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
    (
        "thumb_rotation",
        JointMotionType.ROTATION,
        "toward_index_finger",
        "away_from_index_finger",
    ),
    (
        "thumb_roll",
        JointMotionType.ROLL,
        "perpendicular_to_palm",
        "parallel_to_palm",
    ),
    ("thumb_root", JointMotionType.FLEXION, "flexed", "extended"),
    ("thumb_tip", JointMotionType.FLEXION, "flexed", "extended"),
    (
        "index_abd",
        JointMotionType.ABDUCTION,
        "toward_little_finger",
        "toward_thumb",
    ),
    ("index_root", JointMotionType.FLEXION, "flexed", "extended"),
    ("index_tip", JointMotionType.FLEXION, "flexed", "extended"),
    (
        "middle_abd",
        JointMotionType.ABDUCTION,
        "toward_little_finger",
        "toward_thumb",
    ),
    ("middle_root", JointMotionType.FLEXION, "flexed", "extended"),
    ("middle_tip", JointMotionType.FLEXION, "flexed", "extended"),
    (
        "ring_abd",
        JointMotionType.ABDUCTION,
        "toward_little_finger",
        "toward_thumb",
    ),
    ("ring_root", JointMotionType.FLEXION, "flexed", "extended"),
    ("ring_tip", JointMotionType.FLEXION, "flexed", "extended"),
    (
        "pinky_abd",
        JointMotionType.ABDUCTION,
        "toward_little_finger",
        "toward_thumb",
    ),
    ("pinky_root", JointMotionType.FLEXION, "flexed", "extended"),
    ("pinky_tip", JointMotionType.FLEXION, "flexed", "extended"),
)
_FINGERS = ("thumb", "index", "middle", "ring", "pinky")


def declared_capabilities(side: HandSide) -> DeviceCapabilities:
    """Return immutable, unverified L25 metadata for ``side``."""

    joints = tuple(
        joint(
            index,
            name,
            motion_type,
            U8_RANGE,
            raw_at_zero=0,
            raw_at_hundred=255,
            zero_label=zero_label,
            hundred_label=hundred_label,
        )
        for index, (name, motion_type, zero_label, hundred_label) in enumerate(
            _JOINT_SPECS
        )
    )
    names = tuple(value.name for value in joints)
    return DeviceCapabilities(
        identity=identity(
            "L25",
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
                names,
                U8_RANGE,
                "u8",
                raw_unit="count",
                raw_scale_note="per-axis physical ranges are not runtime conversions",
                unit_confidence=Confidence.UNKNOWN,
            ),
            command(
                CommandSemantic.EFFORT_LIMITS,
                names,
                U8_RANGE,
                "u8",
                raw_unit=None,
                raw_scale_note="configured field remains dimensionless in the API",
                unit_confidence=Confidence.UNKNOWN,
            ),
        ),
        telemetry=(
            angle_telemetry(
                joints,
                "u8",
                recommended_hz=60,
                estimated_transaction_s=0.008,
            ),
            telemetry(
                TelemetrySemantic.CONFIGURED_SPEEDS,
                names,
                "u8",
                shape=(16,),
                raw_range=U8_RANGE,
                raw_unit="count",
                unit_confidence=Confidence.UNKNOWN,
                recommended_hz=30,
                stale_after_s=0.1,
                estimated_transaction_s=0.008,
                device_measured=False,
            ),
            telemetry(
                TelemetrySemantic.EFFORT_LIMITS,
                names,
                "u8",
                shape=(16,),
                raw_range=U8_RANGE,
                raw_unit=None,
                unit_confidence=Confidence.UNKNOWN,
                recommended_hz=30,
                stale_after_s=0.1,
                estimated_transaction_s=0.008,
                device_measured=False,
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
                raw_unit="degree_celsius",
                unit_confidence=Confidence.DOCUMENTED,
                recommended_hz=1,
                stale_after_s=3,
                estimated_transaction_s=0.008,
            ),
            telemetry(
                TelemetrySemantic.FAULTS,
                names,
                "u8_bitmask",
                shape=(16,),
                raw_range=U8_RANGE,
                raw_unit=None,
                unit_confidence=Confidence.DOCUMENTED,
                recommended_hz=5,
                stale_after_s=0.5,
                estimated_transaction_s=0.008,
            ),
        ),
        stall_protection=None,
        transport=transport(TransportKind.CAN),
        operations=OperationCapabilities(clear_faults=True),
    )


__all__ = ["declared_capabilities"]
